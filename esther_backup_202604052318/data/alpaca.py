"""Alpaca Broker Client.

Async client for the Alpaca Trading API — account info, quotes, option chains,
order execution (single-leg and multi-leg), positions, and portfolio history.
Drop-in alternative to TradierClient for order execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from esther.core.config import env
from esther.data.tradier import Quote, OptionQuote, OptionGreeks, OptionType, Bar

logger = structlog.get_logger(__name__)


# ── Pydantic Models ─────────────────────────────────────────────


class AlpacaAccount(BaseModel):
    """Alpaca account summary."""

    id: str = ""
    status: str = ""
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    portfolio_value: float = 0.0
    long_market_value: float = 0.0
    short_market_value: float = 0.0
    daytrade_count: int = 0
    daytrading_buying_power: float = 0.0


class AlpacaQuote(BaseModel):
    """Quote for a stock or option."""

    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0


class AlpacaOptionContract(BaseModel):
    """Option contract from the chain."""

    id: str = ""
    symbol: str = ""
    name: str = ""
    underlying_symbol: str = ""
    type: str = ""  # call or put
    strike_price: float = 0.0
    expiration_date: str = ""
    status: str = ""
    tradable: bool = False
    close_price: float | None = None
    open_interest: int | None = None


class AlpacaPosition(BaseModel):
    """Open position."""

    asset_id: str = ""
    symbol: str = ""
    qty: float = 0.0
    side: str = ""
    market_value: float = 0.0
    cost_basis: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_plpc: float = 0.0
    current_price: float = 0.0
    avg_entry_price: float = 0.0
    asset_class: str = ""


class AlpacaOrder(BaseModel):
    """Order details."""

    id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: str = ""
    type: str = ""
    status: str = ""
    filled_qty: str = ""
    filled_avg_price: str | None = None
    limit_price: str | None = None
    order_class: str = ""
    created_at: str = ""
    legs: list[dict[str, Any]] = Field(default_factory=list)


class AlpacaPortfolioHistory(BaseModel):
    """Portfolio P&L history."""

    timestamp: list[int] = Field(default_factory=list)
    equity: list[float] = Field(default_factory=list)
    profit_loss: list[float] = Field(default_factory=list)
    profit_loss_pct: list[float] = Field(default_factory=list)
    base_value: float = 0.0
    timeframe: str = ""


# ── Client ───────────────────────────────────────────────────────


class AlpacaClient:
    """Async Alpaca Trading API client with rate limiting and retries.

    Usage:
        async with AlpacaClient() as client:
            acct = await client.get_account()
            positions = await client.get_positions()

    Or without context manager (creates/closes client per call):
        client = AlpacaClient()
        acct = await client.get_account()
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    RATE_LIMIT_DELAY = 0.1

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        broker: str | None = None,
    ):
        _env = env()
        _broker = broker or _env.alpaca_broker  # "paper1" or "paper2"

        if api_key and api_secret:
            self.api_key = api_key
            self.api_secret = api_secret
        elif _broker == "paper1":
            self.api_key = _env.alpaca_paper1_key
            self.api_secret = _env.alpaca_paper1_secret
            self.base_url = base_url or _env.alpaca_paper1_url
        else:  # paper2 default
            self.api_key = _env.alpaca_paper2_key
            self.api_secret = _env.alpaca_paper2_secret
            self.base_url = base_url or _env.alpaca_paper2_url

        if base_url:
            self.base_url = base_url
        elif not hasattr(self, "base_url"):
            self.base_url = "https://paper-api.alpaca.markets/v2"

        # Data API base URL for market data (snapshots, options)
        self.data_url = "https://data.alpaca.markets/v2"
        self.options_data_url = "https://data.alpaca.markets/v1beta1"

        self._client: httpx.AsyncClient | None = None
        self._owns_client = False
        self._last_request_time: float = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def __aenter__(self) -> AlpacaClient:
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(30.0),
        )
        self._owns_client = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(30.0),
            )
            self._owns_client = True
        return self._client

    async def _rate_limit(self) -> None:
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < self.RATE_LIMIT_DELAY:
                await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Make an API request with retries and rate limiting."""
        client = await self._ensure_client()
        delay = self.RETRY_DELAY

        for attempt in range(self.MAX_RETRIES):
            await self._rate_limit()

            try:
                if method == "GET":
                    resp = await client.get(url, params=params)
                elif method == "POST":
                    resp = await client.post(url, json=json_data)
                elif method == "DELETE":
                    resp = await client.delete(url, params=params)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    logger.warning("alpaca_rate_limited", retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(retry_after)
                    delay *= 2
                    continue

                if resp.status_code >= 500:
                    logger.warning("alpaca_server_error", status=resp.status_code, attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                resp.raise_for_status()

                # DELETE /positions can return 204 with no body
                if resp.status_code == 204:
                    return {}

                return resp.json()

            except httpx.TimeoutException:
                logger.warning("alpaca_timeout", attempt=attempt)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

        raise RuntimeError(f"Max retries ({self.MAX_RETRIES}) exceeded for {url}")

    # ── Account ──────────────────────────────────────────────────

    async def get_account(self) -> AlpacaAccount:
        """Get account balance, buying power, equity.

        GET /v2/account
        """
        data = await self._request("GET", f"{self.base_url}/account")
        assert isinstance(data, dict)

        return AlpacaAccount(
            id=data.get("id", ""),
            status=data.get("status", ""),
            equity=float(data.get("equity", 0)),
            cash=float(data.get("cash", 0)),
            buying_power=float(data.get("buying_power", 0)),
            portfolio_value=float(data.get("portfolio_value", 0)),
            long_market_value=float(data.get("long_market_value", 0)),
            short_market_value=float(data.get("short_market_value", 0)),
            daytrade_count=int(data.get("daytrade_count", 0)),
            daytrading_buying_power=float(data.get("daytrading_buying_power", 0)),
        )

    # ── Quotes ───────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> AlpacaQuote:
        """Get last price, bid, ask for a stock.

        Uses Alpaca Data API: GET /v2/stocks/{symbol}/snapshot
        For options, uses /v1beta1/options/snapshots/{symbol}
        """
        # Detect if this is an option symbol (OCC format like SPY260327C00640000)
        if len(symbol) > 10 and any(c in symbol for c in ("C", "P")):
            return await self._get_option_quote(symbol)

        data = await self._request(
            "GET", f"{self.data_url}/stocks/{symbol}/snapshot", params={"feed": "iex"}
        )
        assert isinstance(data, dict)

        latest_trade = data.get("latestTrade", {})
        latest_quote = data.get("latestQuote", {})
        daily_bar = data.get("dailyBar", {})

        return AlpacaQuote(
            symbol=symbol,
            last=float(latest_trade.get("p", 0)),
            bid=float(latest_quote.get("bp", 0)),
            ask=float(latest_quote.get("ap", 0)),
            high=float(daily_bar.get("h", 0)),
            low=float(daily_bar.get("l", 0)),
            volume=int(daily_bar.get("v", 0)),
        )

    async def _get_option_quote(self, symbol: str) -> AlpacaQuote:
        """Get quote for an option symbol via snapshots endpoint."""
        data = await self._request(
            "GET",
            f"{self.options_data_url}/options/snapshots/{symbol}",
        )
        assert isinstance(data, dict)

        latest_trade = data.get("latestTrade", {})
        latest_quote = data.get("latestQuote", {})

        return AlpacaQuote(
            symbol=symbol,
            last=float(latest_trade.get("p", 0)),
            bid=float(latest_quote.get("bp", 0)),
            ask=float(latest_quote.get("ap", 0)),
        )

    # ── Option Chain ─────────────────────────────────────────────

    async def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        option_type: str | None = None,
        limit: int = 1000,
    ) -> list[AlpacaOptionContract]:
        """Fetch option contracts for a symbol and expiration.

        GET /v2/options/contracts?underlying_symbol={symbol}&expiration_date={expiration}
        """
        params: dict[str, Any] = {
            "underlying_symbols": symbol,
            "expiration_date": expiration,
            "limit": limit,
        }
        if option_type:
            params["type"] = option_type

        data = await self._request(
            "GET", f"{self.base_url}/options/contracts", params=params
        )
        assert isinstance(data, dict)

        contracts_data = data.get("option_contracts", [])
        if not contracts_data:
            contracts_data = data.get("options", [])

        results = []
        for c in contracts_data:
            results.append(AlpacaOptionContract(
                id=c.get("id", ""),
                symbol=c.get("symbol", ""),
                name=c.get("name", ""),
                underlying_symbol=c.get("underlying_symbol", ""),
                type=c.get("type", ""),
                strike_price=float(c.get("strike_price", 0)),
                expiration_date=c.get("expiration_date", ""),
                status=c.get("status", ""),
                tradable=c.get("tradable", False),
                close_price=float(c["close_price"]) if c.get("close_price") else None,
                open_interest=int(c["open_interest"]) if c.get("open_interest") else None,
            ))

        logger.info(
            "alpaca_option_chain_fetched",
            symbol=symbol,
            expiration=expiration,
            contracts=len(results),
        )
        return results

    # ── Order Execution ──────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str = "buy",
        qty: int = 1,
        order_type: str = "market",
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Place a single-leg order (equity or option).

        POST /v2/orders

        Args:
            symbol: Ticker or OCC option symbol (e.g., "SPY" or "SPY260327C00640000").
            side: "buy" or "sell".
            qty: Number of shares/contracts.
            order_type: "market", "limit", "stop", "stop_limit".
            limit_price: Required for limit orders.
            time_in_force: "day", "gtc", "ioc", "fok".

        Returns:
            AlpacaOrder with order ID and status.
        """
        order_data: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "type": order_type,
            "time_in_force": time_in_force,
        }

        if limit_price is not None:
            order_data["limit_price"] = str(limit_price)

        data = await self._request("POST", f"{self.base_url}/orders", json_data=order_data)
        assert isinstance(data, dict)

        order = self._parse_order(data)
        logger.info(
            "alpaca_order_placed",
            symbol=symbol,
            side=side,
            qty=qty,
            order_id=order.id,
            status=order.status,
        )
        return order

    async def place_multileg_order(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        order_type: str = "limit",
        net_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Place a multi-leg option order (spreads, iron condors).

        POST /v2/orders with order_class="mleg"

        Args:
            symbol: Underlying symbol (e.g., "SPY").
            legs: List of leg dicts:
                - symbol: OCC option symbol
                - side: "buy" or "sell"
                - qty: number of contracts
                - ratio_qty: (optional) for ratio spreads
            order_type: "market", "limit", "debit", "credit".
            net_price: Net credit/debit price (positive = debit, negative = credit).
                For limit orders, the net price of the spread.
            time_in_force: "day", "gtc".

        Returns:
            AlpacaOrder with order ID and status.
        """
        order_legs = []
        for leg in legs:
            order_leg: dict[str, Any] = {
                "symbol": leg["symbol"],
                "side": leg["side"],
                "qty": str(leg.get("qty", leg.get("quantity", 1))),
            }
            if "ratio_qty" in leg:
                order_leg["ratio_qty"] = str(leg["ratio_qty"])
            order_legs.append(order_leg)

        order_data: dict[str, Any] = {
            "symbol": symbol,
            "order_class": "mleg",
            "type": order_type,
            "time_in_force": time_in_force,
            "legs": order_legs,
        }

        if net_price is not None:
            order_data["limit_price"] = str(abs(net_price))

        data = await self._request("POST", f"{self.base_url}/orders", json_data=order_data)
        assert isinstance(data, dict)

        order = self._parse_order(data)
        logger.info(
            "alpaca_multileg_order_placed",
            symbol=symbol,
            legs=len(legs),
            order_id=order.id,
            status=order.status,
        )
        return order

    async def submit_multi_leg_order(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        order_type: str = "limit",
        net_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Submit a multi-leg order with fallback to individual legs.

        Tries the native mleg endpoint first. If it returns a 422 (common on
        paper accounts that don't support multi-leg), falls back to submitting
        each leg as a separate order.

        Args:
            symbol: Underlying symbol (e.g., "SPY").
            legs: List of leg dicts with keys: symbol, side, qty.
            order_type: "market", "limit", "debit", "credit".
            net_price: Net credit/debit price for limit orders.
            time_in_force: "day" or "gtc".

        Returns:
            AlpacaOrder from the multi-leg attempt, or the last individual
            leg order if fallback was used.
        """
        try:
            return await self.place_multileg_order(
                symbol=symbol,
                legs=legs,
                order_type=order_type,
                net_price=net_price,
                time_in_force=time_in_force,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                logger.warning(
                    "mleg_not_supported_falling_back",
                    symbol=symbol,
                    legs=len(legs),
                    error=str(e),
                )
                return await self._submit_legs_individually(
                    legs=legs,
                    time_in_force=time_in_force,
                )
            raise
        except Exception as e:
            # Any other error on mleg — try individual legs as last resort
            logger.warning(
                "mleg_failed_falling_back",
                symbol=symbol,
                error=str(e),
            )
            return await self._submit_legs_individually(
                legs=legs,
                time_in_force=time_in_force,
            )

    async def _submit_legs_individually(
        self,
        legs: list[dict[str, Any]],
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Fallback: submit each leg of a spread as a separate market order.

        NOTE: This is a workaround for paper accounts that don't support
        order_class=mleg. Individual legs may not fill at the same time,
        creating temporary naked risk.
        """
        last_order: AlpacaOrder | None = None
        for leg in legs:
            order = await self.place_order(
                symbol=leg["symbol"],
                side=leg["side"],
                qty=int(leg.get("qty", leg.get("quantity", 1))),
                order_type="market",
                time_in_force=time_in_force,
            )
            last_order = order
            logger.info(
                "individual_leg_submitted",
                symbol=leg["symbol"],
                side=leg["side"],
                order_id=order.id,
                note="WORKAROUND: mleg not supported, legs submitted individually",
            )
        assert last_order is not None
        return last_order

    # ── Positions ────────────────────────────────────────────────

    async def get_positions(self) -> list[AlpacaPosition]:
        """Get all open positions.

        GET /v2/positions
        """
        data = await self._request("GET", f"{self.base_url}/positions")
        assert isinstance(data, list)

        results = []
        for p in data:
            results.append(AlpacaPosition(
                asset_id=p.get("asset_id", ""),
                symbol=p.get("symbol", ""),
                qty=float(p.get("qty", 0)),
                side=p.get("side", ""),
                market_value=float(p.get("market_value", 0)),
                cost_basis=float(p.get("cost_basis", 0)),
                unrealized_pl=float(p.get("unrealized_pl", 0)),
                unrealized_plpc=float(p.get("unrealized_plpc", 0)),
                current_price=float(p.get("current_price", 0)),
                avg_entry_price=float(p.get("avg_entry_price", 0)),
                asset_class=p.get("asset_class", ""),
            ))

        logger.info("alpaca_positions_fetched", count=len(results))
        return results

    # ── Orders ───────────────────────────────────────────────────

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
    ) -> list[AlpacaOrder]:
        """Get order history.

        GET /v2/orders
        """
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
        }

        data = await self._request("GET", f"{self.base_url}/orders", params=params)
        assert isinstance(data, list)

        results = [self._parse_order(o) for o in data]
        logger.info("alpaca_orders_fetched", count=len(results), status=status)
        return results

    # ── Close Position ───────────────────────────────────────────

    async def close_position(self, symbol_or_id: str) -> dict[str, Any]:
        """Close a position by symbol or asset ID.

        DELETE /v2/positions/{symbol_or_asset_id}
        """
        data = await self._request(
            "DELETE", f"{self.base_url}/positions/{symbol_or_id}"
        )
        assert isinstance(data, dict)
        logger.info("alpaca_position_closed", symbol_or_id=symbol_or_id)
        return data

    # ── Portfolio History ────────────────────────────────────────

    async def get_portfolio_history(
        self,
        period: str = "1M",
        timeframe: str = "1D",
    ) -> AlpacaPortfolioHistory:
        """Get portfolio P&L history.

        GET /v2/account/portfolio/history

        Args:
            period: "1D", "1W", "1M", "3M", "1A", "all", or intraday like "1D".
            timeframe: "1Min", "5Min", "15Min", "1H", "1D".
        """
        params: dict[str, Any] = {
            "period": period,
            "timeframe": timeframe,
        }

        data = await self._request(
            "GET", f"{self.base_url}/account/portfolio/history", params=params
        )
        assert isinstance(data, dict)

        return AlpacaPortfolioHistory(
            timestamp=data.get("timestamp", []),
            equity=data.get("equity", []),
            profit_loss=data.get("profit_loss", []),
            profit_loss_pct=data.get("profit_loss_pct", []),
            base_value=float(data.get("base_value", 0)),
            timeframe=data.get("timeframe", ""),
        )

    # ── Tradier-Compatible Interface ────────────────────────────
    # These methods return the same data models as TradierClient so the
    # engine, black_swan, pillars, and position_manager work unchanged.

    # Symbols that are indices (not stocks) — Alpaca can't quote these directly.
    # We use UVXY as a VIX proxy (VIX ETN), or return a stub.
    _INDEX_SYMBOLS = {"VIX", "$VIX", "VIX.X", "SPX", "$SPX"}
    _VIX_PROXY = "VIXY"  # ProShares VIX Short-Term Futures ETF (quotable on Alpaca)

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch quotes for multiple symbols, returning tradier-compatible Quote objects.
        
        Handles index symbols (VIX, SPX) that aren't available on Alpaca's stock API
        by using ETF proxies or returning safe stubs.
        """
        results: list[Quote] = []
        for symbol in symbols:
            if symbol.upper() in self._INDEX_SYMBOLS:
                # VIX / SPX are indices — not quotable as stocks on Alpaca
                # Return a stub with reasonable defaults so the engine doesn't crash
                logger.debug("index_symbol_stub", symbol=symbol, note="indices not available on Alpaca stock API")
                results.append(Quote(
                    symbol=symbol,
                    last=0.0,
                    bid=0.0,
                    ask=0.0,
                    high=0.0,
                    low=0.0,
                    volume=0,
                    change=0.0,
                    change_percentage=0.0,
                ))
                continue
            try:
                aq = await self.get_quote(symbol)
                results.append(Quote(
                    symbol=aq.symbol,
                    last=aq.last,
                    bid=aq.bid,
                    ask=aq.ask,
                    high=aq.high,
                    low=aq.low,
                    volume=aq.volume,
                    change=0.0,
                    change_percentage=0.0,
                ))
            except Exception as e:
                logger.warning("quote_fetch_failed", symbol=symbol, error=str(e))
                results.append(Quote(
                    symbol=symbol,
                    last=0.0, bid=0.0, ask=0.0, high=0.0, low=0.0,
                    volume=0, change=0.0, change_percentage=0.0,
                ))
        return results

    async def get_bars(
        self,
        symbol: str,
        interval: str = "daily",
        start: "date | None" = None,
        end: "date | None" = None,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars via Alpaca Data API, returning tradier-compatible Bar objects."""
        from datetime import date as _date, datetime as _dt

        # Index symbols can't be fetched from stock bars endpoint
        # Use ETF proxies: SPX→SPY, VIX→VIXY
        _bar_proxy = {"SPX": "SPY", "$SPX": "SPY", "VIX": "VIXY", "$VIX": "VIXY", "VIX.X": "VIXY"}
        fetch_symbol = _bar_proxy.get(symbol.upper(), symbol)

        # Map tradier interval names to Alpaca timeframes
        tf_map = {
            "daily": "1Day",
            "weekly": "1Week",
            "monthly": "1Month",
            "5min": "5Min",
            "15min": "15Min",
        }
        timeframe = tf_map.get(interval, "1Day")

        params: dict[str, Any] = {
            "timeframe": timeframe,
            "limit": 1000,
            "adjustment": "raw",
            "feed": "iex",
        }
        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()

        try:
            data = await self._request(
                "GET", f"{self.data_url}/stocks/{fetch_symbol}/bars", params=params
            )
        except Exception as e:
            logger.warning("bars_fetch_failed", symbol=symbol, fetch_symbol=fetch_symbol, error=str(e))
            return []
        assert isinstance(data, dict)

        bars_data = data.get("bars", [])
        results: list[Bar] = []
        for b in bars_data:
            ts = b.get("t", "")
            try:
                timestamp = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                timestamp = _dt.now()
            results.append(Bar(
                timestamp=timestamp,
                open=float(b.get("o", 0)),
                high=float(b.get("h", 0)),
                low=float(b.get("l", 0)),
                close=float(b.get("c", 0)),
                volume=int(b.get("v", 0)),
            ))

        logger.info("alpaca_bars_fetched", symbol=symbol, interval=interval, count=len(results))
        return results

    async def get_option_expirations(self, symbol: str) -> list[str]:
        """Get available option expiration dates for a symbol.

        Uses the Alpaca options contracts endpoint to discover expirations.
        """
        params: dict[str, Any] = {
            "underlying_symbols": symbol,
            "limit": 1000,
        }
        data = await self._request(
            "GET", f"{self.base_url}/options/contracts", params=params
        )
        assert isinstance(data, dict)

        contracts = data.get("option_contracts", [])
        expirations = sorted({c.get("expiration_date", "") for c in contracts if c.get("expiration_date")})
        return expirations

    async def get_option_chain_compat(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True,
    ) -> list[OptionQuote]:
        """Fetch option chain returning tradier-compatible OptionQuote objects.

        This fetches contracts from the trading API and snapshots from the data API
        to build full OptionQuote objects with greeks.
        """
        # Step 1: Get contracts
        contracts = await self.get_option_chain(symbol, expiration)
        if not contracts:
            return []

        # Step 2: Get snapshots for pricing data
        occ_symbols = [c.symbol for c in contracts if c.symbol]
        snapshots = await self._get_option_snapshots(occ_symbols)

        results: list[OptionQuote] = []
        for c in contracts:
            snap = snapshots.get(c.symbol, {})
            latest_quote = snap.get("latestQuote", {})
            greeks_data = snap.get("greeks", {})

            bid = float(latest_quote.get("bp", 0))
            ask = float(latest_quote.get("ap", 0))

            greeks_obj = None
            if greeks and greeks_data:
                greeks_obj = OptionGreeks(
                    delta=float(greeks_data.get("delta", 0)),
                    gamma=float(greeks_data.get("gamma", 0)),
                    theta=float(greeks_data.get("theta", 0)),
                    vega=float(greeks_data.get("vega", 0)),
                    rho=float(greeks_data.get("rho", 0)),
                    smv_vol=float(greeks_data.get("mid_iv", 0)),
                )

            opt_type = OptionType.CALL if c.type == "call" else OptionType.PUT

            results.append(OptionQuote(
                symbol=c.symbol,
                option_type=opt_type,
                strike=c.strike_price,
                expiration=c.expiration_date,
                bid=bid,
                ask=ask,
                mid=round((bid + ask) / 2, 2) if (bid + ask) > 0 else 0.0,
                last=float(snap.get("latestTrade", {}).get("p", 0)),
                volume=int(latest_quote.get("s", 0)),
                open_interest=c.open_interest or 0,
                greeks=greeks_obj,
            ))

        logger.info("alpaca_option_chain_compat", symbol=symbol, expiration=expiration, contracts=len(results))
        return results

    async def _get_option_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch option snapshots for a batch of OCC symbols."""
        if not symbols:
            return {}

        # Alpaca allows batching snapshots — fetch in chunks of 100
        all_snapshots: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            try:
                data = await self._request(
                    "GET",
                    f"{self.options_data_url}/options/snapshots",
                    params={"symbols": ",".join(chunk), "feed": "indicative"},
                )
                assert isinstance(data, dict)
                snapshots = data.get("snapshots", {})
                all_snapshots.update(snapshots)
            except Exception as e:
                logger.warning("option_snapshots_failed", error=str(e), chunk_size=len(chunk))

        return all_snapshots

    async def get_account_balance(self) -> dict[str, Any]:
        """Get account balance in tradier-compatible dict format."""
        acct = await self.get_account()
        return {
            "total_equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
            "portfolio_value": acct.portfolio_value,
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _parse_order(self, data: dict[str, Any]) -> AlpacaOrder:
        """Parse an order response dict into AlpacaOrder."""
        return AlpacaOrder(
            id=data.get("id", ""),
            client_order_id=data.get("client_order_id", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            qty=data.get("qty", ""),
            type=data.get("type", ""),
            status=data.get("status", ""),
            filled_qty=data.get("filled_qty", ""),
            filled_avg_price=data.get("filled_avg_price"),
            limit_price=data.get("limit_price"),
            order_class=data.get("order_class", ""),
            created_at=data.get("created_at", ""),
            legs=data.get("legs") or [],
        )
