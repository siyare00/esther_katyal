"""LEAP Portfolio Manager — Long-Term Equity Options for Wealth Building.

LEAP (Long-term Equity Anticipation Securities) positions are 9-18 month
option contracts used for two distinct strategies:

1. **Deep ITM (delta > 0.80)** — Leveraged stock replacement. Moves nearly
   1:1 with the underlying but at a fraction of the capital. The "poor man's
   covered call" foundation.

2. **Speculative OTM (delta 0.20-0.40)** — Lottery tickets on high-conviction
   names. Small capital outlay, asymmetric upside. These are the moon shots.

Entry Discipline:
    - Only enter when RSI < 35 (oversold) OR price is at key Fibonacci support
    - Never chase — patience is the edge
    - Target January expirations (max theta runway)

Trim Schedule:
    - 25% off at +200% gain
    - Another 25% off at +400% gain
    - Let the remaining 50% ride with a trailing mental stop

Target Universe:
    NVDA, TSLA, AAPL, AMZN, META, PLTR, AVGO, CVNA, LLY, MSTR, GOOGL, APP
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from esther.data.tradier import TradierClient, OptionType, OptionQuote, Bar

logger = structlog.get_logger(__name__)

# Where we persist LEAP portfolio state
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEAP_PORTFOLIO_PATH = _PROJECT_ROOT / "data" / "leap_portfolio.json"

# Target symbols for LEAP scanning
LEAP_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMZN", "META", "PLTR",
    "AVGO", "CVNA", "LLY", "MSTR", "GOOGL", "APP",
]


# ── Enums ────────────────────────────────────────────────────────


class LeapStyle(str, Enum):
    """LEAP strategy style."""

    DEEP_ITM = "DEEP_ITM"          # delta > 0.80, leveraged stock replacement
    SPECULATIVE_OTM = "SPECULATIVE_OTM"  # delta 0.20-0.40, lottery tickets


class AlertType(str, Enum):
    """Types of LEAP alerts."""

    DELTA_EROSION = "DELTA_EROSION"       # Deep ITM losing delta edge
    TRIM_OPPORTUNITY = "TRIM_OPPORTUNITY"  # Position up enough to trim
    THESIS_REVIEW = "THESIS_REVIEW"        # Position down significantly
    EXPIRY_WARNING = "EXPIRY_WARNING"      # Approaching expiration


# ── Pydantic Models ──────────────────────────────────────────────


class TrimRecord(BaseModel):
    """Record of a partial exit (trim)."""

    date: str
    quantity_sold: int
    sell_price: float
    pnl: float
    pnl_pct: float
    reason: str


class LeapPosition(BaseModel):
    """A single LEAP position with full tracking."""

    id: str = Field(default_factory=lambda: f"leap_{uuid.uuid4().hex[:8]}")
    symbol: str                        # underlying ticker
    option_symbol: str = ""            # OCC option symbol from Tradier
    strike: float
    expiry: str                        # e.g. "2027-01-15"
    option_type: OptionType = OptionType.CALL
    style: LeapStyle
    quantity: int
    entry_price: float                 # per-contract premium at entry
    entry_date: str = Field(default_factory=lambda: date.today().isoformat())
    current_price: float = 0.0
    current_delta: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    trim_history: list[TrimRecord] = []
    active: bool = True


class LeapCandidate(BaseModel):
    """A potential LEAP entry identified by scanning."""

    symbol: str
    current_price: float
    rsi: float
    at_support: bool
    suggested_strike: float
    suggested_expiry: str
    style: LeapStyle
    estimated_cost: float              # per-contract cost (mid price * 100)
    rationale: str


class LeapAlert(BaseModel):
    """Alert generated during LEAP monitoring."""

    position_id: str
    symbol: str
    alert_type: AlertType
    message: str
    current_price: float = 0.0
    current_delta: float = 0.0
    pnl_pct: float = 0.0
    suggested_action: str = ""


class TrimResult(BaseModel):
    """Result of a partial position exit."""

    position_id: str
    symbol: str
    contracts_sold: int
    contracts_remaining: int
    sell_price: float
    realized_pnl: float
    realized_pnl_pct: float
    order_response: dict[str, Any] = {}


class LeapPortfolio(BaseModel):
    """Full LEAP portfolio summary."""

    positions: list[LeapPosition] = []
    total_cost: float = 0.0            # total capital deployed
    total_value: float = 0.0           # current market value
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    position_count: int = 0
    deep_itm_count: int = 0
    speculative_count: int = 0


# ── LEAP Manager ─────────────────────────────────────────────────


class LeapManager:
    """Manages a portfolio of LEAP option positions.

    Handles scanning for entries, executing purchases, monitoring positions,
    trimming winners, and persisting state to disk.

    Usage:
        async with TradierClient() as tradier:
            mgr = LeapManager(tradier)
            candidates = await mgr.get_leap_candidates(["NVDA", "TSLA"])
            for c in candidates:
                pos = await mgr.add_leap(c.symbol, c.suggested_strike,
                                          c.suggested_expiry, 5, c.style)
    """

    # Fibonacci retracement levels for support detection
    FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
    FIB_TOLERANCE = 0.015  # 1.5% proximity to fib level counts as "at support"

    # RSI entry threshold
    RSI_OVERSOLD = 35

    # Trim thresholds
    TRIM_1_GAIN_PCT = 2.0    # +200% → sell 25%
    TRIM_2_GAIN_PCT = 4.0    # +400% → sell another 25%
    TRIM_1_SIZE_PCT = 0.25
    TRIM_2_SIZE_PCT = 0.25

    # Alert thresholds
    DEEP_ITM_MIN_DELTA = 0.50    # alert if delta drops below this
    BIG_WINNER_PCT = 2.0          # +200% → consider trim
    BIG_LOSER_PCT = -0.30         # -30% → review thesis

    # Sizing
    MIN_CONTRACTS = 2
    MAX_CONTRACTS = 10

    # Minimum days to expiry for new entries
    MIN_DTE = 270  # ~9 months

    def __init__(self, tradier: TradierClient) -> None:
        self.tradier = tradier
        self._positions: dict[str, LeapPosition] = {}
        self._load_state()

    # ── Public Methods ───────────────────────────────────────────

    async def get_leap_candidates(
        self, symbols: list[str] | None = None
    ) -> list[LeapCandidate]:
        """Scan symbols for LEAP entry opportunities.

        Only suggests entries when:
        - RSI < 35 (oversold) OR price is at a Fibonacci support level
        - A valid January expiration exists 9+ months out
        - Option chain has liquid contracts at target delta

        Args:
            symbols: Tickers to scan. Defaults to LEAP_UNIVERSE.

        Returns:
            List of actionable LeapCandidate objects.
        """
        symbols = symbols or LEAP_UNIVERSE
        candidates: list[LeapCandidate] = []

        for symbol in symbols:
            try:
                candidate_pair = await self._evaluate_symbol(symbol)
                candidates.extend(candidate_pair)
            except Exception as e:
                logger.error("leap_scan_failed", symbol=symbol, error=str(e))

        logger.info("leap_scan_complete", candidates=len(candidates), scanned=len(symbols))
        return candidates

    async def add_leap(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        quantity: int,
        style: LeapStyle | str,
        option_type: OptionType = OptionType.CALL,
    ) -> LeapPosition:
        """Execute a LEAP purchase via Tradier and track it.

        Places a limit order at the mid price for the target contract.

        Args:
            symbol: Underlying ticker.
            strike: Strike price.
            expiry: Expiration date (YYYY-MM-DD).
            quantity: Number of contracts (clamped to 2-10).
            style: DEEP_ITM or SPECULATIVE_OTM.
            option_type: CALL or PUT.

        Returns:
            The new LeapPosition with order details.
        """
        if isinstance(style, str):
            style = LeapStyle(style)

        quantity = max(self.MIN_CONTRACTS, min(self.MAX_CONTRACTS, quantity))

        # Fetch the option chain to get current pricing
        chain = await self.tradier.get_option_chain(symbol, expiry, greeks=True)
        contract = self._find_contract(chain, strike, option_type)

        if not contract:
            raise ValueError(
                f"No contract found for {symbol} {strike} {option_type.value} exp {expiry}"
            )

        mid_price = contract.mid if contract.mid > 0 else round((contract.bid + contract.ask) / 2, 2)
        current_delta = contract.greeks.delta if contract.greeks else 0.0

        # Place limit order at mid
        order_resp = await self.tradier.place_order(
            symbol=symbol,
            option_symbol=contract.symbol,
            side="buy_to_open",
            quantity=quantity,
            order_type="limit",
            price=mid_price,
            duration="gtc",
        )

        position = LeapPosition(
            symbol=symbol,
            option_symbol=contract.symbol,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            style=style,
            quantity=quantity,
            entry_price=mid_price,
            current_price=mid_price,
            current_delta=current_delta,
        )

        self._positions[position.id] = position
        self._save_state()

        logger.info(
            "leap_opened",
            id=position.id,
            symbol=symbol,
            strike=strike,
            expiry=expiry,
            style=style.value,
            qty=quantity,
            price=mid_price,
            delta=current_delta,
            order=order_resp,
        )

        return position

    async def check_leaps(self) -> list[LeapAlert]:
        """Monitor all active LEAP positions and generate alerts.

        Checks for:
        - Delta erosion on Deep ITM positions (delta < 0.50)
        - Trim opportunities (position up 200%+)
        - Thesis review needed (position down 30%+)
        - Expiry warnings (< 90 days to expiration)

        Returns:
            List of LeapAlert objects requiring attention.
        """
        alerts: list[LeapAlert] = []
        active_positions = [p for p in self._positions.values() if p.active]

        if not active_positions:
            logger.info("leap_check_no_positions")
            return alerts

        for pos in active_positions:
            try:
                pos_alerts = await self._check_single_position(pos)
                alerts.extend(pos_alerts)
            except Exception as e:
                logger.error("leap_check_failed", id=pos.id, symbol=pos.symbol, error=str(e))

        self._save_state()
        logger.info("leap_check_complete", positions=len(active_positions), alerts=len(alerts))
        return alerts

    async def trim_position(self, position_id: str, pct: float | None = None) -> TrimResult:
        """Partially exit a LEAP position.

        If pct is not specified, uses the automatic trim schedule:
        - First trim: 25% at +200% gain
        - Second trim: 25% at +400% gain

        Args:
            position_id: ID of the position to trim.
            pct: Percentage of remaining position to sell (0.0-1.0).
                 If None, uses automatic trim rules.

        Returns:
            TrimResult with execution details.

        Raises:
            ValueError: If position not found or invalid trim.
        """
        pos = self._positions.get(position_id)
        if not pos or not pos.active:
            raise ValueError(f"Position {position_id} not found or inactive")

        # Determine trim percentage
        if pct is None:
            pct = self._calculate_auto_trim_pct(pos)
            if pct == 0:
                raise ValueError(
                    f"Position {position_id} doesn't meet auto-trim criteria "
                    f"(PnL: {pos.unrealized_pnl_pct:.0%})"
                )

        pct = max(0.01, min(1.0, pct))
        contracts_to_sell = max(1, int(pos.quantity * pct))

        if contracts_to_sell >= pos.quantity:
            contracts_to_sell = pos.quantity  # Full exit

        # Get current price for the sell
        current_price = await self._get_option_price(pos.option_symbol)
        if current_price is None:
            current_price = pos.current_price  # fallback

        # Place sell order
        order_resp = await self.tradier.place_order(
            symbol=pos.symbol,
            option_symbol=pos.option_symbol,
            side="sell_to_close",
            quantity=contracts_to_sell,
            order_type="limit",
            price=current_price,
            duration="gtc",
        )

        # Calculate realized P&L for this trim
        realized_pnl = round((current_price - pos.entry_price) * 100 * contracts_to_sell, 2)
        realized_pnl_pct = (
            (current_price - pos.entry_price) / pos.entry_price
            if pos.entry_price > 0 else 0.0
        )

        # Record the trim
        trim_record = TrimRecord(
            date=date.today().isoformat(),
            quantity_sold=contracts_to_sell,
            sell_price=current_price,
            pnl=realized_pnl,
            pnl_pct=round(realized_pnl_pct, 4),
            reason=f"Trim {pct:.0%} — PnL {pos.unrealized_pnl_pct:.0%}",
        )
        pos.trim_history.append(trim_record)

        # Update position
        pos.quantity -= contracts_to_sell
        if pos.quantity <= 0:
            pos.active = False
            logger.info("leap_fully_closed", id=pos.id, symbol=pos.symbol)

        self._save_state()

        result = TrimResult(
            position_id=pos.id,
            symbol=pos.symbol,
            contracts_sold=contracts_to_sell,
            contracts_remaining=pos.quantity,
            sell_price=current_price,
            realized_pnl=realized_pnl,
            realized_pnl_pct=round(realized_pnl_pct, 4),
            order_response=order_resp,
        )

        logger.info(
            "leap_trimmed",
            id=pos.id,
            symbol=pos.symbol,
            sold=contracts_to_sell,
            remaining=pos.quantity,
            price=current_price,
            realized_pnl=realized_pnl,
        )

        return result

    def get_leap_portfolio(self) -> LeapPortfolio:
        """Get full LEAP portfolio summary.

        Returns:
            LeapPortfolio with aggregated metrics across all active positions.
        """
        active = [p for p in self._positions.values() if p.active]

        total_cost = sum(p.entry_price * 100 * p.quantity for p in active)
        total_value = sum(p.current_price * 100 * p.quantity for p in active)
        total_pnl = total_value - total_cost
        total_pnl_pct = total_pnl / total_cost if total_cost > 0 else 0.0

        deep_itm = sum(1 for p in active if p.style == LeapStyle.DEEP_ITM)
        speculative = sum(1 for p in active if p.style == LeapStyle.SPECULATIVE_OTM)

        return LeapPortfolio(
            positions=active,
            total_cost=round(total_cost, 2),
            total_value=round(total_value, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            position_count=len(active),
            deep_itm_count=deep_itm,
            speculative_count=speculative,
        )

    # ── Private: Symbol Evaluation ───────────────────────────────

    async def _evaluate_symbol(self, symbol: str) -> list[LeapCandidate]:
        """Evaluate a single symbol for both LEAP styles.

        Returns 0-2 candidates (one per style if criteria are met).
        """
        candidates: list[LeapCandidate] = []

        # Fetch daily bars for RSI and Fibonacci levels
        end_date = date.today()
        start_date = end_date - timedelta(days=200)
        bars = await self.tradier.get_bars(
            symbol, interval="daily", start=start_date, end=end_date
        )

        if len(bars) < 30:
            logger.warning("leap_insufficient_bars", symbol=symbol, bars=len(bars))
            return candidates

        # Calculate RSI
        rsi = self._calculate_rsi(bars)

        # Check Fibonacci support
        at_support = self._check_fibonacci_support(bars)

        # Must meet entry criteria
        if rsi >= self.RSI_OVERSOLD and not at_support:
            logger.debug(
                "leap_no_entry_signal",
                symbol=symbol,
                rsi=round(rsi, 2),
                at_support=at_support,
            )
            return candidates

        current_price = bars[-1].close

        # Find the best January expiration
        expiry = await self._find_january_expiry(symbol)
        if not expiry:
            logger.warning("leap_no_valid_expiry", symbol=symbol)
            return candidates

        # Fetch option chain for that expiry
        chain = await self.tradier.get_option_chain(symbol, expiry, greeks=True)
        calls = [o for o in chain if o.option_type == OptionType.CALL]

        if not calls:
            return candidates

        # Build rationale
        entry_reasons: list[str] = []
        if rsi < self.RSI_OVERSOLD:
            entry_reasons.append(f"RSI oversold at {rsi:.1f}")
        if at_support:
            entry_reasons.append("Price at Fibonacci support")

        # Deep ITM candidate
        deep_strike = self._find_deep_itm_strike(calls)
        if deep_strike is not None:
            deep_contract = self._find_contract(calls, deep_strike, OptionType.CALL)
            if deep_contract:
                cost_per = round(deep_contract.mid * 100, 2) if deep_contract.mid > 0 else round(
                    (deep_contract.bid + deep_contract.ask) / 2 * 100, 2
                )
                candidates.append(LeapCandidate(
                    symbol=symbol,
                    current_price=current_price,
                    rsi=round(rsi, 2),
                    at_support=at_support,
                    suggested_strike=deep_strike,
                    suggested_expiry=expiry,
                    style=LeapStyle.DEEP_ITM,
                    estimated_cost=cost_per,
                    rationale=(
                        f"Deep ITM LEAP (stock replacement). "
                        f"{'; '.join(entry_reasons)}. "
                        f"Strike ${deep_strike} gives delta > 0.80 — "
                        f"moves nearly 1:1 with {symbol} at ~{cost_per / current_price / 100:.0%} "
                        f"of stock cost."
                    ),
                ))

        # Speculative OTM candidate
        spec_strike = self._find_speculative_strike(calls)
        if spec_strike is not None:
            spec_contract = self._find_contract(calls, spec_strike, OptionType.CALL)
            if spec_contract:
                cost_per = round(spec_contract.mid * 100, 2) if spec_contract.mid > 0 else round(
                    (spec_contract.bid + spec_contract.ask) / 2 * 100, 2
                )
                candidates.append(LeapCandidate(
                    symbol=symbol,
                    current_price=current_price,
                    rsi=round(rsi, 2),
                    at_support=at_support,
                    suggested_strike=spec_strike,
                    suggested_expiry=expiry,
                    style=LeapStyle.SPECULATIVE_OTM,
                    estimated_cost=cost_per,
                    rationale=(
                        f"Speculative OTM LEAP (lottery ticket). "
                        f"{'; '.join(entry_reasons)}. "
                        f"Strike ${spec_strike} with delta 0.20-0.40 — "
                        f"asymmetric upside if {symbol} rips. "
                        f"Max risk: ${cost_per} per contract."
                    ),
                ))

        return candidates

    # ── Private: Position Monitoring ─────────────────────────────

    async def _check_single_position(self, pos: LeapPosition) -> list[LeapAlert]:
        """Check a single position and update its market data."""
        alerts: list[LeapAlert] = []

        # Fetch current option data
        chain = await self.tradier.get_option_chain(pos.symbol, pos.expiry, greeks=True)
        contract = self._find_contract(chain, pos.strike, pos.option_type)

        if not contract:
            # Option may have been delisted or expired
            logger.warning("leap_contract_not_found", id=pos.id, symbol=pos.symbol)
            return alerts

        # Update position market data
        mid = contract.mid if contract.mid > 0 else round((contract.bid + contract.ask) / 2, 2)
        pos.current_price = mid
        pos.current_delta = contract.greeks.delta if contract.greeks else 0.0

        # Calculate unrealized P&L
        cost_basis = pos.entry_price * 100 * pos.quantity
        current_value = pos.current_price * 100 * pos.quantity
        pos.unrealized_pnl = round(current_value - cost_basis, 2)
        pos.unrealized_pnl_pct = round(
            pos.unrealized_pnl / cost_basis if cost_basis > 0 else 0.0, 4
        )

        # Check delta erosion on Deep ITM
        if pos.style == LeapStyle.DEEP_ITM and pos.current_delta < self.DEEP_ITM_MIN_DELTA:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.DELTA_EROSION,
                message=(
                    f"⚠️ {pos.symbol} Deep ITM LEAP delta dropped to {pos.current_delta:.2f} "
                    f"(below {self.DEEP_ITM_MIN_DELTA}). Losing stock-replacement edge."
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    "Roll down to a lower strike to restore delta > 0.80, "
                    "or close if thesis is broken."
                ),
            ))

        # Check for trim opportunity (+200%+)
        if pos.unrealized_pnl_pct >= self.BIG_WINNER_PCT:
            auto_trim_pct = self._calculate_auto_trim_pct(pos)
            if auto_trim_pct > 0:
                alerts.append(LeapAlert(
                    position_id=pos.id,
                    symbol=pos.symbol,
                    alert_type=AlertType.TRIM_OPPORTUNITY,
                    message=(
                        f"🎯 {pos.symbol} LEAP up {pos.unrealized_pnl_pct:.0%}! "
                        f"Consider trimming {auto_trim_pct:.0%} ({int(pos.quantity * auto_trim_pct)} contracts). "
                        f"Entry: ${pos.entry_price:.2f} → Now: ${pos.current_price:.2f}"
                    ),
                    current_price=pos.current_price,
                    current_delta=pos.current_delta,
                    pnl_pct=pos.unrealized_pnl_pct,
                    suggested_action=f"Trim {auto_trim_pct:.0%} to lock in gains, let the rest ride.",
                ))

        # Check for thesis review (-30%+)
        if pos.unrealized_pnl_pct <= self.BIG_LOSER_PCT:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.THESIS_REVIEW,
                message=(
                    f"🔴 {pos.symbol} LEAP down {pos.unrealized_pnl_pct:.0%}. "
                    f"Entry: ${pos.entry_price:.2f} → Now: ${pos.current_price:.2f}. "
                    f"Review thesis — is the setup still valid?"
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    "Re-evaluate: Has the fundamental thesis changed? "
                    "If yes, cut the loss. If no, consider adding at lower prices."
                ),
            ))

        # Check expiry proximity
        days_to_expiry = (date.fromisoformat(pos.expiry) - date.today()).days
        if 0 < days_to_expiry <= 90:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.EXPIRY_WARNING,
                message=(
                    f"⏰ {pos.symbol} LEAP expires in {days_to_expiry} days ({pos.expiry}). "
                    f"Theta decay accelerating — roll or close."
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    f"Roll to next January expiry to maintain time value, "
                    f"or close if P&L is acceptable ({pos.unrealized_pnl_pct:.0%})."
                ),
            ))
        elif days_to_expiry <= 0:
            pos.active = False
            logger.warning("leap_expired", id=pos.id, symbol=pos.symbol, expiry=pos.expiry)

        return alerts

    # ── Private: RSI Calculation ─────────────────────────────────

    @staticmethod
    def _calculate_rsi(bars: list[Bar], period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index) from price bars.

        Uses the Wilder smoothing method (exponential moving average of
        gains and losses).

        Args:
            bars: List of OHLCV bars (must have at least period+1 bars).
            period: RSI lookback period. Default 14.

        Returns:
            RSI value between 0 and 100.
        """
        if len(bars) < period + 1:
            return 50.0  # Not enough data, return neutral

        closes = [b.close for b in bars]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Initial average gain/loss over first `period` changes
        gains = [d if d > 0 else 0.0 for d in deltas[:period]]
        losses = [-d if d < 0 else 0.0 for d in deltas[:period]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        # Wilder smoothing for remaining periods
        for i in range(period, len(deltas)):
            d = deltas[i]
            current_gain = d if d > 0 else 0.0
            current_loss = -d if d < 0 else 0.0

            avg_gain = (avg_gain * (period - 1) + current_gain) / period
            avg_loss = (avg_loss * (period - 1) + current_loss) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return round(rsi, 2)

    # ── Private: Fibonacci Support Detection ─────────────────────

    def _check_fibonacci_support(self, bars: list[Bar]) -> bool:
        """Check if the current price is near a Fibonacci retracement support level.

        Uses the highest high and lowest low of the lookback period to
        calculate Fibonacci retracement levels, then checks if the current
        price is within FIB_TOLERANCE of any of them.

        Args:
            bars: Daily OHLCV bars (uses last 100 bars for swing range).

        Returns:
            True if price is at or near a Fibonacci support level.
        """
        lookback = bars[-100:] if len(bars) >= 100 else bars
        current_price = bars[-1].close

        swing_high = max(b.high for b in lookback)
        swing_low = min(b.low for b in lookback)
        swing_range = swing_high - swing_low

        if swing_range <= 0:
            return False

        for fib in self.FIB_LEVELS:
            # Retracement level = high - (range * fib)
            fib_price = swing_high - (swing_range * fib)
            distance_pct = abs(current_price - fib_price) / current_price

            if distance_pct <= self.FIB_TOLERANCE:
                logger.debug(
                    "leap_fib_support_found",
                    fib_level=fib,
                    fib_price=round(fib_price, 2),
                    current_price=current_price,
                    distance_pct=round(distance_pct, 4),
                )
                return True

        return False

    # ── Private: Strike Selection ────────────────────────────────

    @staticmethod
    def _find_deep_itm_strike(
        chain: list[OptionQuote], target_delta: float = 0.80
    ) -> float | None:
        """Find the best deep ITM call strike with delta >= target.

        Picks the strike closest to the target delta from above — we want
        at least target_delta but not so deep that we're paying pure
        intrinsic with no leverage benefit.

        Args:
            chain: List of call OptionQuotes with greeks.
            target_delta: Minimum delta threshold.

        Returns:
            Strike price, or None if no suitable strike found.
        """
        candidates = []
        for opt in chain:
            if opt.option_type != OptionType.CALL:
                continue
            if not opt.greeks or opt.greeks.delta <= 0:
                continue
            if opt.greeks.delta >= target_delta:
                candidates.append(opt)

        if not candidates:
            return None

        # Sort by delta ascending — pick the one closest to target_delta
        # (least deep ITM that still meets the threshold)
        candidates.sort(key=lambda o: o.greeks.delta)  # type: ignore[union-attr]
        best = candidates[0]

        # Ensure there's actual liquidity
        if best.bid <= 0 or best.open_interest < 10:
            # Try the next candidate
            for c in candidates[1:]:
                if c.bid > 0 and c.open_interest >= 10:
                    return c.strike
            return None

        return best.strike

    @staticmethod
    def _find_speculative_strike(
        chain: list[OptionQuote],
        min_delta: float = 0.20,
        max_delta: float = 0.40,
    ) -> float | None:
        """Find the best speculative OTM call strike with delta in range.

        Picks the strike closest to the midpoint of the delta range for
        the best risk/reward balance.

        Args:
            chain: List of call OptionQuotes with greeks.
            min_delta: Minimum delta for the range.
            max_delta: Maximum delta for the range.

        Returns:
            Strike price, or None if no suitable strike found.
        """
        mid_delta = (min_delta + max_delta) / 2
        candidates = []

        for opt in chain:
            if opt.option_type != OptionType.CALL:
                continue
            if not opt.greeks or opt.greeks.delta <= 0:
                continue
            if min_delta <= opt.greeks.delta <= max_delta:
                candidates.append(opt)

        if not candidates:
            return None

        # Sort by distance from midpoint delta
        candidates.sort(key=lambda o: abs(o.greeks.delta - mid_delta))  # type: ignore[union-attr]

        # Prefer liquid contracts
        for c in candidates:
            if c.bid > 0 and c.open_interest >= 5:
                return c.strike

        # Fallback: take best delta match even if less liquid
        return candidates[0].strike if candidates else None

    # ── Private: Expiry Selection ────────────────────────────────

    async def _find_january_expiry(self, symbol: str) -> str | None:
        """Find the next January expiration that's at least 9 months out.

        Prefers January expirations for maximum liquidity and standard
        LEAP dates. Falls back to the furthest available expiry if no
        January date qualifies.

        Args:
            symbol: Underlying ticker.

        Returns:
            Expiration date string (YYYY-MM-DD), or None.
        """
        expirations = await self.tradier.get_option_expirations(symbol)
        if not expirations:
            return None

        today = date.today()
        min_expiry_date = today + timedelta(days=self.MIN_DTE)

        # Look for January expirations first
        january_candidates: list[str] = []
        all_valid: list[str] = []

        for exp_str in expirations:
            try:
                exp_date = date.fromisoformat(exp_str)
            except ValueError:
                continue

            if exp_date < min_expiry_date:
                continue

            all_valid.append(exp_str)

            # January = month 1, typically the 15th or 17th (3rd Friday)
            if exp_date.month == 1:
                january_candidates.append(exp_str)

        if january_candidates:
            # Pick the nearest January that qualifies
            january_candidates.sort()
            return january_candidates[0]

        if all_valid:
            # No January — pick the furthest out expiry
            all_valid.sort(reverse=True)
            return all_valid[0]

        return None

    # ── Private: Helpers ─────────────────────────────────────────

    @staticmethod
    def _find_contract(
        chain: list[OptionQuote], strike: float, option_type: OptionType
    ) -> OptionQuote | None:
        """Find a specific contract in an option chain by strike and type."""
        for opt in chain:
            if opt.strike == strike and opt.option_type == option_type:
                return opt
        return None

    def _calculate_auto_trim_pct(self, pos: LeapPosition) -> float:
        """Determine auto-trim percentage based on position P&L and trim history.

        Trim schedule:
        - +200%: trim 25% (first trim)
        - +400%: trim 25% (second trim)
        - Remaining 50%: let it ride

        Returns:
            Trim percentage (0.0 if no trim warranted).
        """
        trims_done = len(pos.trim_history)

        if pos.unrealized_pnl_pct >= self.TRIM_2_GAIN_PCT and trims_done < 2:
            return self.TRIM_2_SIZE_PCT

        if pos.unrealized_pnl_pct >= self.TRIM_1_GAIN_PCT and trims_done < 1:
            return self.TRIM_1_SIZE_PCT

        return 0.0

    async def _get_option_price(self, option_symbol: str) -> float | None:
        """Fetch current mid price for an option contract."""
        try:
            quotes = await self.tradier.get_quotes([option_symbol])
            if quotes:
                q = quotes[0]
                mid = round((q.bid + q.ask) / 2, 2)
                return mid if mid > 0 else q.last
        except Exception as e:
            logger.error("leap_price_fetch_failed", option=option_symbol, error=str(e))
        return None

    # ── State Persistence ────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist all LEAP positions to JSON file."""
        LEAP_PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "updated_at": datetime.now().isoformat(),
            "positions": {
                pid: pos.model_dump(mode="json")
                for pid, pos in self._positions.items()
            },
        }

        with open(LEAP_PORTFOLIO_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.debug("leap_state_saved", positions=len(self._positions), path=str(LEAP_PORTFOLIO_PATH))

    def _load_state(self) -> None:
        """Load LEAP positions from JSON file if it exists."""
        if not LEAP_PORTFOLIO_PATH.exists():
            logger.info("leap_no_saved_state", path=str(LEAP_PORTFOLIO_PATH))
            return

        try:
            with open(LEAP_PORTFOLIO_PATH) as f:
                data = json.load(f)

            positions_data = data.get("positions", {})
            for pid, pos_dict in positions_data.items():
                self._positions[pid] = LeapPosition.model_validate(pos_dict)

            logger.info(
                "leap_state_loaded",
                positions=len(self._positions),
                active=sum(1 for p in self._positions.values() if p.active),
            )
        except Exception as e:
            logger.error("leap_state_load_failed", error=str(e), path=str(LEAP_PORTFOLIO_PATH))
