"""Position Manager — Track, Monitor, and Manage Open Positions.

Handles all post-entry position management:
    - Track entry price, targets, and stops
    - P1-P3: profit target at 75% of credit (was 50%), tiered stop losses
    - P4: trailing stop (20% initial, tighten to 10% after 50% gain)
    - Power hour management: different rules after 3:00 PM
    - Time-based exits: close P1-P3 at 3:45 PM ET
    - Expire worthless tracking: flag near-expiry OTM spreads to let expire
    - Multi-day swing position tracking with overnight P&L
    - Scale-in tracking with average cost calculation
    - Real-time Greeks monitoring

Tiered Stop Loss System:
    Position is split into 3 tranches with different stop levels:
    - Tranche 1 (1/3): Tightest stop (e.g., $3.40) — first to exit on dips
    - Tranche 2 (1/3): Medium stop (e.g., $2.90) — exits on deeper moves
    - Tranche 3 (1/3): Widest stop (e.g., $2.30) — only exits on worst case
    This way a brief dip only stops out 1/3, not the whole position.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import TradierClient
from esther.data.alpaca import AlpacaClient
from esther.execution.pillars import SpreadOrder, OrderSide, check_expire_worthless, OptionType

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_PROFIT = "CLOSED_PROFIT"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_TIME = "CLOSED_TIME"
    CLOSED_FORCE = "CLOSED_FORCE"
    CLOSED_TRAIL = "CLOSED_TRAIL"
    EXPIRED_WORTHLESS = "EXPIRED_WORTHLESS"  # Let expire for max profit
    CLOSED_TIERED_STOP = "CLOSED_TIERED_STOP"  # Partial stop via tiered system


class TrancheStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"


class Tranche(BaseModel):
    """A single tranche within a tiered stop system.

    Each position is split into 3 tranches with different stop levels.
    When a tranche's stop is hit, only that portion of the position is closed.
    """

    id: int  # 1, 2, or 3
    quantity: int  # contracts in this tranche
    stop_price: float  # stop level for this tranche
    status: TrancheStatus = TrancheStatus.ACTIVE
    stopped_at: datetime | None = None
    stopped_value: float = 0.0


class ScaleInEntry(BaseModel):
    """Record of a scale-in (adding to an existing position)."""

    added_at: datetime = Field(default_factory=datetime.now)
    quantity: int
    price: float  # price at which the scale-in was made
    order_id: str = ""


class Position(BaseModel):
    """A tracked open position with tiered stops and scale-in tracking."""

    id: str  # unique position ID
    symbol: str
    pillar: int
    order_id: str = ""
    legs: list[dict[str, Any]] = []
    quantity: int = 1

    # Entry
    entry_price: float = 0.0  # credit received (P1-P3) or debit paid (P4)
    entry_time: datetime = Field(default_factory=datetime.now)
    expiration: str = ""

    # Targets — 75% profit target (was 50%)
    profit_target: float = 0.0  # price to close at for profit
    stop_loss: float = 0.0  # overall stop (worst case, all tranches)

    # Tiered stop system
    tranches: list[Tranche] = []
    active_quantity: int = 0  # contracts still open (not stopped out)

    # P4 trailing stop
    trail_pct: float = 0.0
    highest_value: float = 0.0  # high water mark for trailing stop
    trail_tightened: bool = False

    # Current state
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    close_time: datetime | None = None
    close_reason: str = ""

    # Expire worthless tracking
    expire_worthless_flagged: bool = False
    expire_worthless_checked_at: datetime | None = None

    # Scale-in tracking
    scale_ins: list[ScaleInEntry] = []
    average_entry_price: float = 0.0  # weighted average including scale-ins
    total_invested_quantity: int = 0  # total contracts including scale-ins

    # Multi-day swing tracking
    is_swing: bool = False  # True if this is a multi-day position
    overnight_pnl: float = 0.0  # P&L from overnight holds
    previous_close_value: float = 0.0  # value at previous day's close
    swing_days: int = 0  # number of days position has been open
    swing_thesis: str = ""  # reason for holding overnight

    # Runner mode (P4 directional — let 30% ride after first target)
    runner_active: bool = False
    runner_quantity: int = 0
    runner_trail_pct: float = 0.20  # wider trail for runners (2x normal)
    initial_quantity_closed: int = 0

    # Power hour flag
    is_power_hour_entry: bool = False

    # Greeks
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0

    # Metadata
    tier: str = ""
    direction: str = ""  # BULL or BEAR
    original_direction: str = ""  # BULL or BEAR before inversion
    debate_confidence: int = 0


class PositionManager:
    """Manages all open positions with tiered stops, expire worthless, and swing tracking.

    Position Management Rules by Pillar:

    P1 (Iron Condors):
        - Profit: Close at 75% of credit received (was 50%)
        - Stop: Tiered — 3 tranches at different stop levels
        - Time: Force close at 3:45 PM ET
        - Expire worthless: If >80% OTM with <15 min, let expire

    P2 (Bear Call Spreads):
        - Same as P1

    P3 (Bull Put Spreads):
        - Same as P1

    P4 (0DTE Directional Scalps):
        - Trailing stop: starts at 20% from peak
        - Tightens to 10% after position gains 50%
        - Power hour mode: wider stops (25%), faster targets after 3:00 PM
        - No time exit (0DTE, expires end of day anyway)
    """

    # Tradier side → Alpaca side mapping
    _SIDE_MAP = {
        "buy_to_open": "buy",
        "sell_to_open": "sell",
        "buy_to_close": "buy",
        "sell_to_close": "sell",
    }

    def __init__(self, client: TradierClient | AlpacaClient):
        self.client = client
        self._cfg = config()
        self._positions: dict[str, Position] = {}
        self._closed_positions: list[Position] = []
        self._next_id = 1
        self._swing_positions: dict[str, Position] = {}  # Separate tracking for swings

    async def _close_leg(
        self, symbol: str, option_symbol: str, side: str, quantity: int
    ) -> None:
        """Close a single option leg via the active broker."""
        if isinstance(self.client, AlpacaClient):
            await self.client.place_order(
                symbol=option_symbol,
                side=self._SIDE_MAP.get(side, "buy"),
                qty=quantity,
                order_type="market",
            )
        else:
            await self.client.place_order(
                symbol=symbol,
                option_symbol=option_symbol,
                side=side,
                quantity=quantity,
                order_type="market",
            )

    @property
    def open_positions(self) -> list[Position]:
        """All currently open positions (including swings)."""
        return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]

    @property
    def open_day_positions(self) -> list[Position]:
        """Open positions that are NOT swings (0DTE only)."""
        return [p for p in self.open_positions if not p.is_swing]

    @property
    def open_swing_positions(self) -> list[Position]:
        """Open positions that ARE swings (multi-day)."""
        return [p for p in self.open_positions if p.is_swing]

    @property
    def closed_positions(self) -> list[Position]:
        """All closed positions (today)."""
        return list(self._closed_positions)

    def open_position(
        self,
        order: SpreadOrder,
        order_id: str = "",
        direction: str = "",
        original_direction: str = "",
        tier: str = "",
        confidence: int = 0,
        is_swing: bool = False,
        swing_thesis: str = "",
    ) -> Position:
        """Register a new position after order fill with tiered stops.

        Args:
            order: The spread order that was filled.
            order_id: Tradier order ID.
            direction: BULL or BEAR.
            tier: Ticker tier (tier1, tier2, tier3).
            confidence: Debate confidence (0-100).
            is_swing: Whether this is a multi-day swing position.
            swing_thesis: Reason for holding overnight (if swing).

        Returns:
            The new Position object.
        """
        pos_id = f"pos_{self._next_id:04d}"
        self._next_id += 1

        entry_price = order.net_price
        pillar_cfg = self._get_pillar_config(order.pillar)
        is_power_hour = self._is_power_hour()

        # Set profit target and stop loss based on pillar
        # Profit target is now 75% (was 50%)
        if order.pillar in (1, 2, 3):
            # Credit spreads: profit when value decreases
            # 75% profit target: close when value drops to 25% of entry
            profit_target = round(entry_price * (1 - 0.75), 2)
            # Overall stop at 2x credit (worst case — all tranches stopped)
            stop_loss = round(entry_price * pillar_cfg.get("stop_loss_multiplier", 2.0), 2)
            trail_pct = 0.0

            # Build tiered stops
            tranches = self._build_tiered_stops(
                total_quantity=order.quantity,
                entry_price=entry_price,
                stop_multiplier=pillar_cfg.get("stop_loss_multiplier", 2.0),
            )
        else:
            # P4 directional: profit when value increases
            profit_target = 0.0  # No fixed target, trailing stop only
            stop_loss = 0.0  # Trailing stop handles this
            tranches = []

            # Power hour: wider initial trail for momentum capture
            if is_power_hour:
                trail_pct = 0.25  # 25% trail during power hour (vs normal 20%)
            else:
                trail_pct = self._cfg.pillars.p4.initial_trail_pct

        position = Position(
            id=pos_id,
            symbol=order.symbol,
            pillar=order.pillar,
            order_id=order_id,
            legs=[leg.model_dump() for leg in order.legs],
            quantity=order.quantity,
            entry_price=entry_price,
            expiration=order.expiration,
            profit_target=profit_target,
            stop_loss=stop_loss,
            trail_pct=trail_pct,
            highest_value=entry_price if order.pillar == 4 else 0.0,
            current_value=entry_price,
            tier=tier,
            direction=direction,
            original_direction=original_direction,
            debate_confidence=confidence,
            tranches=tranches,
            active_quantity=order.quantity,
            average_entry_price=entry_price,
            total_invested_quantity=order.quantity,
            is_swing=is_swing,
            swing_thesis=swing_thesis,
            is_power_hour_entry=is_power_hour,
        )

        self._positions[pos_id] = position

        logger.info(
            "position_opened",
            id=pos_id,
            symbol=order.symbol,
            pillar=order.pillar,
            entry=entry_price,
            target=profit_target,
            stop=stop_loss,
            tranches=len(tranches),
            is_swing=is_swing,
            power_hour=is_power_hour,
        )

        return position

    def _build_tiered_stops(
        self,
        total_quantity: int,
        entry_price: float,
        stop_multiplier: float,
    ) -> list[Tranche]:
        """Build 3 tranches with graduated stop levels.

        Tranche 1 (1/3 of position): Tightest stop — first line of defense
        Tranche 2 (1/3 of position): Medium stop — protects from moderate moves
        Tranche 3 (1/3 of position): Widest stop — only stops on worst case

        For a $1.70 credit with 2x stop multiplier ($3.40):
        - Tranche 1: $3.40 stop (tightest, 2.0x)
        - Tranche 2: $2.90 stop (medium, ~1.7x)
        - Tranche 3: $2.30 stop (widest, ~1.35x)

        Wait, for credit spreads the stop is when VALUE RISES above the stop.
        So tightest stop = lowest stop price (triggers first on any adverse move).

        Actually, let me re-think. For credit spreads:
        - Entry credit = $1.70
        - We lose when spread value INCREASES
        - Stop loss = 2x credit = $3.40 means we close when value reaches $3.40
        - Tightest stop = stops out first = LOWEST stop price
        - Widest stop = tolerates more pain = HIGHEST stop price

        Tranche 1 (tightest): stop at 1.7x entry (exits first)
        Tranche 2 (medium): stop at 2.0x entry
        Tranche 3 (widest): stop at 2.5x entry (most tolerance)

        Args:
            total_quantity: Total contracts to split across tranches.
            entry_price: Entry credit/debit price.
            stop_multiplier: Base stop loss multiplier from config.

        Returns:
            List of 3 Tranche objects.
        """
        if total_quantity < 3:
            # Can't split less than 3 contracts into 3 tranches
            # Use single tranche with standard stop
            return [
                Tranche(
                    id=1,
                    quantity=total_quantity,
                    stop_price=round(entry_price * stop_multiplier, 2),
                ),
            ]

        # Split into 3 roughly equal tranches
        base_qty = total_quantity // 3
        remainder = total_quantity % 3

        qty_1 = base_qty + (1 if remainder > 0 else 0)
        qty_2 = base_qty + (1 if remainder > 1 else 0)
        qty_3 = base_qty

        # Graduated stop levels
        # Tranche 1: tightest (exits first on adverse moves)
        stop_1 = round(entry_price * (stop_multiplier * 0.85), 2)  # ~1.7x for 2x base
        # Tranche 2: medium
        stop_2 = round(entry_price * stop_multiplier, 2)  # 2.0x (standard)
        # Tranche 3: widest (most tolerance)
        stop_3 = round(entry_price * (stop_multiplier * 1.25), 2)  # ~2.5x for 2x base

        return [
            Tranche(id=1, quantity=qty_1, stop_price=stop_1),
            Tranche(id=2, quantity=qty_2, stop_price=stop_2),
            Tranche(id=3, quantity=qty_3, stop_price=stop_3),
        ]

    def record_scale_in(
        self,
        position_id: str,
        additional_quantity: int,
        scale_in_price: float,
        order_id: str = "",
    ) -> Position | None:
        """Record a scale-in (adding to an existing position).

        Updates the average entry price and total quantity. Only call this
        AFTER the scale-in order has been filled.

        Args:
            position_id: ID of the existing position.
            additional_quantity: Number of new contracts added.
            scale_in_price: Price of the new contracts.
            order_id: Order ID for the scale-in fill.

        Returns:
            Updated Position, or None if position not found.
        """
        pos = self._positions.get(position_id)
        if not pos:
            logger.warning("scale_in_position_not_found", id=position_id)
            return None

        # Record the scale-in
        entry = ScaleInEntry(
            quantity=additional_quantity,
            price=scale_in_price,
            order_id=order_id,
        )
        pos.scale_ins.append(entry)

        # Update average entry price (weighted average)
        old_total = pos.average_entry_price * pos.total_invested_quantity
        new_total = scale_in_price * additional_quantity
        pos.total_invested_quantity += additional_quantity
        pos.average_entry_price = round(
            (old_total + new_total) / pos.total_invested_quantity, 4
        )

        # Update quantity
        pos.quantity += additional_quantity
        pos.active_quantity += additional_quantity

        # Rebuild tiered stops with new total quantity
        if pos.pillar in (1, 2, 3) and pos.active_quantity >= 3:
            pillar_cfg = self._get_pillar_config(pos.pillar)
            stop_mult = pillar_cfg.get("stop_loss_multiplier", 2.0)
            pos.tranches = self._build_tiered_stops(
                total_quantity=pos.active_quantity,
                entry_price=pos.average_entry_price,
                stop_multiplier=stop_mult,
            )

        logger.info(
            "scale_in_recorded",
            id=position_id,
            added_qty=additional_quantity,
            price=scale_in_price,
            new_avg_price=pos.average_entry_price,
            total_qty=pos.total_invested_quantity,
        )

        return pos

    async def update_positions(self) -> list[Position]:
        """Update all open positions with current prices and check exits.

        This is called every scan cycle. For each position:
        1. Fetch current option prices
        2. Update P&L and Greeks
        3. Check expire worthless eligibility
        4. Check tiered stops (per-tranche)
        5. Check profit target, trailing stop, and time exit
        6. Apply power hour management rules after 3:00 PM
        7. Close positions that hit exit conditions

        Returns:
            List of positions that were closed this cycle.
        """
        closed_this_cycle: list[Position] = []
        is_power_hour = self._is_power_hour()

        for pos in self.open_positions:
            try:
                # Update current value from Tradier
                await self._update_position_value(pos)

                # Check expire worthless FIRST (before any close logic)
                if pos.pillar in (1, 2, 3) and not pos.expire_worthless_flagged:
                    self._check_expire_worthless(pos)

                # If flagged for expire worthless, skip normal close logic
                if pos.expire_worthless_flagged:
                    if _minutes_to_close() <= 0:
                        # Market closed — position expired worthless!
                        pos.status = PositionStatus.EXPIRED_WORTHLESS
                        pos.close_time = datetime.now()
                        pos.close_reason = "EXPIRED_WORTHLESS: Let expire for max profit"
                        pos.unrealized_pnl = pos.entry_price * 100 * pos.quantity  # Full credit = profit
                        self._closed_positions.append(pos)
                        del self._positions[pos.id]
                        closed_this_cycle.append(pos)
                        logger.info(
                            "position_expired_worthless",
                            id=pos.id,
                            symbol=pos.symbol,
                            pnl=pos.unrealized_pnl,
                        )
                    continue

                # Check tiered stops for P1-P3
                if pos.pillar in (1, 2, 3) and pos.tranches:
                    tranche_closes = self._check_tiered_stops(pos)
                    if tranche_closes:
                        for tranche in tranche_closes:
                            await self._close_tranche(pos, tranche)

                        # If all tranches are stopped, close the whole position
                        if all(t.status == TrancheStatus.STOPPED for t in pos.tranches):
                            pos.status = PositionStatus.CLOSED_TIERED_STOP
                            pos.close_time = datetime.now()
                            pos.close_reason = "ALL_TRANCHES_STOPPED"
                            self._closed_positions.append(pos)
                            if pos.id in self._positions:
                                del self._positions[pos.id]
                            closed_this_cycle.append(pos)
                            continue

                # Apply power hour management if applicable
                if is_power_hour and pos.pillar == 4:
                    self._apply_power_hour_rules(pos)

                # Check exit conditions (standard)
                close_reason = self._check_exits(pos, is_power_hour)

                if close_reason:
                    await self._close_position(pos, close_reason)
                    closed_this_cycle.append(pos)

            except Exception as e:
                logger.error("position_update_failed", id=pos.id, error=str(e))

        # Update swing position day counts
        self._update_swing_tracking()

        return closed_this_cycle

    def _check_expire_worthless(self, pos: Position) -> None:
        """Check if a credit spread should be flagged to expire worthless.

        Conditions:
        - Spread is >80% OTM (>2 standard deviations from current price)
        - Less than 15 minutes to market close
        - Position is profitable

        This saves a day trade and captures maximum profit.
        """
        if pos.pillar not in (1, 2, 3):
            return

        minutes_left = _minutes_to_close()
        if minutes_left > 15:
            return

        # Find the short strike and determine option type
        short_legs = [
            leg for leg in pos.legs
            if leg.get("side") == OrderSide.SELL_TO_OPEN.value
        ]
        if not short_legs:
            return

        # Check if the spread value is near zero (>80% of credit has decayed)
        if pos.entry_price > 0 and pos.current_value > 0:
            decay_pct = 1 - (abs(pos.current_value) / pos.entry_price)
            if decay_pct >= 0.80:
                pos.expire_worthless_flagged = True
                pos.expire_worthless_checked_at = datetime.now()
                logger.info(
                    "expire_worthless_flagged",
                    id=pos.id,
                    symbol=pos.symbol,
                    decay_pct=f"{decay_pct:.1%}",
                    minutes_left=f"{minutes_left:.0f}",
                    current_value=pos.current_value,
                    entry_credit=pos.entry_price,
                )

    def _check_tiered_stops(self, pos: Position) -> list[Tranche]:
        """Check which tranches have hit their stop levels.

        For credit spreads, a stop is triggered when the spread value
        RISES above the tranche's stop price.

        Returns:
            List of tranches that need to be closed.
        """
        triggered = []
        for tranche in pos.tranches:
            if tranche.status != TrancheStatus.ACTIVE:
                continue

            if pos.current_value >= tranche.stop_price:
                triggered.append(tranche)
                logger.info(
                    "tranche_stop_triggered",
                    id=pos.id,
                    tranche=tranche.id,
                    stop=tranche.stop_price,
                    current=pos.current_value,
                    qty=tranche.quantity,
                )

        return triggered

    async def _close_tranche(self, pos: Position, tranche: Tranche) -> None:
        """Close a single tranche of a position.

        Only closes the quantity associated with this tranche, not the whole position.
        """
        tranche.status = TrancheStatus.STOPPED
        tranche.stopped_at = datetime.now()
        tranche.stopped_value = pos.current_value

        # Update active quantity
        pos.active_quantity -= tranche.quantity

        # Submit closing orders for this tranche's quantity
        for leg in pos.legs:
            close_side = (
                OrderSide.BUY_TO_CLOSE.value
                if leg.get("side") == OrderSide.SELL_TO_OPEN.value
                else OrderSide.SELL_TO_CLOSE.value
            )

            try:
                await self._close_leg(
                    symbol=pos.symbol,
                    option_symbol=leg["option_symbol"],
                    side=close_side,
                    quantity=tranche.quantity,
                )
            except Exception as e:
                logger.error(
                    "tranche_close_failed",
                    id=pos.id,
                    tranche=tranche.id,
                    leg=leg["option_symbol"],
                    error=str(e),
                )

        logger.info(
            "tranche_closed",
            id=pos.id,
            tranche=tranche.id,
            qty_closed=tranche.quantity,
            remaining_qty=pos.active_quantity,
            stop_price=tranche.stop_price,
        )

    def _apply_power_hour_rules(self, pos: Position) -> None:
        """Apply power hour management rules for P4 positions after 3:00 PM.

        Power hour changes:
        - Wider trailing stop (25% vs 20%) to let momentum plays breathe
        - Faster profit taking: if >30% profit, tighten trail to 15% (vs waiting for 50%)
        - More aggressive on high-delta moves
        """
        if pos.pillar != 4:
            return

        # Wider trailing stop during power hour
        if not pos.is_power_hour_entry and pos.trail_pct < 0.25:
            pos.trail_pct = 0.25
            logger.info("power_hour_widened_trail", id=pos.id, new_trail=0.25)

        # Faster profit taking: tighten trail after 30% gain (vs normal 50%)
        if pos.entry_price > 0 and pos.current_value > 0:
            gain_pct = (pos.current_value - pos.entry_price) / pos.entry_price
            if gain_pct >= 0.30 and not pos.trail_tightened:
                pos.trail_pct = 0.15  # Tighter trail to lock in power hour profits
                pos.trail_tightened = True
                logger.info(
                    "power_hour_tightened_trail",
                    id=pos.id,
                    gain_pct=f"{gain_pct:.1%}",
                    new_trail=0.15,
                )

    def _update_swing_tracking(self) -> None:
        """Update day counts and overnight P&L for swing positions."""
        now_et = datetime.now(ET)

        for pos in self.open_swing_positions:
            # Update swing days
            entry_date = pos.entry_time.date() if pos.entry_time.tzinfo is None else pos.entry_time.astimezone(ET).date()
            pos.swing_days = (now_et.date() - entry_date).days

            # Calculate overnight P&L if we have a previous close value
            if pos.previous_close_value > 0:
                if pos.pillar in (1, 2, 3):
                    pos.overnight_pnl = round(
                        (pos.previous_close_value - pos.current_value) * 100 * pos.active_quantity, 2
                    )
                else:
                    pos.overnight_pnl = round(
                        (pos.current_value - pos.previous_close_value) * 100 * pos.active_quantity, 2
                    )

    def record_day_close_values(self) -> None:
        """Record current values as previous close for overnight P&L tracking.

        Call this at EOD for swing positions that will be held overnight.
        """
        for pos in self.open_swing_positions:
            pos.previous_close_value = pos.current_value
            logger.info(
                "swing_close_value_recorded",
                id=pos.id,
                symbol=pos.symbol,
                close_value=pos.current_value,
                swing_days=pos.swing_days,
            )

    def get_overnight_pnl(self) -> float:
        """Get total overnight P&L across all swing positions."""
        return sum(pos.overnight_pnl for pos in self.open_swing_positions)

    async def _update_position_value(self, pos: Position) -> None:
        """Fetch current prices and update position value + Greeks."""
        # Get quotes for all legs
        leg_symbols = [leg["option_symbol"] for leg in pos.legs]

        try:
            quotes = await self.client.get_quotes(leg_symbols)
        except Exception:
            return  # Skip update if quotes fail

        total_value = 0.0
        total_delta = 0.0
        total_theta = 0.0
        total_gamma = 0.0
        total_vega = 0.0

        for leg in pos.legs:
            quote = next((q for q in quotes if q.symbol == leg["option_symbol"]), None)
            if not quote:
                continue

            mid = (quote.bid + quote.ask) / 2

            # Guard: Alpaca paper can return bid=0/ask=0 temporarily.
            # A zero mid on a position we just entered is bad data, not a real price.
            # Skip the update entirely to avoid false trailing-stop triggers.
            if mid <= 0 and pos.entry_price > 0:
                logger.warning(
                    "stale_quote_skipped",
                    id=pos.id,
                    symbol=leg["option_symbol"],
                    bid=quote.bid,
                    ask=quote.ask,
                )
                return  # Abort — keep previous current_value

            is_short = leg["side"] in ("sell_to_open", "sell_to_close")
            sign = -1 if is_short else 1
            ratio = leg["quantity"] / pos.quantity if pos.quantity > 0 else 1

            total_value += mid * sign * ratio

            # Aggregate Greeks if available
            if hasattr(quote, 'greeks') and quote.greeks:
                total_delta += quote.greeks.delta * sign * ratio
                total_theta += quote.greeks.theta * sign * ratio
                total_gamma += quote.greeks.gamma * sign * ratio
                total_vega += quote.greeks.vega * sign * ratio

        # Normalize credit spreads to be positive values for easier logic
        # If it's a credit spread (P1-P3), the natural total_value is negative (short > long).
        # We invert it so a credit spread is worth +X, tracking its decay.
        if pos.pillar in (1, 2, 3):
            total_value = -total_value

        pos.current_value = round(total_value, 2)
        pos.delta = round(total_delta, 4)
        pos.theta = round(total_theta, 4)
        pos.gamma = round(total_gamma, 4)
        pos.vega = round(total_vega, 4)

        # Calculate unrealized P&L using active quantity
        active_qty = pos.active_quantity if pos.active_quantity > 0 else pos.quantity
        if pos.pillar in (1, 2, 3):
            # Credit spread: entered at credit, want value to decrease
            pos.unrealized_pnl = round(
                (pos.average_entry_price - pos.current_value) * 100 * active_qty, 2
            )
        else:
            # Debit (P4): entered at debit, want value to increase
            pos.unrealized_pnl = round(
                (pos.current_value - pos.average_entry_price) * 100 * active_qty, 2
            )

        # Add realized P&L from stopped tranches
        for tranche in pos.tranches:
            if tranche.status == TrancheStatus.STOPPED:
                tranche_pnl = (pos.average_entry_price - tranche.stopped_value) * 100 * tranche.quantity
                pos.unrealized_pnl += round(tranche_pnl, 2)

        # Update trailing stop high water mark for P4
        if pos.pillar == 4 and pos.current_value > pos.highest_value:
            pos.highest_value = pos.current_value

            # Check if we should tighten the trailing stop
            gain_pct = (pos.current_value - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            if gain_pct >= self._cfg.pillars.p4.tighten_after_gain_pct and not pos.trail_tightened:
                pos.trail_pct = self._cfg.pillars.p4.tight_trail_pct
                pos.trail_tightened = True
                logger.info("trailing_stop_tightened", id=pos.id, new_trail=pos.trail_pct)

    def _check_exits(self, pos: Position, is_power_hour: bool = False) -> str:
        """Check all exit conditions for a position.

        Args:
            pos: The position to check.
            is_power_hour: Whether we're in power hour (3:00-3:45 PM).

        Returns:
            Close reason string, or empty string if no exit triggered.
        """
        # Check P1-P3 profit target (75% of credit)
        if pos.pillar in (1, 2, 3):
            if pos.current_value <= pos.profit_target and pos.profit_target > 0:
                return f"PROFIT_TARGET_75PCT: Value {pos.current_value:.2f} <= target {pos.profit_target:.2f}"

            # Overall stop (worst case — should be caught by tiered stops first)
            if not pos.tranches and pos.current_value >= pos.stop_loss and pos.stop_loss > 0:
                return f"STOP_LOSS: Value {pos.current_value:.2f} >= stop {pos.stop_loss:.2f}"

        # Check P4 trailing stop
        if pos.pillar == 4 and pos.highest_value > 0 and pos.trail_pct > 0:
            trail_level = pos.highest_value * (1 - pos.trail_pct)
            if pos.current_value <= trail_level:
                return (
                    f"TRAILING_STOP: Value {pos.current_value:.2f} <= "
                    f"trail {trail_level:.2f} (high: {pos.highest_value:.2f}, "
                    f"trail%: {pos.trail_pct:.0%})"
                )

        # Time-based exit for P1-P3 (but NOT swing positions)
        if pos.pillar in (1, 2, 3) and not pos.is_swing:
            now = datetime.now(ET)
            eod_minutes = self._cfg.positions.eod_close_minutes_before if hasattr(self._cfg, 'positions') else 15
            eod_time = time(15, 45)
            if now.time() >= eod_time:
                return f"TIME_EXIT: Market closing in {eod_minutes} min"

        return ""

    async def _close_position(self, pos: Position, reason: str) -> None:
        """Close a position by submitting closing orders.

        Only closes remaining active quantity (accounts for stopped tranches).

        Args:
            pos: The position to close.
            reason: Why we're closing.
        """
        logger.info(
            "closing_position",
            id=pos.id,
            symbol=pos.symbol,
            reason=reason,
            pnl=pos.unrealized_pnl,
            active_qty=pos.active_quantity,
        )

        # Build closing order legs — only close active quantity
        close_qty = pos.active_quantity if pos.active_quantity > 0 else pos.quantity

        for leg in pos.legs:
            close_side = (
                OrderSide.BUY_TO_CLOSE.value
                if leg["side"] == OrderSide.SELL_TO_OPEN.value
                else OrderSide.SELL_TO_CLOSE.value
            )

            try:
                await self._close_leg(
                    symbol=pos.symbol,
                    option_symbol=leg["option_symbol"],
                    side=close_side,
                    quantity=close_qty,
                )
            except Exception as e:
                logger.error(
                    "close_order_failed",
                    id=pos.id,
                    leg=leg["option_symbol"],
                    error=str(e),
                )

        # Update position status
        if "PROFIT" in reason:
            pos.status = PositionStatus.CLOSED_PROFIT
        elif "STOP" in reason:
            pos.status = PositionStatus.CLOSED_STOP
        elif "TRAIL" in reason:
            pos.status = PositionStatus.CLOSED_TRAIL
        elif "TIME" in reason:
            pos.status = PositionStatus.CLOSED_TIME
        else:
            pos.status = PositionStatus.CLOSED_FORCE

        pos.close_time = datetime.now()
        pos.close_reason = reason

        # Move to closed list
        self._closed_positions.append(pos)
        if pos.id in self._positions:
            del self._positions[pos.id]

        logger.info(
            "position_closed",
            id=pos.id,
            symbol=pos.symbol,
            status=pos.status.value,
            pnl=pos.unrealized_pnl,
        )

    # ── Runner Mode (P4 Directional — Lever 2: Let Winners Run) ────

    def should_activate_runner(self, pos: Position, current_price: float) -> bool:
        """Check if a P4 position should activate runner mode.

        From @SuperLuckeee's Lever 2: "Don't sell too early. Let winners reach target."

        Runner mode activates when:
        - Position is P4 (directional scalp)
        - Runner is not already active
        - Position has gained 100%+ from entry (doubled)
        - At least 2 contracts remain

        When activated: close 70% at first target, let 30% ride with
        a wider trailing stop (2x normal) for potential 300-1000% returns.

        Args:
            pos: The position to check.
            current_price: Current option price.

        Returns:
            True if runner mode should be activated.
        """
        if pos.pillar != 4:
            return False
        if pos.runner_active:
            return False
        if pos.active_quantity < 2:
            return False  # Need at least 2 contracts to split

        # Check if position has doubled (100%+ gain)
        if pos.entry_price > 0:
            gain_pct = (current_price - pos.entry_price) / pos.entry_price
            return gain_pct >= 1.0  # 100% gain = doubled

        return False

    def activate_runner(self, position_id: str) -> bool:
        """Activate runner mode — close 70%, let 30% ride with wide trail.

        From @SuperLuckeee: "Added into strength and built this position uppp"
        — they scale OUT 70% at first target and let 30% run for the big move.

        This is how $1.50 → $28 (1,767%) happens. Without runners, you sell
        at $3 and miss the 10x.

        Args:
            position_id: The position to activate runner on.

        Returns:
            True if runner was activated, False if not eligible.
        """
        pos = self._positions.get(position_id)
        if pos is None:
            return False

        if pos.runner_active or pos.pillar != 4:
            return False

        total_qty = pos.active_quantity
        if total_qty < 2:
            return False

        # Close 70%, keep 30% as runner
        close_qty = max(1, int(total_qty * 0.70))
        runner_qty = total_qty - close_qty

        pos.initial_quantity_closed = close_qty
        pos.runner_active = True
        pos.runner_quantity = runner_qty
        pos.active_quantity = runner_qty  # Only runners remain

        # Widen the trailing stop for runners (2x normal)
        normal_trail = self._cfg.pillars.p4.tight_trail_pct if pos.trail_tightened else self._cfg.pillars.p4.initial_trail_pct
        pos.runner_trail_pct = normal_trail * 2.0
        pos.trail_pct = pos.runner_trail_pct  # Apply wider trail immediately

        logger.info(
            "runner_activated",
            position_id=position_id,
            symbol=pos.symbol,
            total_qty=total_qty,
            closed_qty=close_qty,
            runner_qty=runner_qty,
            runner_trail=f"{pos.runner_trail_pct:.0%}",
            entry_price=pos.entry_price,
            current_value=pos.current_value,
        )

        return True

    async def force_close_all(self, reason: str = "FORCE_CLOSE") -> list[Position]:
        """Emergency: close ALL open positions immediately.

        Used by Black Swan detector when RED is triggered,
        or by Risk Manager when daily loss cap is hit.
        Does NOT close positions flagged for expire worthless.

        Returns:
            List of all closed positions.
        """
        logger.warning("force_closing_all", count=len(self.open_positions), reason=reason)

        closed = []
        for pos in list(self.open_positions):
            # Don't force-close expire worthless positions (unless BLACK_SWAN)
            if pos.expire_worthless_flagged and "BLACK_SWAN" not in reason:
                logger.info(
                    "skipping_expire_worthless",
                    id=pos.id,
                    symbol=pos.symbol,
                )
                continue

            try:
                await self._close_position(pos, reason)
                closed.append(pos)
            except Exception as e:
                logger.error("force_close_failed", id=pos.id, error=str(e))

        return closed

    def get_position_count(self, tier: str | None = None) -> int:
        """Count open positions, optionally filtered by tier."""
        if tier:
            return len([p for p in self.open_positions if p.tier == tier])
        return len(self.open_positions)

    def get_daily_pnl(self) -> float:
        """Calculate total P&L for today (open + closed)."""
        closed_pnl = sum(p.unrealized_pnl for p in self._closed_positions)
        open_pnl = sum(p.unrealized_pnl for p in self.open_positions)
        return round(closed_pnl + open_pnl, 2)

    def get_position_by_symbol_pillar(
        self, symbol: str, pillar: int
    ) -> Position | None:
        """Find an open position by symbol and pillar (for scale-in checks)."""
        for pos in self.open_positions:
            if pos.symbol == symbol and pos.pillar == pillar:
                return pos
        return None

    def get_positions_for_symbol(self, symbol: str) -> list[Position]:
        """Get all open positions for a symbol (for multi-pillar tracking)."""
        return [p for p in self.open_positions if p.symbol == symbol]

    def _get_pillar_config(self, pillar: int) -> dict[str, Any]:
        """Get pillar-specific config as a dict."""
        pillar_map = {
            1: self._cfg.pillars.p1,
            2: self._cfg.pillars.p2,
            3: self._cfg.pillars.p3,
        }
        p = pillar_map.get(pillar)
        if p:
            return p.model_dump()
        return {}

    @staticmethod
    def _is_power_hour() -> bool:
        """Check if we're in power hour (3:00-3:45 PM ET)."""
        now_et = datetime.now(ET)
        return time(15, 0) <= now_et.time() <= time(15, 45)


def _minutes_to_close() -> float:
    """Calculate minutes until market close (4:00 PM ET)."""
    now_et = datetime.now(ET)
    close_dt = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = (close_dt - now_et).total_seconds() / 60
    return max(0, delta)
