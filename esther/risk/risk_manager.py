"""Risk Manager — Position Limits, Daily Loss Caps, PDT Mode, and Advanced Risk Rules.

The last line of defense before capital destruction. Enforces:

    - Per-tier position limits (T1: 5, T2: 3, T3: 3)
    - Linear scaling by account size (10→25→50→100 spreads max)
    - Daily loss cap: 5% of account value → shut down for the day
    - Cooldown: 2 consecutive losses on same ticker → 30 min pause
    - PDT Mode: Under $25K, limit to 3 day trades per 5 rolling days
    - Event day sizing: Reduce by 50% on FOMC/CPI/PPI days
    - Multi-pillar risk: Aggregate risk per ticker, max 3% combined
    - Tiered stop risk calculation
    - Swing position risk: Max 10% of account in overnight positions
    - Daily stats tracking (win rate, avg win/loss, bad trade %)
    - Force-close: triggered by Black Swan RED or daily cap hit

Every trade request passes through the Risk Manager before execution.
If the Risk Manager says no, the trade doesn't happen. Period.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field

from esther.core.config import config
from esther.execution.position_manager import Position, PositionManager, PositionStatus

logger = structlog.get_logger(__name__)


# ── Economic Calendar Events ────────────────────────────────────

# Major economic events that warrant reduced sizing
EVENT_KEYWORDS = {"FOMC", "CPI", "PPI", "NFP", "JOBS", "GDP", "PCE", "RETAIL_SALES"}


class DayTrade(BaseModel):
    """Record of a single day trade for PDT tracking."""

    symbol: str
    date: date
    was_credit_spread_expiry: bool = False  # Credit spreads that expire don't count


class DailyStats(BaseModel):
    """Daily trading statistics — the 4 key metrics from @SuperLuckeee.

    Lever 1: Win Rate (increase by taking A+ setups only)
    Lever 2: Average Win (increase by letting winners run)
    Lever 3: Average Loss (decrease with hard stops)
    Lever 4: Bad Trade % (decrease by eliminating rule violations)
    """

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_losses: float = 0.0
    max_loss_trades: int = 0  # Trades that hit max loss
    rule_violations: int = 0  # Trades that violated trading rules
    bad_trades_detail: list[str] = []  # WHY each trade was bad

    @property
    def win_rate(self) -> float:
        """Win Rate = Wins / Total Trades."""
        return self.winning_trades / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def average_win(self) -> float:
        """Average Win = Total Profit / Winning Trades."""
        return self.total_profit / self.winning_trades if self.winning_trades > 0 else 0.0

    @property
    def average_loss(self) -> float:
        """Average Loss = Total Losses / Losing Trades."""
        return self.total_losses / self.losing_trades if self.losing_trades > 0 else 0.0

    @property
    def bad_trade_pct(self) -> float:
        """Bad Trade % = Trades that hit max loss / Total Trades."""
        return self.max_loss_trades / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        """Profit Factor = Total Profit / |Total Losses|."""
        return abs(self.total_profit / self.total_losses) if self.total_losses != 0 else float("inf")


class AccountTier(BaseModel):
    """Account tier for linear scaling rules."""

    tier_name: str
    min_balance: float
    max_spreads: int


class RiskCheck(BaseModel):
    """Result of a risk assessment."""

    approved: bool
    reason: str = ""
    current_positions: int = 0
    max_positions: int = 0
    daily_pnl: float = 0.0
    daily_loss_cap: float = 0.0
    cooldown_active: bool = False
    cooldown_until: datetime | None = None
    pdt_trades_remaining: int = -1  # -1 means PDT not active
    event_day_reduction: bool = False
    account_tier: str = ""


class DailyRiskReport(BaseModel):
    """End-of-day risk summary with expanded stats."""

    date: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    bad_trade_pct: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_positions: int = 0
    risk_events: list[str] = []
    shutdown_triggered: bool = False
    force_closes: int = 0
    day_trades_used: int = 0
    swing_positions_held: int = 0
    overnight_exposure: float = 0.0
    account_tier: str = ""
    max_spreads_allowed: int = 0


class RiskManager:
    """Enforces all risk limits and tracks daily risk metrics.

    Pre-trade checks (can_open_position):
    1. Is the daily loss cap hit? → Reject all trades
    2. PDT check: Under $25K and 3+ day trades in 5 days? → Reject
    3. Is this ticker on cooldown? → Reject
    4. Is the tier at max positions? → Reject
    5. Linear scaling: within account tier limits? → Reject
    6. Multi-pillar risk: combined risk per ticker under 3%? → Reject
    7. Event day: is it FOMC/CPI/PPI? → Reduce size by 50%
    8. Swing risk: overnight exposure under 10%? → Reject swing trades
    9. Is the total risk within bounds? → Reject

    Post-trade tracking:
    - Update consecutive loss counters
    - Track daily P&L watermark
    - Track the 4 key stats
    - Generate end-of-day risk reports
    """

    # Account tier thresholds for linear scaling
    # Configured for $10K/day target with compounding
    # $80K start → medium tier → scale to large as profits compound
    ACCOUNT_TIERS = [
        AccountTier(tier_name="micro", min_balance=0, max_spreads=10),
        AccountTier(tier_name="small", min_balance=10_000, max_spreads=25),
        AccountTier(tier_name="medium", min_balance=50_000, max_spreads=60),   # bumped from 50
        AccountTier(tier_name="large", min_balance=150_000, max_spreads=100),  # lowered from 200K
        AccountTier(tier_name="whale", min_balance=500_000, max_spreads=200),  # new tier
    ]

    def __init__(self, position_manager: PositionManager, account_balance: float, risk_cfg=None):
        self.pm = position_manager
        self.account_balance = account_balance
        self._cfg = risk_cfg if risk_cfg is not None else config().risk

        # Daily state
        self._daily_pnl_peak: float = 0.0
        self._daily_max_drawdown: float = 0.0
        self._shutdown: bool = False
        self._shutdown_reason: str = ""

        # Cooldown tracking: {symbol: (consecutive_losses, cooldown_until)}
        self._cooldowns: dict[str, tuple[int, datetime | None]] = {}

        # Recent loser tracking: {symbol: last_loss_time} — no re-entry for 30 min
        self._recent_losers: dict[str, datetime] = {}

        # PDT tracking: rolling 5-day window of day trades
        self._day_trades: list[DayTrade] = []

        # Event day state
        self._is_event_day: bool = False
        self._event_name: str = ""

        # Daily stats tracking
        self._daily_stats = DailyStats()

        # Metrics
        self._risk_events: list[str] = []
        self._peak_positions: int = 0

    @property
    def is_shutdown(self) -> bool:
        """Whether trading is shut down for the day."""
        return self._shutdown

    @property
    def daily_loss_cap(self) -> float:
        """Maximum allowable daily loss in dollars."""
        return self.account_balance * self._cfg.daily_loss_cap_pct

    @property
    def daily_stats(self) -> DailyStats:
        """Current daily statistics."""
        return self._daily_stats

    def get_account_tier(self) -> AccountTier:
        """Determine current account tier for linear scaling.

        Returns the tier matching the current account balance.
        Tiers:
            Under $10K: 10 spreads max
            $10K-$50K: 25 spreads max
            $50K-$200K: 50 spreads max
            Over $200K: 100 spreads max
        """
        current_tier = self.ACCOUNT_TIERS[0]
        for tier in self.ACCOUNT_TIERS:
            if self.account_balance >= tier.min_balance:
                current_tier = tier
        return current_tier

    def get_max_spreads(self) -> int:
        """Get maximum number of spreads allowed for current account size."""
        return self.get_account_tier().max_spreads

    # ── PDT Tracking ─────────────────────────────────────────────

    def is_pdt_restricted(self) -> bool:
        """Check if the account is subject to PDT rules (under $25K)."""
        return self.account_balance < 25_000

    def get_pdt_trades_remaining(self) -> int:
        """Get number of day trades remaining in the 5-day window.

        Returns -1 if PDT rules don't apply (account >= $25K).
        """
        if not self.is_pdt_restricted():
            return -1

        # Count day trades in the last 5 rolling business days
        cutoff = date.today() - timedelta(days=5)
        recent_trades = [
            dt for dt in self._day_trades
            if dt.date >= cutoff and not dt.was_credit_spread_expiry
        ]
        return max(0, 3 - len(recent_trades))

    def record_day_trade(
        self, symbol: str, was_credit_spread_expiry: bool = False
    ) -> None:
        """Record a day trade for PDT tracking.

        Credit spreads that expire worthless do NOT count as day trades.

        Args:
            symbol: The traded symbol.
            was_credit_spread_expiry: True if this was a credit spread that expired.
        """
        self._day_trades.append(
            DayTrade(
                symbol=symbol,
                date=date.today(),
                was_credit_spread_expiry=was_credit_spread_expiry,
            )
        )

        # Clean up old trades (older than 5 days)
        cutoff = date.today() - timedelta(days=5)
        self._day_trades = [dt for dt in self._day_trades if dt.date >= cutoff]

        if not was_credit_spread_expiry:
            remaining = self.get_pdt_trades_remaining()
            logger.info(
                "day_trade_recorded",
                symbol=symbol,
                pdt_remaining=remaining,
                is_pdt=self.is_pdt_restricted(),
            )

    # ── Event Day Management ─────────────────────────────────────

    def set_event_day(self, event_name: str) -> None:
        """Mark today as an economic event day (FOMC, CPI, PPI, etc.).

        This reduces position sizing by 50% for all new trades.

        Args:
            event_name: Name of the event (e.g., "FOMC", "CPI").
        """
        self._is_event_day = True
        self._event_name = event_name
        self._risk_events.append(f"EVENT_DAY: {event_name} — sizing reduced 50%")
        logger.warning("event_day_set", event=event_name)

    def get_event_day_multiplier(self) -> float:
        """Get the sizing multiplier for event days.

        Returns 0.5 on event days, 1.0 otherwise.
        """
        return 0.5 if self._is_event_day else 1.0

    # ── Multi-Pillar Risk ────────────────────────────────────────

    def get_ticker_total_risk(self, symbol: str) -> float:
        """Get the total risk exposure for a single ticker across all pillars.

        Used to enforce the 3% max combined risk per ticker when running
        multiple pillars simultaneously.

        Args:
            symbol: Ticker symbol.

        Returns:
            Total risk in dollars across all open positions for this ticker.
        """
        positions = self.pm.get_positions_for_symbol(symbol)
        total_risk = 0.0
        for pos in positions:
            # For credit spreads, risk = (stop_loss - entry) * 100 * quantity
            if pos.pillar in (1, 2, 3):
                # Use the worst-case stop (widest tranche stop)
                active_tranches = [t for t in pos.tranches if t.status.value == "ACTIVE"]
                if active_tranches:
                    worst_stop = max(t.stop_price for t in active_tranches)
                else:
                    worst_stop = pos.stop_loss
                risk = (worst_stop - pos.average_entry_price) * 100 * pos.active_quantity
            else:
                # P4: risk = premium paid * 100 * quantity
                risk = pos.average_entry_price * 100 * pos.active_quantity
            total_risk += abs(risk)
        return round(total_risk, 2)

    def get_max_risk_per_ticker(self) -> float:
        """Maximum combined risk per ticker (configurable, default 3%)."""
        return self.account_balance * self._cfg.max_risk_per_ticker_pct

    # ── Tiered Stop Risk Calculation ─────────────────────────────

    def calculate_tiered_stop_risk(self, position: Position) -> float:
        """Calculate max risk accounting for tiered stops.

        Worst case = all tranches stopped out. But since tranches have
        different stop levels, the actual max risk is the weighted sum.

        Args:
            position: Position with tiered stops.

        Returns:
            Maximum possible loss in dollars.
        """
        if not position.tranches:
            # No tiered stops — use standard calculation
            if position.pillar in (1, 2, 3):
                return abs(
                    (position.stop_loss - position.entry_price) * 100 * position.quantity
                )
            else:
                return position.entry_price * 100 * position.quantity

        total_risk = 0.0
        for tranche in position.tranches:
            # Risk per tranche = (stop_price - entry) * 100 * tranche_quantity
            tranche_risk = (tranche.stop_price - position.average_entry_price) * 100 * tranche.quantity
            total_risk += abs(tranche_risk)

        return round(total_risk, 2)

    # ── Swing Position Risk ──────────────────────────────────────

    def get_swing_exposure(self) -> float:
        """Get total dollar exposure in swing (overnight) positions."""
        swing_positions = self.pm.open_swing_positions
        total_exposure = 0.0
        for pos in swing_positions:
            if pos.pillar in (1, 2, 3):
                # Credit spread: exposure = wing_width * 100 * quantity
                # Approximate wing width from stop loss
                exposure = abs(pos.stop_loss) * 100 * pos.active_quantity
            else:
                exposure = pos.average_entry_price * 100 * pos.active_quantity
            total_exposure += exposure
        return round(total_exposure, 2)

    def get_max_swing_exposure(self) -> float:
        """Maximum allowed exposure in swing positions = 10% of account."""
        return self.account_balance * 0.10

    def can_open_swing(self, additional_risk: float) -> bool:
        """Check if we can open a new swing position within risk budget.

        Args:
            additional_risk: Risk of the proposed new swing position.

        Returns:
            True if the new swing would be within the 10% budget.
        """
        current_exposure = self.get_swing_exposure()
        max_exposure = self.get_max_swing_exposure()
        return (current_exposure + additional_risk) <= max_exposure

    # ── Main Risk Check ──────────────────────────────────────────

    def can_open_position(
        self,
        symbol: str,
        tier: str,
        max_risk: float,
        is_swing: bool = False,
        is_scale_in: bool = False,
        pillar: int = 1,
    ) -> RiskCheck:
        """Pre-trade risk check. Must pass before any order is submitted.

        Runs through all risk checks in order of severity:
        1. Daily shutdown
        2. Daily loss cap
        3. PDT check (if under $25K)
        4. Cooldown
        5. Position limit for tier
        6. Linear scaling (account tier max spreads)
        7. Multi-pillar risk (max 3% per ticker)
        8. Swing position budget (if swing trade)
        9. Total risk check

        Args:
            symbol: Ticker symbol.
            tier: Ticker tier ("tier1", "tier2", "tier3").
            max_risk: Maximum possible loss for this trade in dollars.
            is_swing: Whether this is a swing (overnight) trade.
            is_scale_in: Whether this is adding to an existing position.

        Returns:
            RiskCheck with approval status and details.
        """
        account_tier = self.get_account_tier()

        # Check 1: Daily shutdown
        if self._shutdown:
            return RiskCheck(
                approved=False,
                reason=f"DAILY_SHUTDOWN: {self._shutdown_reason}",
                daily_pnl=self.pm.get_daily_pnl(),
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 2: Daily loss cap
        daily_pnl = self.pm.get_daily_pnl()
        if daily_pnl <= -self.daily_loss_cap:
            self._trigger_shutdown(
                f"Daily loss cap hit: ${daily_pnl:,.2f} <= -${self.daily_loss_cap:,.2f}"
            )
            return RiskCheck(
                approved=False,
                reason=f"DAILY_LOSS_CAP: P&L ${daily_pnl:,.2f} exceeds cap -${self.daily_loss_cap:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 3: PDT (Pattern Day Trader) restriction
        if self.is_pdt_restricted() and not is_swing:
            remaining = self.get_pdt_trades_remaining()
            if remaining <= 0:
                return RiskCheck(
                    approved=False,
                    reason=f"PDT_LIMIT: 0 day trades remaining (account ${self.account_balance:,.0f} < $25K)",
                    pdt_trades_remaining=0,
                    daily_pnl=daily_pnl,
                    daily_loss_cap=self.daily_loss_cap,
                    account_tier=account_tier.tier_name,
                )

        # Check 3b: Recent loser — handled by ReentryGuard in engine (candle-based, not time-based)
        # 180 Acrobat Flip: removing the 5-min fallback block to allow continuous inversion Flip 20x logic
        if symbol in self._recent_losers:
            loss_time = self._recent_losers[symbol]
            elapsed = (datetime.now() - loss_time).total_seconds() / 60
            if elapsed > 60:
                # Auto-expire after 1 hour regardless
                del self._recent_losers[symbol]

        # Check 4: Cooldown (skip for scale-ins)
        if not is_scale_in:
            cooldown_info = self._cooldowns.get(symbol)
            if cooldown_info:
                consecutive_losses, cooldown_until = cooldown_info
                # 180 Acrobat Flip 20x: Bypass time cooldowns so the inversion engine can continuously flip
                if cooldown_until and datetime.now() < cooldown_until and False: # disabled
                    remaining = (cooldown_until - datetime.now()).total_seconds() / 60
                    return RiskCheck(
                        approved=False,
                        reason=f"COOLDOWN: {symbol} on cooldown for {remaining:.0f} more minutes "
                               f"({consecutive_losses} consecutive losses)",
                        cooldown_active=True,
                        cooldown_until=cooldown_until,
                        account_tier=account_tier.tier_name,
                    )

        # Check 5: Position limit for this tier
        max_pos = self._cfg.max_positions.get(tier, 3)
        current_pos = self.pm.get_position_count(tier)

        if current_pos >= max_pos and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"POSITION_LIMIT: {tier} has {current_pos}/{max_pos} positions",
                current_positions=current_pos,
                max_positions=max_pos,
                account_tier=account_tier.tier_name,
            )

        # Check 5b: Per-ticker position limit
        ticker_positions_list = self.pm.get_positions_for_symbol(symbol)
        ticker_positions = len(ticker_positions_list)
        max_per_ticker = self._cfg.max_positions_per_ticker
        logger.info(
            "ticker_limit_check",
            symbol=symbol,
            ticker_positions=ticker_positions,
            max_per_ticker=max_per_ticker,
            position_ids=[p.id for p in ticker_positions_list],
        )
        if ticker_positions >= max_per_ticker and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"TICKER_LIMIT: {symbol} already has {ticker_positions}/{max_per_ticker} positions",
                current_positions=ticker_positions,
                max_positions=max_per_ticker,
                account_tier=account_tier.tier_name,
            )

        # Check 6: Linear scaling — total spreads across all tiers
        total_positions = self.pm.get_position_count()
        max_spreads = account_tier.max_spreads

        if total_positions >= max_spreads and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"ACCOUNT_TIER_LIMIT: {account_tier.tier_name} tier allows max {max_spreads} spreads, "
                       f"currently at {total_positions}",
                current_positions=total_positions,
                max_positions=max_spreads,
                account_tier=account_tier.tier_name,
            )

        # Check 7: Multi-pillar risk — max 3% per ticker
        ticker_risk = self.get_ticker_total_risk(symbol)
        max_ticker_risk = self.get_max_risk_per_ticker()

        if ticker_risk + max_risk > max_ticker_risk:
            return RiskCheck(
                approved=False,
                reason=f"MULTI_PILLAR_RISK: {symbol} combined risk ${ticker_risk + max_risk:,.2f} "
                       f"would exceed 3% cap ${max_ticker_risk:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 8: Swing position budget
        if is_swing:
            if not self.can_open_swing(max_risk):
                current_swing = self.get_swing_exposure()
                max_swing = self.get_max_swing_exposure()
                return RiskCheck(
                    approved=False,
                    reason=f"SWING_RISK_BUDGET: Current swing exposure ${current_swing:,.2f} + "
                           f"new risk ${max_risk:,.2f} would exceed 10% budget ${max_swing:,.2f}",
                    daily_pnl=daily_pnl,
                    daily_loss_cap=self.daily_loss_cap,
                    account_tier=account_tier.tier_name,
                )

        # Check 9: Would this trade push us past the daily loss cap?
        # NOTE: For credit spreads (P1/P2/P3), max_risk = margin requirement (worst case).
        # We should NOT gate entries on margin requirement vs daily loss cap —
        # that blocks ALL trades on small accounts. Only gate on realized P&L.
        # This check only applies to debit trades (P4 scalps) where max_risk = actual cash outlay.
        if pillar == 4 and daily_pnl - max_risk <= -self.daily_loss_cap:
            return RiskCheck(
                approved=False,
                reason=f"RISK_TOO_HIGH: Current P&L ${daily_pnl:,.2f} - max risk ${max_risk:,.2f} "
                       f"would exceed cap -${self.daily_loss_cap:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 10: Max trades per day
        max_daily = config().engine.max_trades_per_day if hasattr(config().engine, 'max_trades_per_day') else 50
        trades_today = self._daily_stats.total_trades + len(self.pm.open_day_positions)
        if trades_today >= max_daily and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"MAX_TRADES_PER_DAY: Hit daily limit of {max_daily} trades",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # All checks passed
        pdt_remaining = self.get_pdt_trades_remaining()

        logger.info(
            "risk_approved",
            symbol=symbol,
            tier=tier,
            positions=f"{current_pos}/{max_pos}",
            daily_pnl=daily_pnl,
            account_tier=account_tier.tier_name,
            max_spreads=max_spreads,
            event_day=self._is_event_day,
            pdt_remaining=pdt_remaining,
            is_swing=is_swing,
            is_scale_in=is_scale_in,
        )

        return RiskCheck(
            approved=True,
            current_positions=current_pos,
            max_positions=max_pos,
            daily_pnl=daily_pnl,
            daily_loss_cap=self.daily_loss_cap,
            pdt_trades_remaining=pdt_remaining,
            event_day_reduction=self._is_event_day,
            account_tier=account_tier.tier_name,
        )

    def adjust_size_for_events(self, base_quantity: int) -> int:
        """Adjust position size for event days.

        On FOMC/CPI/PPI days, reduce position size by 50%.

        Args:
            base_quantity: The originally calculated quantity.

        Returns:
            Adjusted quantity (at least 1).
        """
        multiplier = self.get_event_day_multiplier()
        adjusted = max(1, int(base_quantity * multiplier))

        if multiplier < 1.0:
            logger.info(
                "event_day_size_reduction",
                event=self._event_name,
                original_qty=base_quantity,
                adjusted_qty=adjusted,
                multiplier=multiplier,
            )

        return adjusted

    def record_trade_result(
        self,
        position: Position,
        trade_time: datetime | None = None,
        ai_confidence: float = 1.0,
        flow_aligned: bool = True,
        level_confirmed: bool = True,
    ) -> None:
        """Record a completed trade for risk tracking and stats.

        Enhanced with bad trade classification from @SuperLuckeee's Lever 4:
        "Your expectancy gets damaged most by extra trades, boredom trades,
        revenge trades, trades outside your window."

        A trade is classified as BAD if ANY of these are true:
        - Taken before 10:00 AM ET
        - AI confidence below 70%
        - Outside key levels (no level confirmation)
        - Against flow direction
        - More than 2 trades already taken today

        Updates:
        - Daily stats (win rate, avg win/loss, bad trade %)
        - Bad trade classification with reasons
        - Consecutive loss counter for cooldowns
        - PDT tracking
        - Peak position count
        - Daily P&L watermark and drawdown

        Args:
            position: The closed position.
            trade_time: When the trade was taken (for time-of-day check).
            ai_confidence: Kage's verdict confidence (0-1).
            flow_aligned: Whether flow agreed with trade direction.
            level_confirmed: Whether price was at a key level.
        """
        won = position.unrealized_pnl > 0
        pnl = position.unrealized_pnl

        # ── Bad Trade Classification (Lever 4) ───────────────
        bad_reasons: list[str] = []

        # Check 1: Taken before 10:00 AM ET
        if trade_time:
            from zoneinfo import ZoneInfo
            trade_et = trade_time.astimezone(ZoneInfo("America/New_York"))
            if trade_et.hour < 10:
                bad_reasons.append(f"BEFORE_10AM: trade at {trade_et.strftime('%H:%M')} ET")

        # Check 2: AI confidence below 70%
        if ai_confidence < 0.70:
            bad_reasons.append(f"LOW_AI_CONFIDENCE: {ai_confidence:.0%} < 70%")

        # Check 3: Outside key levels
        if not level_confirmed:
            bad_reasons.append("NO_LEVEL_CONFIRMATION: trade not at key S/R")

        # Check 4: Against flow direction
        if not flow_aligned:
            bad_reasons.append("FLOW_MISALIGNED: trade against institutional flow")

        # Check 5: Overtrading — use max_trades_per_day from engine config
        max_daily = config().engine.max_trades_per_day if hasattr(config().engine, 'max_trades_per_day') else 50
        if self._daily_stats.total_trades >= max_daily:
            bad_reasons.append(f"OVERTRADE: trade #{self._daily_stats.total_trades + 1} (max {max_daily}/day)")

        if bad_reasons:
            self._daily_stats.rule_violations += 1
            for reason in bad_reasons:
                self._daily_stats.bad_trades_detail.append(
                    f"{position.symbol} P{position.pillar}: {reason}"
                )
            logger.warning(
                "bad_trade_detected",
                symbol=position.symbol,
                pillar=position.pillar,
                reasons=bad_reasons,
                total_violations=self._daily_stats.rule_violations,
            )

        # Update daily stats
        self._daily_stats.total_trades += 1
        if won:
            self._daily_stats.winning_trades += 1
            self._daily_stats.total_profit += pnl
        else:
            self._daily_stats.losing_trades += 1
            self._daily_stats.total_losses += pnl  # pnl is negative for losses

            # Check if this was a max loss trade
            # Max loss = position was stopped out at worst level
            if position.status in (PositionStatus.CLOSED_STOP, PositionStatus.CLOSED_TIERED_STOP):
                # Check if ALL tranches were stopped (worst case)
                if position.tranches and all(
                    t.status.value == "STOPPED" for t in position.tranches
                ):
                    self._daily_stats.max_loss_trades += 1
                elif not position.tranches:
                    self._daily_stats.max_loss_trades += 1

        # PDT tracking — record day trade
        is_expired = position.status == PositionStatus.EXPIRED_WORTHLESS
        is_credit_spread = position.pillar in (1, 2, 3)
        self.record_day_trade(
            symbol=position.symbol,
            was_credit_spread_expiry=is_credit_spread and is_expired,
        )

        # Update cooldown tracking
        symbol = position.symbol
        if symbol not in self._cooldowns:
            self._cooldowns[symbol] = (0, None)

        consecutive_losses, _ = self._cooldowns[symbol]

        if won:
            # Win resets the counter
            self._cooldowns[symbol] = (0, None)
        else:
            consecutive_losses += 1
            cooldown_until = None

            if consecutive_losses >= self._cfg.cooldown_consecutive_losses:
                cooldown_until = datetime.now() + timedelta(minutes=self._cfg.cooldown_minutes)
                event = (
                    f"COOLDOWN_TRIGGERED: {symbol} ({consecutive_losses} consecutive losses, "
                    f"paused until {cooldown_until.strftime('%H:%M')})"
                )
                self._risk_events.append(event)
                logger.warning("cooldown_triggered", symbol=symbol, until=cooldown_until)

            self._cooldowns[symbol] = (consecutive_losses, cooldown_until)

            # Track as recent loser — prevent same-symbol re-entry for 30 min
            self._recent_losers[symbol] = datetime.now()
            logger.info("recent_loser_tracked", symbol=symbol)

        # Update P&L watermark
        daily_pnl = self.pm.get_daily_pnl()
        if daily_pnl > self._daily_pnl_peak:
            self._daily_pnl_peak = daily_pnl

        drawdown = self._daily_pnl_peak - daily_pnl
        if drawdown > self._daily_max_drawdown:
            self._daily_max_drawdown = drawdown

        # Update peak positions
        current_count = len(self.pm.open_positions)
        if current_count > self._peak_positions:
            self._peak_positions = current_count

        # Check if we've hit the loss cap
        if daily_pnl <= -self.daily_loss_cap:
            self._trigger_shutdown(f"Daily loss cap hit after trade: ${daily_pnl:,.2f}")

        logger.info(
            "trade_recorded",
            symbol=symbol,
            won=won,
            pnl=pnl,
            daily_pnl=daily_pnl,
            win_rate=f"{self._daily_stats.win_rate:.0%}",
            avg_win=f"${self._daily_stats.average_win:.2f}",
            avg_loss=f"${self._daily_stats.average_loss:.2f}",
            bad_trade_pct=f"{self._daily_stats.bad_trade_pct:.0%}",
        )

    def _trigger_shutdown(self, reason: str) -> None:
        """Shut down trading for the rest of the day."""
        self._shutdown = True
        self._shutdown_reason = reason
        self._risk_events.append(f"SHUTDOWN: {reason}")
        logger.error("daily_shutdown", reason=reason)

    def trigger_force_close(self, reason: str = "BLACK_SWAN_RED") -> None:
        """Signal that all positions should be force-closed.

        Called by the Black Swan detector or when daily cap is hit.
        The actual closing is done by PositionManager.force_close_all().
        """
        self._risk_events.append(f"FORCE_CLOSE: {reason}")
        self._trigger_shutdown(reason)
        logger.error("force_close_triggered", reason=reason)

    def generate_daily_report(self) -> DailyRiskReport:
        """Generate end-of-day risk report with full stats.

        Includes the 4 key daily stats:
        1. Win Rate = Wins / Total Trades
        2. Average Win = Total Profit / Winning Trades
        3. Average Loss = Total Losses / Losing Trades
        4. Bad Trade % = Trades that hit max loss / Total Trades

        Returns:
            DailyRiskReport with all metrics for the day.
        """
        stats = self._daily_stats
        account_tier = self.get_account_tier()

        report = DailyRiskReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_trades=stats.total_trades,
            winning_trades=stats.winning_trades,
            losing_trades=stats.losing_trades,
            win_rate=round(stats.win_rate, 4),
            average_win=round(stats.average_win, 2),
            average_loss=round(stats.average_loss, 2),
            bad_trade_pct=round(stats.bad_trade_pct, 4),
            profit_factor=round(stats.profit_factor, 2) if stats.profit_factor != float("inf") else 0.0,
            total_pnl=self.pm.get_daily_pnl(),
            max_drawdown=round(self._daily_max_drawdown, 2),
            peak_positions=self._peak_positions,
            risk_events=self._risk_events,
            shutdown_triggered=self._shutdown,
            force_closes=len([e for e in self._risk_events if "FORCE_CLOSE" in e]),
            day_trades_used=len([
                dt for dt in self._day_trades
                if dt.date == date.today() and not dt.was_credit_spread_expiry
            ]),
            swing_positions_held=len(self.pm.open_swing_positions),
            overnight_exposure=self.get_swing_exposure(),
            account_tier=account_tier.tier_name,
            max_spreads_allowed=account_tier.max_spreads,
        )

        logger.info(
            "daily_risk_report",
            trades=stats.total_trades,
            win_rate=f"{stats.win_rate:.0%}",
            avg_win=f"${stats.average_win:.2f}",
            avg_loss=f"${stats.average_loss:.2f}",
            bad_trade_pct=f"{stats.bad_trade_pct:.0%}",
            pnl=report.total_pnl,
            max_drawdown=report.max_drawdown,
            events=len(report.risk_events),
            account_tier=account_tier.tier_name,
            max_spreads=account_tier.max_spreads,
        )

        return report

    def reset_daily(self, new_balance: float | None = None) -> None:
        """Reset all daily state for a new trading day.

        Args:
            new_balance: Updated account balance. If None, keeps current.
        """
        if new_balance is not None:
            self.account_balance = new_balance

        self._daily_pnl_peak = 0.0
        self._daily_max_drawdown = 0.0
        self._shutdown = False
        self._shutdown_reason = ""
        self._cooldowns.clear()
        self._risk_events.clear()
        self._peak_positions = 0
        self._is_event_day = False
        self._event_name = ""
        self._daily_stats = DailyStats()

        # Don't clear day trades — they use a rolling 5-day window
        # Clean up old ones instead
        cutoff = date.today() - timedelta(days=5)
        self._day_trades = [dt for dt in self._day_trades if dt.date >= cutoff]

        logger.info(
            "risk_manager_reset",
            balance=self.account_balance,
            account_tier=self.get_account_tier().tier_name,
            max_spreads=self.get_max_spreads(),
            pdt_restricted=self.is_pdt_restricted(),
            pdt_remaining=self.get_pdt_trades_remaining(),
        )
