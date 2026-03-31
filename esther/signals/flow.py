"""Order Flow Data Integration — Institutional Flow Analysis.

Tracks institutional order flow to detect where big money is positioning.
Flow data is the single most important signal (weight: 0.25) because
institutions move markets — retail follows.

Data providers (priority order):
    1. Unusual Whales API (PRIMARY) — flow alerts, net premium ticks,
       dark pool, max pain, greek exposure
    2. Tradier time & sales (FALLBACK) — option chains with volume
    3. Manual CSV import — for backtesting

Key concepts:
    - FlowEntry: Individual option trade with premium, side, etc.
    - FlowBubble: Cluster of big trades at a specific strike
    - FlowSummary: Aggregated analysis of flow for a symbol
    - Flow Bias: -100 to +100 score based on put/call premium ratio
    - DarkPoolEntry: Dark pool print with size, price, NBBO context
    - MaxPainData: Max pain strike with surrounding levels
    - GreekExposure: Aggregate greek exposure for positioning analysis
"""

from __future__ import annotations

import csv
import time
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from esther.core.config import config, env

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Cache TTLs (seconds)
# ---------------------------------------------------------------------------
_CACHE_TTL_FLOW_ALERTS = 60        # flow alerts refresh every minute
_CACHE_TTL_OPTIONS_VOLUME = 300    # daily aggregates refresh every 5 min
_CACHE_TTL_NET_PREM_TICKS = 60    # intraday ticks refresh every minute
_CACHE_TTL_DARK_POOL = 120         # dark pool refresh every 2 min
_CACHE_TTL_MAX_PAIN = 600          # max pain refresh every 10 min
_CACHE_TTL_GREEK_EXPOSURE = 300    # greeks refresh every 5 min


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OptionSide(str, Enum):
    """Trade side — buy or sell at the ask/bid."""
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class FlowOptionType(str, Enum):
    CALL = "call"
    PUT = "put"


# ---------------------------------------------------------------------------
# Core Models (PRESERVED interfaces — other modules depend on these)
# ---------------------------------------------------------------------------

class FlowEntry(BaseModel):
    """A single options trade from the flow feed."""

    symbol: str
    strike: float
    expiry: str  # ISO date string
    option_type: FlowOptionType
    premium: float  # Total dollar premium (price * volume * 100)
    volume: int
    price: float  # Per-contract price
    exchange: str = ""
    side: OptionSide = OptionSide.UNKNOWN
    timestamp: datetime = Field(default_factory=datetime.now)
    open_interest: int = 0

    # --- Unusual Whales enrichment fields (optional, zero-default) ---
    ask_side_premium: float = 0.0
    bid_side_premium: float = 0.0
    sweep_volume: int = 0
    floor_volume: int = 0
    multileg_volume: int = 0
    volume_oi_ratio: float = 0.0
    iv_start: float = 0.0
    iv_end: float = 0.0
    underlying_price: float = 0.0
    alert_rule: str = ""
    has_sweep: bool = False
    has_floor: bool = False
    has_multileg: bool = False


class FlowBubble(BaseModel):
    """Cluster of big money at a specific strike/expiry.

    When multiple large trades land at the same strike, it signals
    institutional conviction at that level. Used for target setting.
    """

    symbol: str
    strike: float
    expiry: str
    total_premium: float
    trade_count: int
    net_side: OptionSide  # net direction of the cluster
    dominant_type: FlowOptionType  # calls or puts
    avg_price: float


class FlowSummary(BaseModel):
    """Aggregated flow analysis for a symbol on a given day."""

    symbol: str
    date: str
    total_call_premium: float = 0.0
    total_put_premium: float = 0.0
    net_premium: float = 0.0  # positive = call-heavy, negative = put-heavy
    call_volume: int = 0
    put_volume: int = 0
    put_call_ratio: float = 0.0
    biggest_trades: list[FlowEntry] = []
    unusual_trades: list[FlowEntry] = []
    flow_bubbles: list[FlowBubble] = []
    flow_bias_score: float = 0.0  # -100 to +100


# ---------------------------------------------------------------------------
# New Models (Unusual Whales enrichment)
# ---------------------------------------------------------------------------

class DarkPoolEntry(BaseModel):
    """A single dark pool print."""

    ticker: str
    price: float
    size: int
    volume: int = 0
    premium: float = 0.0
    executed_at: datetime | None = None
    nbbo_ask: float = 0.0
    nbbo_bid: float = 0.0
    market_center: str = ""


class MaxPainData(BaseModel):
    """Max pain data for a symbol/expiry."""

    symbol: str
    expiry: str = ""
    max_pain: float = 0.0
    close: float = 0.0
    open: float = 0.0
    next_upper_strike: float = 0.0
    next_lower_strike: float = 0.0


class GreekExposure(BaseModel):
    """Aggregate greek exposure across all strikes for a symbol."""

    symbol: str
    date: str = ""
    call_delta: float = 0.0
    put_delta: float = 0.0
    net_delta: float = 0.0
    call_gamma: float = 0.0
    put_gamma: float = 0.0
    net_gamma: float = 0.0
    call_charm: float = 0.0
    put_charm: float = 0.0
    call_vanna: float = 0.0
    put_vanna: float = 0.0


class OptionsVolume(BaseModel):
    """Daily options volume aggregates."""

    symbol: str
    date: str = ""
    call_volume: int = 0
    put_volume: int = 0
    call_premium: float = 0.0
    put_premium: float = 0.0
    net_call_premium: float = 0.0
    net_put_premium: float = 0.0
    bearish_premium: float = 0.0
    bullish_premium: float = 0.0
    put_open_interest: int = 0
    call_open_interest: int = 0
    call_volume_ask_side: int = 0
    call_volume_bid_side: int = 0
    put_volume_ask_side: int = 0
    put_volume_bid_side: int = 0


class NetPremiumTick(BaseModel):
    """Minute-by-minute net premium tick."""

    tape_time: str = ""
    call_volume: int = 0
    put_volume: int = 0
    net_call_premium: float = 0.0
    net_put_premium: float = 0.0
    net_call_volume: int = 0
    net_put_volume: int = 0
    net_delta: float = 0.0


# ---------------------------------------------------------------------------
# Timed Cache helper
# ---------------------------------------------------------------------------

class _TimedCache:
    """Simple TTL cache for API responses."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float) -> Any | None:
        if key in self._store:
            ts, val = self._store[key]
            if time.time() - ts < ttl:
                return val
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Unusual Whales Client
# ---------------------------------------------------------------------------

class UnusualWhalesClient:
    """Async HTTP client for the Unusual Whales API.

    All methods return parsed dicts/lists or raise on failure.
    Auth via ``Authorization: Bearer <key>`` header.
    """

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self._cache = _TimedCache()

    # ----- internal helpers ------------------------------------------------

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_ttl: float = 60,
    ) -> Any:
        """Issue GET request with optional caching.

        Returns parsed JSON body (usually a dict with a ``data`` key).
        """
        if cache_key:
            cached = self._cache.get(cache_key, cache_ttl)
            if cached is not None:
                return cached

        url = f"{self.BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        if cache_key:
            self._cache.set(cache_key, data)
        return data

    # ----- public endpoints -----------------------------------------------

    async def get_flow_alerts(
        self, ticker: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """GET /api/option-trades/flow-alerts — institutional flow alerts.

        This is THE key endpoint. Returns individual option trades flagged
        as unusual by Unusual Whales' detection engine.
        """
        cache_key = f"flow_alerts:{ticker}:{limit}"
        data = await self._get(
            "/api/option-trades/flow-alerts",
            params={"ticker": ticker, "limit": limit},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_FLOW_ALERTS,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_options_volume(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/options-volume — daily aggregates."""
        cache_key = f"opt_vol:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/options-volume",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_OPTIONS_VOLUME,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_net_premium_ticks(
        self, ticker: str, target_date: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/net-prem-ticks — minute-by-minute flow."""
        if target_date is None:
            target_date = date.today().isoformat()
        cache_key = f"net_prem:{ticker}:{target_date}"
        data = await self._get(
            f"/api/stock/{ticker}/net-prem-ticks",
            params={"date": target_date},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_NET_PREM_TICKS,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_option_contracts(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/option-contracts — per-contract data."""
        cache_key = f"opt_contracts:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/option-contracts",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_OPTIONS_VOLUME,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_dark_pool(self, limit: int = 10) -> list[dict[str, Any]]:
        """GET /api/darkpool/recent — recent dark pool prints."""
        cache_key = f"darkpool:{limit}"
        data = await self._get(
            "/api/darkpool/recent",
            params={"limit": limit},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_DARK_POOL,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_max_pain(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/max-pain — max pain per expiry."""
        cache_key = f"max_pain:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/max-pain",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_MAX_PAIN,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_greek_exposure(
        self, ticker: str, target_date: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/greek-exposure — aggregate greek exposure."""
        if target_date is None:
            target_date = date.today().isoformat()
        cache_key = f"greeks:{ticker}:{target_date}"
        data = await self._get(
            f"/api/stock/{ticker}/greek-exposure",
            params={"date": target_date},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_GREEK_EXPOSURE,
        )
        return data.get("data", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# FlowAnalyzer  (preserved interface + UW enrichment)
# ---------------------------------------------------------------------------

class FlowAnalyzer:
    """Analyzes options flow data from multiple providers.

    The flow analyzer fetches institutional flow data, identifies unusual
    activity (large premium trades), clusters trades into bubbles at
    key strikes, and produces a directional bias score.

    Provider priority:
        1. Unusual Whales flow alerts (rich signal: sweep, floor, side prem)
        2. Tradier option chains (fallback if UW key missing or API fails)
    """

    # Tradier endpoints (fallback)
    PROD_BASE = "https://api.tradier.com/v1"
    SANDBOX_BASE = "https://sandbox.tradier.com/v1"

    def __init__(self) -> None:
        self._cfg = config().flow
        self._env = env()

        # Unusual Whales client (primary)
        self._uw: UnusualWhalesClient | None = None
        if self._env.unusual_whales_api_key:
            self._uw = UnusualWhalesClient(self._env.unusual_whales_api_key)
            logger.info("flow_provider_init", provider="unusual_whales")
        else:
            logger.warning(
                "flow_provider_fallback",
                reason="UNUSUAL_WHALES_API_KEY not set, using Tradier",
            )

        # Tradier (fallback)
        self._base_url = (
            self.SANDBOX_BASE if self._env.tradier_sandbox else self.PROD_BASE
        )
        self._tradier_headers = {
            "Authorization": f"Bearer {self._env.tradier_api_key}",
            "Accept": "application/json",
        }

        # Entry cache (keyed by symbol:date)
        self._cache: dict[str, list[FlowEntry]] = {}

    # --- public properties ------------------------------------------------

    @property
    def uw_client(self) -> UnusualWhalesClient | None:
        """Direct access to the Unusual Whales client for advanced queries."""
        return self._uw

    # ------------------------------------------------------------------
    # get_flow  — primary entry point
    # ------------------------------------------------------------------

    async def get_flow(
        self, symbol: str, target_date: date | None = None
    ) -> list[FlowEntry]:
        """Fetch options flow for a symbol.

        Tries Unusual Whales first; falls back to Tradier on failure.

        Args:
            symbol: Underlying symbol (e.g. "SPY", "SPX").
            target_date: Date to fetch flow for. Defaults to today.

        Returns:
            List of FlowEntry trades.
        """
        if target_date is None:
            target_date = date.today()

        cache_key = f"{symbol}:{target_date.isoformat()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        entries: list[FlowEntry] = []

        # --- Primary: Unusual Whales ---
        if self._uw is not None:
            try:
                entries = await self._fetch_from_unusual_whales(symbol)
                if entries:
                    logger.info(
                        "flow_fetched",
                        provider="unusual_whales",
                        symbol=symbol,
                        date=target_date.isoformat(),
                        trade_count=len(entries),
                    )
                    self._cache[cache_key] = entries
                    return entries
                # Empty result — fall through to Tradier
                logger.warning(
                    "uw_empty_response",
                    symbol=symbol,
                    msg="No flow alerts returned, trying Tradier",
                )
            except Exception as exc:
                logger.error(
                    "uw_fetch_failed",
                    symbol=symbol,
                    error=str(exc),
                    msg="Falling back to Tradier",
                )

        # --- Fallback: Tradier ---
        try:
            entries = await self._fetch_from_tradier(symbol, target_date)
            logger.info(
                "flow_fetched",
                provider="tradier",
                symbol=symbol,
                date=target_date.isoformat(),
                trade_count=len(entries),
            )
        except Exception as exc:
            logger.error("tradier_fetch_failed", symbol=symbol, error=str(exc))

        self._cache[cache_key] = entries
        return entries

    # ------------------------------------------------------------------
    # Unusual Whales fetch
    # ------------------------------------------------------------------

    async def _fetch_from_unusual_whales(
        self, symbol: str
    ) -> list[FlowEntry]:
        """Convert UW flow alerts into FlowEntry objects."""
        assert self._uw is not None
        raw_alerts = await self._uw.get_flow_alerts(symbol, limit=100)

        entries: list[FlowEntry] = []
        for alert in raw_alerts:
            try:
                opt_type_raw = str(alert.get("type", "")).lower()
                if opt_type_raw not in ("call", "put"):
                    # Some alerts may have "C"/"P" shorthand
                    opt_type_raw = "call" if opt_type_raw.startswith("c") else "put"

                total_premium = _safe_float(alert.get("total_premium", 0))
                total_size = _safe_int(alert.get("total_size", 0))
                volume = _safe_int(alert.get("volume", 0))
                ask_prem = _safe_float(alert.get("total_ask_side_prem", 0))
                bid_prem = _safe_float(alert.get("total_bid_side_prem", 0))

                # Determine side from ask_side vs bid_side premium
                if ask_prem > bid_prem * 1.5:
                    side = OptionSide.BUY
                elif bid_prem > ask_prem * 1.5:
                    side = OptionSide.SELL
                else:
                    side = OptionSide.UNKNOWN

                # Per-contract price from premium / (size * 100)
                effective_size = total_size if total_size > 0 else volume
                price = (
                    total_premium / (effective_size * 100)
                    if effective_size > 0
                    else _safe_float(alert.get("ask", 0))
                )

                # Parse timestamp
                created = alert.get("created_at")
                ts = _parse_timestamp(created) if created else datetime.now()

                entry = FlowEntry(
                    symbol=str(alert.get("ticker", symbol)),
                    strike=_safe_float(alert.get("strike", 0)),
                    expiry=str(alert.get("expiry", "")),
                    option_type=FlowOptionType(opt_type_raw),
                    premium=round(total_premium, 2),
                    volume=volume if volume > 0 else effective_size,
                    price=round(price, 4),
                    side=side,
                    timestamp=ts,
                    open_interest=_safe_int(alert.get("open_interest", 0)),
                    # UW enrichment
                    ask_side_premium=round(ask_prem, 2),
                    bid_side_premium=round(bid_prem, 2),
                    sweep_volume=_safe_int(alert.get("sweep_volume", 0)),
                    floor_volume=_safe_int(alert.get("floor_volume", 0)),
                    volume_oi_ratio=_safe_float(alert.get("volume_oi_ratio", 0)),
                    iv_start=_safe_float(alert.get("iv_start", 0)),
                    iv_end=_safe_float(alert.get("iv_end", 0)),
                    underlying_price=_safe_float(alert.get("underlying_price", 0)),
                    alert_rule=str(alert.get("alert_rule", "")),
                    has_sweep=bool(alert.get("has_sweep", False)),
                    has_floor=bool(alert.get("has_floor", False)),
                    has_multileg=bool(alert.get("has_multileg", False)),
                )
                entries.append(entry)
            except Exception as exc:
                logger.warning(
                    "uw_alert_parse_error",
                    alert=alert,
                    error=str(exc),
                )
                continue

        return entries

    # ------------------------------------------------------------------
    # Tradier fetch (fallback — original implementation)
    # ------------------------------------------------------------------

    async def _fetch_from_tradier(
        self, symbol: str, target_date: date
    ) -> list[FlowEntry]:
        """Fetch option chain data from Tradier as FlowEntry list."""
        entries: list[FlowEntry] = []

        async with httpx.AsyncClient(timeout=30) as client:
            # Get option expirations first
            exp_resp = await client.get(
                f"{self._base_url}/markets/options/expirations",
                headers=self._tradier_headers,
                params={"symbol": symbol, "includeAllRoots": "true"},
            )
            exp_resp.raise_for_status()
            exp_data = exp_resp.json()

            expirations = exp_data.get("expirations", {}).get("date", [])
            if isinstance(expirations, str):
                expirations = [expirations]

            # Get nearest expiration for 0DTE flow
            target_exp = None
            for exp in expirations:
                if exp >= target_date.isoformat():
                    target_exp = exp
                    break
            if target_exp is None and expirations:
                target_exp = expirations[0]
            if target_exp is None:
                logger.warning("no_expirations_found", symbol=symbol)
                return entries

            # Get option chain with greeks
            chain_resp = await client.get(
                f"{self._base_url}/markets/options/chains",
                headers=self._tradier_headers,
                params={
                    "symbol": symbol,
                    "expiration": target_exp,
                    "greeks": "true",
                },
            )
            chain_resp.raise_for_status()
            chain_data = chain_resp.json()

            options = chain_data.get("options", {}).get("option", [])
            if isinstance(options, dict):
                options = [options]

            for opt in options:
                vol = opt.get("volume", 0)
                last_price = opt.get("last", 0.0) or opt.get("bid", 0.0)
                if vol <= 0 or last_price <= 0:
                    continue

                premium = last_price * vol * 100

                ask = opt.get("ask", 0.0)
                bid = opt.get("bid", 0.0)
                if last_price >= ask and ask > 0:
                    side = OptionSide.BUY
                elif last_price <= bid and bid > 0:
                    side = OptionSide.SELL
                else:
                    side = OptionSide.UNKNOWN

                entry = FlowEntry(
                    symbol=symbol,
                    strike=opt.get("strike", 0.0),
                    expiry=target_exp,
                    option_type=(
                        FlowOptionType.CALL
                        if opt.get("option_type") == "call"
                        else FlowOptionType.PUT
                    ),
                    premium=round(premium, 2),
                    volume=vol,
                    price=last_price,
                    exchange=opt.get("exchange", ""),
                    side=side,
                    open_interest=opt.get("open_interest", 0),
                )
                entries.append(entry)

        return entries

    # ------------------------------------------------------------------
    # Unusual Whales enrichment methods
    # ------------------------------------------------------------------

    async def get_flow_alerts(
        self, symbol: str, limit: int = 50
    ) -> list[FlowEntry]:
        """Get flow alerts as FlowEntry objects.

        Convenience wrapper — same as get_flow() but forces UW provider.
        Falls back to get_flow() if UW is unavailable.
        """
        if self._uw is not None:
            try:
                return await self._fetch_from_unusual_whales(symbol)
            except Exception as exc:
                logger.error("get_flow_alerts_failed", error=str(exc))
        return await self.get_flow(symbol)

    async def get_options_volume(self, symbol: str) -> OptionsVolume | None:
        """Get daily options volume aggregates from Unusual Whales.

        Returns None if UW client is not configured or API fails.
        """
        if self._uw is None:
            logger.warning("uw_not_configured", method="get_options_volume")
            return None
        try:
            raw_list = await self._uw.get_options_volume(symbol)
            if not raw_list:
                return None
            # Take the most recent entry
            raw = raw_list[-1] if isinstance(raw_list, list) else raw_list
            return OptionsVolume(
                symbol=symbol,
                date=str(raw.get("date", "")),
                call_volume=_safe_int(raw.get("call_volume", 0)),
                put_volume=_safe_int(raw.get("put_volume", 0)),
                call_premium=_safe_float(raw.get("call_premium", 0)),
                put_premium=_safe_float(raw.get("put_premium", 0)),
                net_call_premium=_safe_float(raw.get("net_call_premium", 0)),
                net_put_premium=_safe_float(raw.get("net_put_premium", 0)),
                bearish_premium=_safe_float(raw.get("bearish_premium", 0)),
                bullish_premium=_safe_float(raw.get("bullish_premium", 0)),
                put_open_interest=_safe_int(raw.get("put_open_interest", 0)),
                call_open_interest=_safe_int(raw.get("call_open_interest", 0)),
                call_volume_ask_side=_safe_int(raw.get("call_volume_ask_side", 0)),
                call_volume_bid_side=_safe_int(raw.get("call_volume_bid_side", 0)),
                put_volume_ask_side=_safe_int(raw.get("put_volume_ask_side", 0)),
                put_volume_bid_side=_safe_int(raw.get("put_volume_bid_side", 0)),
            )
        except Exception as exc:
            logger.error("get_options_volume_failed", symbol=symbol, error=str(exc))
            return None

    async def get_net_premium_ticks(
        self, symbol: str, target_date: str | None = None
    ) -> list[NetPremiumTick]:
        """Get minute-by-minute net premium ticks from Unusual Whales.

        Args:
            symbol: Ticker symbol.
            target_date: ISO date string. Defaults to today.

        Returns:
            List of NetPremiumTick objects, or empty list on failure.
        """
        if self._uw is None:
            return []
        try:
            raw_list = await self._uw.get_net_premium_ticks(symbol, target_date)
            ticks: list[NetPremiumTick] = []
            for raw in raw_list:
                ticks.append(NetPremiumTick(
                    tape_time=str(raw.get("tape_time", "")),
                    call_volume=_safe_int(raw.get("call_volume", 0)),
                    put_volume=_safe_int(raw.get("put_volume", 0)),
                    net_call_premium=_safe_float(raw.get("net_call_premium", 0)),
                    net_put_premium=_safe_float(raw.get("net_put_premium", 0)),
                    net_call_volume=_safe_int(raw.get("net_call_volume", 0)),
                    net_put_volume=_safe_int(raw.get("net_put_volume", 0)),
                    net_delta=_safe_float(raw.get("net_delta", 0)),
                ))
            return ticks
        except Exception as exc:
            logger.error("get_net_premium_ticks_failed", symbol=symbol, error=str(exc))
            return []

    async def get_dark_pool(
        self, limit: int = 10, ticker: str | None = None
    ) -> list[DarkPoolEntry]:
        """Get recent dark pool prints from Unusual Whales.

        Args:
            limit: Max number of prints.
            ticker: Optional filter by ticker (client-side).

        Returns:
            List of DarkPoolEntry objects, or empty list on failure.
        """
        if self._uw is None:
            return []
        try:
            raw_list = await self._uw.get_dark_pool(limit)
            entries: list[DarkPoolEntry] = []
            for raw in raw_list:
                dp_ticker = str(raw.get("ticker", ""))
                if ticker and dp_ticker.upper() != ticker.upper():
                    continue
                executed = raw.get("executed_at")
                entries.append(DarkPoolEntry(
                    ticker=dp_ticker,
                    price=_safe_float(raw.get("price", 0)),
                    size=_safe_int(raw.get("size", 0)),
                    volume=_safe_int(raw.get("volume", 0)),
                    premium=_safe_float(raw.get("premium", 0)),
                    executed_at=_parse_timestamp(executed) if executed else None,
                    nbbo_ask=_safe_float(raw.get("nbbo_ask", 0)),
                    nbbo_bid=_safe_float(raw.get("nbbo_bid", 0)),
                    market_center=str(raw.get("market_center", "")),
                ))
            return entries
        except Exception as exc:
            logger.error("get_dark_pool_failed", error=str(exc))
            return []

    async def get_max_pain(self, symbol: str) -> MaxPainData | None:
        """Get max pain data for a symbol from Unusual Whales.

        Returns the nearest expiry's max pain data, or None on failure.
        """
        if self._uw is None:
            return None
        try:
            raw_list = await self._uw.get_max_pain(symbol)
            if not raw_list:
                return None
            # Use the nearest expiry (first entry)
            raw = raw_list[0] if isinstance(raw_list, list) else raw_list
            return MaxPainData(
                symbol=symbol,
                expiry=str(raw.get("expiry", "")),
                max_pain=_safe_float(raw.get("max_pain", 0)),
                close=_safe_float(raw.get("close", 0)),
                open=_safe_float(raw.get("open", 0)),
                next_upper_strike=_safe_float(raw.get("next_upper_strike", 0)),
                next_lower_strike=_safe_float(raw.get("next_lower_strike", 0)),
            )
        except Exception as exc:
            logger.error("get_max_pain_failed", symbol=symbol, error=str(exc))
            return None

    async def get_greek_exposure(
        self, symbol: str, target_date: str | None = None
    ) -> GreekExposure | None:
        """Get aggregate greek exposure for a symbol from Unusual Whales.

        Args:
            symbol: Ticker symbol.
            target_date: ISO date string. Defaults to today.

        Returns:
            GreekExposure object, or None on failure.
        """
        if self._uw is None:
            return None
        try:
            raw_list = await self._uw.get_greek_exposure(symbol, target_date)
            if not raw_list:
                return None
            # Use the most recent entry
            raw = raw_list[-1] if isinstance(raw_list, list) else raw_list
            call_delta = _safe_float(raw.get("call_delta", 0))
            put_delta = _safe_float(raw.get("put_delta", 0))
            call_gamma = _safe_float(raw.get("call_gamma", 0))
            put_gamma = _safe_float(raw.get("put_gamma", 0))
            return GreekExposure(
                symbol=symbol,
                date=str(raw.get("date", target_date or "")),
                call_delta=call_delta,
                put_delta=put_delta,
                net_delta=call_delta + put_delta,
                call_gamma=call_gamma,
                put_gamma=put_gamma,
                net_gamma=call_gamma + put_gamma,
                call_charm=_safe_float(raw.get("call_charm", 0)),
                put_charm=_safe_float(raw.get("put_charm", 0)),
                call_vanna=_safe_float(raw.get("call_vanna", 0)),
                put_vanna=_safe_float(raw.get("put_vanna", 0)),
            )
        except Exception as exc:
            logger.error("get_greek_exposure_failed", symbol=symbol, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # analyze_flow  (preserved interface)
    # ------------------------------------------------------------------

    def analyze_flow(self, entries: list[FlowEntry]) -> FlowSummary:
        """Analyze a list of flow entries into a summary.

        Calculates net call/put premium, identifies the biggest trades,
        detects unusual activity, clusters into flow bubbles, and
        produces a directional bias score.

        Args:
            entries: List of FlowEntry trades for a symbol.

        Returns:
            FlowSummary with all analysis results.
        """
        if not entries:
            return FlowSummary(
                symbol="UNKNOWN",
                date=date.today().isoformat(),
            )

        symbol = entries[0].symbol
        today_str = date.today().isoformat()

        total_call_premium = sum(
            e.premium for e in entries if e.option_type == FlowOptionType.CALL
        )
        total_put_premium = sum(
            e.premium for e in entries if e.option_type == FlowOptionType.PUT
        )
        call_volume = sum(
            e.volume for e in entries if e.option_type == FlowOptionType.CALL
        )
        put_volume = sum(
            e.volume for e in entries if e.option_type == FlowOptionType.PUT
        )

        net_premium = total_call_premium - total_put_premium
        pcr = put_volume / call_volume if call_volume > 0 else 0.0

        # Top 5 biggest trades by premium
        sorted_by_premium = sorted(entries, key=lambda e: e.premium, reverse=True)
        biggest = sorted_by_premium[:5]

        # Unusual activity
        unusual = self.detect_unusual_activity(entries)

        # Flow bubbles
        bubbles = self._cluster_flow_bubbles(entries)

        # Bias score — uses UW-enriched data when available
        bias = self._calculate_flow_bias(
            total_call_premium, total_put_premium, entries
        )

        summary = FlowSummary(
            symbol=symbol,
            date=today_str,
            total_call_premium=round(total_call_premium, 2),
            total_put_premium=round(total_put_premium, 2),
            net_premium=round(net_premium, 2),
            call_volume=call_volume,
            put_volume=put_volume,
            put_call_ratio=round(pcr, 3),
            biggest_trades=biggest,
            unusual_trades=unusual,
            flow_bubbles=bubbles,
            flow_bias_score=round(bias, 2),
        )

        logger.info(
            "flow_analyzed",
            symbol=symbol,
            call_premium=summary.total_call_premium,
            put_premium=summary.total_put_premium,
            pcr=summary.put_call_ratio,
            bias=summary.flow_bias_score,
            unusual_count=len(unusual),
        )
        return summary

    def detect_unusual_activity(
        self, entries: list[FlowEntry]
    ) -> list[FlowEntry]:
        """Filter for trades with premium exceeding the configured minimum.

        Default minimum is $100K. These are the trades that move markets —
        institutional block orders, hedge fund positioning, etc.

        Also flags entries with sweeps, floor trades, and high vol/OI ratio
        as unusual even if below the premium threshold.

        Args:
            entries: All flow entries for the day.

        Returns:
            List of FlowEntry trades exceeding min_premium threshold.
        """
        min_premium = self._cfg.min_premium
        unusual: list[FlowEntry] = []

        for e in entries:
            is_big = e.premium >= min_premium
            is_sweep = e.has_sweep and e.premium >= min_premium * 0.5
            is_floor = e.has_floor and e.premium >= min_premium * 0.5
            high_vol_oi = e.volume_oi_ratio > 3.0 and e.premium >= min_premium * 0.3

            if is_big or is_sweep or is_floor or high_vol_oi:
                unusual.append(e)

        if unusual:
            logger.info(
                "unusual_flow_detected",
                count=len(unusual),
                total_premium=sum(e.premium for e in unusual),
                sweeps=sum(1 for e in unusual if e.has_sweep),
                floor_trades=sum(1 for e in unusual if e.has_floor),
                min_threshold=min_premium,
            )

        return sorted(unusual, key=lambda e: e.premium, reverse=True)

    # ------------------------------------------------------------------
    # Bias methods (preserved interface)
    # ------------------------------------------------------------------

    async def get_flow_bias(self, symbol: str) -> float:
        """Get the flow bias score for a symbol.

        Convenience method that fetches flow data and returns just the
        bias score (-100 to +100).

        Args:
            symbol: Ticker symbol.

        Returns:
            Flow bias score. Positive = bullish flow, negative = bearish.
        """
        entries = await self.get_flow(symbol)
        if not entries:
            return 0.0
        summary = self.analyze_flow(entries)
        return summary.flow_bias_score

    def get_flow_bias_sync(self, entries: list[FlowEntry]) -> float:
        """Synchronous version — get flow bias from pre-fetched entries.

        Args:
            entries: Pre-fetched flow entries.

        Returns:
            Flow bias score -100 to +100.
        """
        if not entries:
            return 0.0
        summary = self.analyze_flow(entries)
        return summary.flow_bias_score

    # ------------------------------------------------------------------
    # Bias calculation (enhanced with UW data)
    # ------------------------------------------------------------------

    def _calculate_flow_bias(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
    ) -> float:
        """Calculate the directional bias from flow data.

        Enhanced scoring using Unusual Whales data when available:

        1. **Premium ratio** (20%) — net call vs put premium
        2. **Ask/Bid side premium** (25%) — aggressive buying at ask = bullish,
           aggressive selling at bid = bearish. This is THE most reliable
           signal from UW data.
        3. **Sweep/floor activity** (20%) — sweeps signal urgency,
           floor trades signal institutional size
        4. **Volume/OI ratio** (15%) — high ratio = new positioning,
           weighted by direction
        5. **Unusual activity direction** (20%) — big trades set the tone

        Falls back to the simpler 3-component model when UW enrichment
        fields are all zero (i.e. Tradier data).

        Args:
            call_premium: Total call premium.
            put_premium: Total put premium.
            entries: All flow entries.

        Returns:
            Score from -100 (extreme bearish) to +100 (extreme bullish).
        """
        total = call_premium + put_premium
        if total == 0:
            return 0.0

        # Check if we have UW-enriched data
        has_uw_data = any(
            e.ask_side_premium > 0 or e.bid_side_premium > 0
            for e in entries
        )

        if has_uw_data:
            return self._calculate_flow_bias_uw(
                call_premium, put_premium, entries, total
            )
        else:
            return self._calculate_flow_bias_tradier(
                call_premium, put_premium, entries, total
            )

    def _calculate_flow_bias_uw(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
        total: float,
    ) -> float:
        """UW-enriched bias calculation — uses ask/bid side premium,
        sweep activity, floor trades, and vol/OI ratio."""

        # Component 1: Premium ratio (20%)
        call_pct = call_premium / total
        premium_score = (call_pct - 0.5) * 200

        # Component 2: Ask/Bid side premium (25%)
        # Ask-side premium on calls = aggressive call buying = bullish
        # Ask-side premium on puts = aggressive put buying = bearish
        # Bid-side premium on calls = call selling = bearish
        # Bid-side premium on puts = put selling = bullish
        bull_side_prem = sum(
            e.ask_side_premium for e in entries
            if e.option_type == FlowOptionType.CALL
        ) + sum(
            e.bid_side_premium for e in entries
            if e.option_type == FlowOptionType.PUT
        )
        bear_side_prem = sum(
            e.ask_side_premium for e in entries
            if e.option_type == FlowOptionType.PUT
        ) + sum(
            e.bid_side_premium for e in entries
            if e.option_type == FlowOptionType.CALL
        )
        side_total = bull_side_prem + bear_side_prem
        if side_total > 0:
            side_score = ((bull_side_prem / side_total) - 0.5) * 200
        else:
            side_score = 0.0

        # Component 3: Sweep/Floor activity direction (20%)
        # Sweeps signal urgency — direction matters
        sweep_entries = [e for e in entries if e.has_sweep]
        floor_entries = [e for e in entries if e.has_floor]
        urgent_entries = sweep_entries + floor_entries

        if urgent_entries:
            urgent_call_prem = sum(
                e.premium for e in urgent_entries
                if e.option_type == FlowOptionType.CALL
            )
            urgent_put_prem = sum(
                e.premium for e in urgent_entries
                if e.option_type == FlowOptionType.PUT
            )
            urgent_total = urgent_call_prem + urgent_put_prem
            if urgent_total > 0:
                sweep_score = ((urgent_call_prem / urgent_total) - 0.5) * 200
            else:
                sweep_score = 0.0
        else:
            sweep_score = 0.0

        # Component 4: Volume/OI ratio weighted direction (15%)
        # High vol/OI = new positioning. Direction tells the story.
        high_voi_entries = [e for e in entries if e.volume_oi_ratio > 1.5]
        if high_voi_entries:
            voi_call_prem = sum(
                e.premium * min(e.volume_oi_ratio, 10)
                for e in high_voi_entries
                if e.option_type == FlowOptionType.CALL
            )
            voi_put_prem = sum(
                e.premium * min(e.volume_oi_ratio, 10)
                for e in high_voi_entries
                if e.option_type == FlowOptionType.PUT
            )
            voi_total = voi_call_prem + voi_put_prem
            if voi_total > 0:
                voi_score = ((voi_call_prem / voi_total) - 0.5) * 200
            else:
                voi_score = 0.0
        else:
            voi_score = 0.0

        # Component 5: Unusual activity direction (20%)
        unusual = [e for e in entries if e.premium >= self._cfg.min_premium]
        if unusual:
            unusual_call = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.CALL
            )
            unusual_put = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.PUT
            )
            unusual_total = unusual_call + unusual_put
            if unusual_total > 0:
                unusual_score = ((unusual_call / unusual_total) - 0.5) * 200
            else:
                unusual_score = 0.0
        else:
            unusual_score = 0.0

        # Weighted combination
        final = (
            premium_score * 0.20
            + side_score * 0.25
            + sweep_score * 0.20
            + voi_score * 0.15
            + unusual_score * 0.20
        )
        return float(max(-100, min(100, final)))

    def _calculate_flow_bias_tradier(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
        total: float,
    ) -> float:
        """Legacy Tradier-based bias — 3-component model."""

        # Component 1: Premium ratio
        call_pct = call_premium / total
        premium_score = (call_pct - 0.5) * 200

        # Component 2: Aggressive flow
        aggressive_bull = sum(
            e.premium for e in entries
            if e.side == OptionSide.BUY and e.option_type == FlowOptionType.CALL
        )
        aggressive_bear = sum(
            e.premium for e in entries
            if e.side == OptionSide.BUY and e.option_type == FlowOptionType.PUT
        )
        agg_total = aggressive_bull + aggressive_bear
        agg_score = (
            ((aggressive_bull / agg_total) - 0.5) * 200 if agg_total > 0 else 0.0
        )

        # Component 3: Unusual activity
        unusual = [e for e in entries if e.premium >= self._cfg.min_premium]
        if unusual:
            unusual_call = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.CALL
            )
            unusual_put = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.PUT
            )
            unusual_total = unusual_call + unusual_put
            unusual_score = (
                ((unusual_call / unusual_total) - 0.5) * 200
                if unusual_total > 0
                else 0.0
            )
        else:
            unusual_score = 0.0

        final = premium_score * 0.30 + agg_score * 0.30 + unusual_score * 0.40
        return float(max(-100, min(100, final)))

    # ------------------------------------------------------------------
    # Flow bubble clustering (preserved)
    # ------------------------------------------------------------------

    def _cluster_flow_bubbles(
        self, entries: list[FlowEntry]
    ) -> list[FlowBubble]:
        """Cluster flow entries into bubbles at specific strikes.

        A bubble forms when multiple large trades land at the same
        strike/expiry combination. These are key levels where
        institutions are building positions.

        Args:
            entries: All flow entries.

        Returns:
            List of FlowBubble clusters, sorted by total premium.
        """
        clusters: dict[tuple[float, str, str], list[FlowEntry]] = {}
        for entry in entries:
            key = (entry.strike, entry.expiry, entry.option_type.value)
            clusters.setdefault(key, []).append(entry)

        bubbles: list[FlowBubble] = []
        for (strike, expiry, opt_type), cluster_entries in clusters.items():
            total_prem = sum(e.premium for e in cluster_entries)
            if total_prem < self._cfg.min_premium / 2:
                continue

            buy_premium = sum(
                e.premium for e in cluster_entries if e.side == OptionSide.BUY
            )
            sell_premium = sum(
                e.premium for e in cluster_entries if e.side == OptionSide.SELL
            )
            net_side = (
                OptionSide.BUY if buy_premium >= sell_premium else OptionSide.SELL
            )

            total_vol = sum(e.volume for e in cluster_entries)
            avg_price = (
                sum(e.price * e.volume for e in cluster_entries) / total_vol
                if total_vol > 0
                else 0.0
            )

            bubble = FlowBubble(
                symbol=cluster_entries[0].symbol,
                strike=strike,
                expiry=expiry,
                total_premium=round(total_prem, 2),
                trade_count=len(cluster_entries),
                net_side=net_side,
                dominant_type=FlowOptionType(opt_type),
                avg_price=round(avg_price, 4),
            )
            bubbles.append(bubble)

        bubbles.sort(key=lambda b: b.total_premium, reverse=True)
        return bubbles[:10]

    # ------------------------------------------------------------------
    # CSV import (preserved)
    # ------------------------------------------------------------------

    @classmethod
    def load_from_csv(cls, filepath: str | Path) -> list[FlowEntry]:
        """Load flow data from a CSV file for manual import.

        Expected columns: symbol, strike, expiry, option_type, premium,
        volume, price, side, timestamp

        Args:
            filepath: Path to the CSV file.

        Returns:
            List of FlowEntry parsed from the CSV.
        """
        entries: list[FlowEntry] = []
        path = Path(filepath)

        if not path.exists():
            logger.warning("csv_not_found", path=str(path))
            return entries

        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry = FlowEntry(
                        symbol=row["symbol"],
                        strike=float(row["strike"]),
                        expiry=row["expiry"],
                        option_type=FlowOptionType(row["option_type"]),
                        premium=float(row["premium"]),
                        volume=int(row["volume"]),
                        price=float(row["price"]),
                        side=OptionSide(row.get("side", "unknown")),
                        timestamp=datetime.fromisoformat(
                            row.get("timestamp", datetime.now().isoformat())
                        ),
                    )
                    entries.append(entry)
                except (KeyError, ValueError) as e:
                    logger.warning("csv_parse_error", row=row, error=str(e))

        logger.info("csv_loaded", path=str(path), count=len(entries))
        return entries


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: Any) -> int:
    """Safely convert a value to int, returning 0 on failure."""
    if val is None:
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def _parse_timestamp(val: Any) -> datetime:
    """Parse various timestamp formats into datetime."""
    if isinstance(val, datetime):
        return val
    if not val:
        return datetime.now()
    s = str(val)
    # Try common formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("timestamp_parse_failed", raw=s)
        return datetime.now()
