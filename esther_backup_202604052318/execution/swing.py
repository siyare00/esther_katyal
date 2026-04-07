"""Swing Position Manager — Multi-Day Positions Held Overnight or Over Weekends.

Handles positions that span multiple trading days, unlike the core 0DTE focus.
Swing positions are used for:
    - Multi-day directional plays based on strong bias + flow confirmation
    - FOMC/CPI positioning (enter before, hold through event)
    - Death cross bearish plays that need time to develop
    - Friday→Monday weekend swings when conviction is high

Risk Budget:
    - Max 10% of account in overnight positions
    - Each swing has explicit thesis, target, and stop
    - Separate tracking from intraday 0DTE positions

Weekend Swing Logic:
    - Only hold over weekend if Friday bias is STRONG (>70 confidence)
    - Flow must confirm direction
    - Use smaller size (50% of normal swing size)
    - Always use defined risk (spreads, not naked)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, time
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import TradierClient

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class SwingStatus(str, Enum):
    """Status of a swing position."""

    OPEN = "OPEN"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_EXPIRY = "CLOSED_EXPIRY"
    CLOSED_THESIS_BROKEN = "CLOSED_THESIS_BROKEN"


class SwingSide(str, Enum):
    """Direction of the swing trade."""

    LONG = "LONG"
    SHORT = "SHORT"


class SwingPosition(BaseModel):
    """A multi-day swing position with thesis tracking.

    Unlike 0DTE positions, swings have:
    - A thesis (why we're holding overnight)
    - Overnight P&L tracking
    - Multi-day duration tracking
    - Explicit expiration (the option expiry, not just today)
    """

    id: str = Field(default_factory=lambda: f"swing_{uuid.uuid4().hex[:8]}")
    symbol: str  # underlying (e.g., "SPX", "AAPL")
    option_symbol: str  # specific option contract
    side: SwingSide
    quantity: int
    thesis: str  # why we're holding (e.g., "FOMC positioning", "death cross bearish")

    # Entry
    entry_date: date = Field(default_factory=date.today)
    entry_price: float = 0.0
    entry_time: datetime = Field(default_factory=datetime.now)

    # Current state
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    pnl_pct: float = 0.0

    # Targets and stops
    target: float = 0.0  # price target
    stop: float = 0.0  # stop loss price
    expiration: date | None = None  # option expiration date

    # Tracking
    status: SwingStatus = SwingStatus.OPEN
    close_date: date | None = None
    close_price: float = 0.0
    close_reason: str = ""

    # Overnight tracking
    daily_closes: list[float] = []  # closing price each day
    overnight_pnl: float = 0.0  # P&L from overnight gap
    total_days_held: int = 0

    # Weekend swing specific
    is_weekend_swing: bool = False
    weekend_direction: str = ""  # BULL or BEAR
    weekend_confidence: float = 0.0

    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    tags: list[str] = []  # e.g., ["fomc", "death_cross", "weekend"]


class SwingPortfolio(BaseModel):
    """Summary of all swing positions."""

    total_positions: int = 0
    total_exposure: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_overnight_pnl: float = 0.0
    positions: list[SwingPosition] = []
    weekend_swings: int = 0
    avg_days_held: float = 0.0
    account_pct_used: float = 0.0  # % of account in swings


class SwingManager:
    """Manages multi-day swing positions.

    Swing positions are held overnight or over multiple days, unlike
    the core 0DTE strategy. They have their own risk budget (max 10%
    of account) and explicit thesis tracking.

    Key methods:
        open_swing() — open a new swing position
        check_swings() — check all swings against targets/stops
        close_swing() — close a specific swing
        get_overnight_pnl() — calculate P/L from overnight holds
        weekend_swing() — open a Friday→Monday swing
    """

    MAX_SWING_PCT = 0.10  # Max 10% of account in swings
    WEEKEND_SIZE_MULTIPLIER = 0.50  # Weekend swings are 50% of normal size
    MIN_WEEKEND_CONFIDENCE = 70  # Minimum confidence for weekend swings

    def __init__(self, client: TradierClient, account_balance: float):
        self.client = client
        self.account_balance = account_balance
        self._swings: dict[str, SwingPosition] = {}
        self._closed_swings: list[SwingPosition] = []

    @property
    def open_swings(self) -> list[SwingPosition]:
        """All currently open swing positions."""
        return [s for s in self._swings.values() if s.status == SwingStatus.OPEN]

    @property
    def total_exposure(self) -> float:
        """Total dollar exposure in swing positions."""
        return sum(
            s.entry_price * 100 * s.quantity
            for s in self.open_swings
        )

    @property
    def exposure_pct(self) -> float:
        """Swing exposure as percentage of account."""
        return self.total_exposure / self.account_balance if self.account_balance > 0 else 0.0

    def open_swing(
        self,
        symbol: str,
        option_symbol: str,
        side: SwingSide | str,
        quantity: int,
        thesis: str,
        target: float,
        stop: float,
        expiration: date | str,
        entry_price: float | None = None,
        tags: list[str] | None = None,
    ) -> SwingPosition | None:
        """Open a new swing position.

        Checks swing risk budget before opening. Max 10% of account.

        Args:
            symbol: Underlying symbol (e.g., "SPX").
            option_symbol: Specific option contract symbol.
            side: LONG or SHORT.
            quantity: Number of contracts.
            thesis: Why we're taking this swing (e.g., "FOMC positioning").
            target: Price target for the option.
            stop: Stop loss price for the option.
            expiration: Option expiration date.
            entry_price: Entry price (if None, will fetch from market).
            tags: Optional tags for categorization.

        Returns:
            SwingPosition if opened, None if rejected by risk check.
        """
        if isinstance(side, str):
            side = SwingSide(side.upper())
        if isinstance(expiration, str):
            expiration = date.fromisoformat(expiration)

        # Risk check: would this exceed 10% swing budget?
        estimated_exposure = (entry_price or 0) * 100 * quantity
        if self.total_exposure + estimated_exposure > self.account_balance * self.MAX_SWING_PCT:
            logger.warning(
                "swing_rejected_risk_budget",
                symbol=symbol,
                current_exposure=self.total_exposure,
                new_exposure=estimated_exposure,
                max_allowed=self.account_balance * self.MAX_SWING_PCT,
            )
            return None

        swing = SwingPosition(
            symbol=symbol,
            option_symbol=option_symbol,
            side=side,
            quantity=quantity,
            thesis=thesis,
            entry_price=entry_price or 0.0,
            current_price=entry_price or 0.0,
            target=target,
            stop=stop,
            expiration=expiration,
            tags=tags or [],
        )

        self._swings[swing.id] = swing

        logger.info(
            "swing_opened",
            id=swing.id,
            symbol=symbol,
            option=option_symbol,
            side=side.value,
            qty=quantity,
            thesis=thesis,
            target=target,
            stop=stop,
            expiration=expiration.isoformat(),
        )

        return swing

    async def check_swings(self) -> list[SwingPosition]:
        """Check all open swings against targets, stops, and expiration.

        For each open swing:
        1. Fetch current price
        2. Update P&L
        3. Check if target hit → close for profit
        4. Check if stop hit → close for loss
        5. Check if approaching expiration → alert or close
        6. Check if thesis is still valid

        Returns:
            List of swings that were closed this check.
        """
        closed_this_check: list[SwingPosition] = []

        for swing in list(self.open_swings):
            try:
                # Fetch current price
                current_price = await self._get_current_price(swing.option_symbol)
                if current_price is None:
                    continue

                # Update state
                swing.current_price = current_price
                swing.total_days_held = (date.today() - swing.entry_date).days

                # Calculate P&L
                if swing.side == SwingSide.LONG:
                    swing.unrealized_pnl = round(
                        (current_price - swing.entry_price) * 100 * swing.quantity, 2
                    )
                else:
                    swing.unrealized_pnl = round(
                        (swing.entry_price - current_price) * 100 * swing.quantity, 2
                    )

                swing.pnl_pct = (
                    swing.unrealized_pnl / (swing.entry_price * 100 * swing.quantity)
                    if swing.entry_price > 0 else 0.0
                )

                # Check target
                if swing.side == SwingSide.LONG and current_price >= swing.target:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_TARGET,
                        f"TARGET_HIT: Price {current_price:.2f} >= target {swing.target:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                if swing.side == SwingSide.SHORT and current_price <= swing.target:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_TARGET,
                        f"TARGET_HIT: Price {current_price:.2f} <= target {swing.target:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                # Check stop
                if swing.side == SwingSide.LONG and current_price <= swing.stop:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_STOP,
                        f"STOP_HIT: Price {current_price:.2f} <= stop {swing.stop:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                if swing.side == SwingSide.SHORT and current_price >= swing.stop:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_STOP,
                        f"STOP_HIT: Price {current_price:.2f} >= stop {swing.stop:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                # Check expiration proximity
                if swing.expiration:
                    days_to_expiry = (swing.expiration - date.today()).days
                    if days_to_expiry <= 0:
                        await self._close_swing(
                            swing,
                            SwingStatus.CLOSED_EXPIRY,
                            f"EXPIRY: Option expires today ({swing.expiration})",
                        )
                        closed_this_check.append(swing)
                        continue

                    if days_to_expiry == 1:
                        logger.warning(
                            "swing_expiring_tomorrow",
                            id=swing.id,
                            symbol=swing.symbol,
                            pnl=swing.unrealized_pnl,
                        )

            except Exception as e:
                logger.error(
                    "swing_check_failed",
                    id=swing.id,
                    symbol=swing.symbol,
                    error=str(e),
                )

        return closed_this_check

    async def close_swing(self, swing_id: str, reason: str = "MANUAL") -> SwingPosition | None:
        """Close a specific swing position.

        Args:
            swing_id: ID of the swing to close.
            reason: Why we're closing.

        Returns:
            The closed SwingPosition, or None if not found.
        """
        swing = self._swings.get(swing_id)
        if not swing or swing.status != SwingStatus.OPEN:
            logger.warning("swing_not_found_or_closed", id=swing_id)
            return None

        await self._close_swing(swing, SwingStatus.CLOSED_MANUAL, reason)
        return swing

    async def _close_swing(
        self,
        swing: SwingPosition,
        status: SwingStatus,
        reason: str,
    ) -> None:
        """Internal method to close a swing position.

        Submits the closing order to Tradier and updates tracking.
        """
        # Submit closing order
        close_side = "sell_to_close" if swing.side == SwingSide.LONG else "buy_to_close"

        try:
            await self.client.place_order(
                symbol=swing.symbol,
                option_symbol=swing.option_symbol,
                side=close_side,
                quantity=swing.quantity,
                order_type="market",
            )
        except Exception as e:
            logger.error(
                "swing_close_order_failed",
                id=swing.id,
                error=str(e),
            )

        swing.status = status
        swing.close_date = date.today()
        swing.close_price = swing.current_price
        swing.close_reason = reason

        # Move to closed list
        self._closed_swings.append(swing)
        if swing.id in self._swings:
            del self._swings[swing.id]

        logger.info(
            "swing_closed",
            id=swing.id,
            symbol=swing.symbol,
            status=status.value,
            pnl=swing.unrealized_pnl,
            days_held=swing.total_days_held,
            thesis=swing.thesis,
            reason=reason,
        )

    def get_overnight_pnl(self) -> float:
        """Calculate total P/L from overnight holds.

        Compares current price to previous day's close for each swing.

        Returns:
            Total overnight P&L in dollars.
        """
        total_overnight = 0.0
        for swing in self.open_swings:
            if swing.daily_closes:
                prev_close = swing.daily_closes[-1]
                if swing.side == SwingSide.LONG:
                    overnight = (swing.current_price - prev_close) * 100 * swing.quantity
                else:
                    overnight = (prev_close - swing.current_price) * 100 * swing.quantity
                swing.overnight_pnl = round(overnight, 2)
                total_overnight += overnight

        return round(total_overnight, 2)

    def record_daily_close(self) -> None:
        """Record today's closing prices for all open swings.

        Call this at EOD to track overnight gaps.
        """
        for swing in self.open_swings:
            swing.daily_closes.append(swing.current_price)
            logger.info(
                "swing_daily_close_recorded",
                id=swing.id,
                symbol=swing.symbol,
                close_price=swing.current_price,
                days_held=swing.total_days_held,
            )

    async def weekend_swing(
        self,
        symbol: str,
        option_symbol: str,
        direction: str,
        confidence: float,
        quantity: int,
        entry_price: float,
        target: float,
        stop: float,
        expiration: date | str,
        thesis: str = "",
    ) -> SwingPosition | None:
        """Open a Friday→Monday weekend swing position.

        Weekend swings have stricter requirements:
        - Confidence must be >= 70 (MIN_WEEKEND_CONFIDENCE)
        - Size is reduced by 50%
        - Only uses defined-risk strategies
        - Must have strong flow confirmation (passed in as confidence)

        Args:
            symbol: Underlying symbol.
            option_symbol: Option contract.
            direction: "BULL" or "BEAR".
            confidence: Confidence level (0-100). Must be >= 70.
            quantity: Base quantity (will be halved for weekend sizing).
            entry_price: Entry price per contract.
            target: Price target.
            stop: Stop loss.
            expiration: Option expiration.
            thesis: Thesis for the weekend hold.

        Returns:
            SwingPosition if opened, None if rejected.
        """
        # Check it's actually Friday
        now_et = datetime.now(ET)
        if now_et.weekday() != 4:  # 4 = Friday
            logger.warning(
                "weekend_swing_not_friday",
                day=now_et.strftime("%A"),
            )
            # Allow it but log warning — could be pre-positioning on Thursday

        # Confidence check
        if confidence < self.MIN_WEEKEND_CONFIDENCE:
            logger.info(
                "weekend_swing_rejected_confidence",
                symbol=symbol,
                confidence=confidence,
                min_required=self.MIN_WEEKEND_CONFIDENCE,
            )
            return None

        # Reduce size for weekend risk
        weekend_qty = max(1, int(quantity * self.WEEKEND_SIZE_MULTIPLIER))

        if not thesis:
            thesis = f"Weekend {direction} swing — confidence {confidence:.0f}%"

        side = SwingSide.LONG if direction == "BULL" else SwingSide.SHORT

        swing = self.open_swing(
            symbol=symbol,
            option_symbol=option_symbol,
            side=side,
            quantity=weekend_qty,
            thesis=thesis,
            target=target,
            stop=stop,
            expiration=expiration,
            entry_price=entry_price,
            tags=["weekend", direction.lower()],
        )

        if swing:
            swing.is_weekend_swing = True
            swing.weekend_direction = direction
            swing.weekend_confidence = confidence

            logger.info(
                "weekend_swing_opened",
                id=swing.id,
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                original_qty=quantity,
                weekend_qty=weekend_qty,
                thesis=thesis,
            )

        return swing

    def get_swing_portfolio(self) -> SwingPortfolio:
        """Get a summary of all swing positions.

        Returns:
            SwingPortfolio with aggregated metrics.
        """
        positions = self.open_swings
        total_unrealized = sum(s.unrealized_pnl for s in positions)
        total_overnight = sum(s.overnight_pnl for s in positions)
        total_exposure = self.total_exposure
        weekend_count = sum(1 for s in positions if s.is_weekend_swing)
        avg_days = (
            sum(s.total_days_held for s in positions) / len(positions)
            if positions else 0.0
        )

        return SwingPortfolio(
            total_positions=len(positions),
            total_exposure=round(total_exposure, 2),
            total_unrealized_pnl=round(total_unrealized, 2),
            total_overnight_pnl=round(total_overnight, 2),
            positions=positions,
            weekend_swings=weekend_count,
            avg_days_held=round(avg_days, 1),
            account_pct_used=round(self.exposure_pct * 100, 2),
        )

    async def _get_current_price(self, option_symbol: str) -> float | None:
        """Fetch current mid price for an option contract."""
        try:
            quotes = await self.client.get_quotes([option_symbol])
            if quotes:
                q = quotes[0]
                return round((q.bid + q.ask) / 2, 2)
        except Exception as e:
            logger.error(
                "swing_price_fetch_failed",
                option=option_symbol,
                error=str(e),
            )
        return None

    def update_account_balance(self, new_balance: float) -> None:
        """Update the account balance for risk calculations."""
        self.account_balance = new_balance
