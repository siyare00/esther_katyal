"""Tests for the Risk Manager — position limits, daily loss caps, and cooldowns."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from esther.risk.risk_manager import RiskManager, RiskCheck, DailyRiskReport
from esther.execution.position_manager import PositionManager, Position, PositionStatus
from esther.data.tradier import TradierClient


# ── Fixtures ─────────────────────────────────────────────────────


def _make_position(
    symbol: str = "SPY",
    pillar: int = 1,
    tier: str = "tier1",
    pnl: float = 100.0,
    status: PositionStatus = PositionStatus.CLOSED_PROFIT,
    direction: str = "BULL",
) -> Position:
    """Create a test position."""
    return Position(
        id=f"pos_{id(symbol):04d}",
        symbol=symbol,
        pillar=pillar,
        quantity=1,
        entry_price=2.50,
        tier=tier,
        direction=direction,
        unrealized_pnl=pnl,
        status=status,
    )


@pytest.fixture
def mock_pm():
    """Create a mocked PositionManager."""
    pm = MagicMock(spec=PositionManager)
    pm.open_positions = []
    pm.closed_positions = []
    pm.get_daily_pnl.return_value = 0.0
    pm.get_position_count.return_value = 0
    return pm


@pytest.fixture
def risk_mgr(mock_pm):
    """Create a RiskManager with $100k balance."""
    with patch("esther.risk.risk_manager.config") as mock_config:
        from esther.core.config import RiskConfig
        mock_config.return_value.risk = RiskConfig()
        return RiskManager(mock_pm, account_balance=100_000.0)


# ── Tier Position Limit Tests ────────────────────────────────────


class TestTierPositionLimits:
    """Test per-tier position limits (T1: 5, T2: 3, T3: 3)."""

    def test_tier1_under_limit_approved(self, risk_mgr, mock_pm):
        """Tier 1 with fewer than 5 positions should be approved."""
        mock_pm.get_position_count.return_value = 3
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

    def test_tier1_at_limit_rejected(self, risk_mgr, mock_pm):
        """Tier 1 at max 5 positions should be rejected."""
        mock_pm.get_position_count.return_value = 5
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "POSITION_LIMIT" in result.reason

    def test_tier2_limit_is_3(self, risk_mgr, mock_pm):
        """Tier 2 max positions should be 3."""
        mock_pm.get_position_count.return_value = 3
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("GLD", "tier2", max_risk=500.0)
        assert not result.approved
        assert "POSITION_LIMIT" in result.reason

    def test_tier3_limit_is_3(self, risk_mgr, mock_pm):
        """Tier 3 max positions should be 3."""
        mock_pm.get_position_count.return_value = 2
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("NVDA", "tier3", max_risk=500.0)
        assert result.approved

        mock_pm.get_position_count.return_value = 3
        result = risk_mgr.can_open_position("NVDA", "tier3", max_risk=500.0)
        assert not result.approved

    def test_current_positions_in_result(self, risk_mgr, mock_pm):
        """RiskCheck should include current and max position counts."""
        mock_pm.get_position_count.return_value = 2
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.current_positions == 2
        assert result.max_positions == 5


# ── Daily Loss Cap Tests ─────────────────────────────────────────


class TestDailyLossCap:
    """Test daily loss cap enforcement (5% of account)."""

    def test_loss_cap_is_2_percent(self, risk_mgr):
        """Daily loss cap should be 2% of $100k = $2,000 (sovereign instruction set)."""
        assert risk_mgr.daily_loss_cap == 2_000.0

    def test_under_cap_approved(self, risk_mgr, mock_pm):
        """P&L above the loss cap should be approved."""
        mock_pm.get_daily_pnl.return_value = -1_000.0  # Lost $1k, cap is $2k
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

    def test_at_cap_rejected(self, risk_mgr, mock_pm):
        """P&L at or below the loss cap should be rejected."""
        mock_pm.get_daily_pnl.return_value = -2_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "DAILY_LOSS_CAP" in result.reason

    def test_exceeding_cap_triggers_shutdown(self, risk_mgr, mock_pm):
        """Exceeding loss cap should trigger daily shutdown."""
        mock_pm.get_daily_pnl.return_value = -3_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert risk_mgr.is_shutdown

    def test_risk_too_high_for_remaining_cap(self, risk_mgr, mock_pm):
        """Trade that would push past cap should be rejected."""
        mock_pm.get_daily_pnl.return_value = -1_500.0  # Already down $1.5k
        mock_pm.get_position_count.return_value = 0

        # Max risk of $1k would push to $2.5k loss → past the $2k cap
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=1_000.0)
        assert not result.approved
        assert "RISK_TOO_HIGH" in result.reason

    def test_shutdown_blocks_all_trades(self, risk_mgr, mock_pm):
        """Once shutdown is triggered, all subsequent trades should be rejected."""
        mock_pm.get_daily_pnl.return_value = -3_000.0
        mock_pm.get_position_count.return_value = 0

        # First trade triggers shutdown
        risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert risk_mgr.is_shutdown

        # Even a tiny trade should be rejected
        mock_pm.get_daily_pnl.return_value = 0.0  # P&L recovered somehow
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=1.0)
        assert not result.approved
        assert "DAILY_SHUTDOWN" in result.reason

    def test_daily_pnl_included_in_result(self, risk_mgr, mock_pm):
        """RiskCheck should include daily P&L and loss cap."""
        mock_pm.get_daily_pnl.return_value = -1_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.daily_pnl == -1_000.0
        assert result.daily_loss_cap == 2_000.0


# ── Cooldown Tests ───────────────────────────────────────────────


class TestCooldownLogic:
    """Test consecutive loss cooldowns."""

    def test_no_cooldown_initially(self, risk_mgr, mock_pm):
        """No cooldown should be active initially."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved
        assert not result.cooldown_active

    def test_single_loss_triggers_recent_loser(self, risk_mgr, mock_pm):
        """A single loss should trigger 5-min recent loser cooldown (candle guard handles longer)."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        loss_pos = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss_pos)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason

    def test_two_consecutive_losses_triggers_cooldown(self, risk_mgr, mock_pm):
        """Two consecutive losses should trigger both recent loser + cooldown."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # First loss
        loss1 = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss1)

        # Second consecutive loss
        loss2 = _make_position("SPY", pnl=-300.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss2)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason  # recent loser check hits first

    def test_win_resets_cooldown_counter(self, risk_mgr, mock_pm):
        """A win should reset the consecutive loss counter but recent loser still active."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # One loss
        loss1 = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss1)

        # A win (different symbol — SPY is on recent loser cooldown)
        win = _make_position("QQQ", pnl=150.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        # QQQ should be tradeable (no loss recorded)
        result = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert result.approved

        # SPY still blocked by recent loser
        result_spy = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result_spy.approved

    def test_cooldown_per_symbol(self, risk_mgr, mock_pm):
        """Cooldown should be per-symbol, not global."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Two consecutive losses on SPY
        for _ in range(2):
            loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # SPY should be on cooldown
        result_spy = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result_spy.approved

        # QQQ should still be fine
        result_qqq = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert result_qqq.approved

    def test_cooldown_expires(self, risk_mgr, mock_pm):
        """Both cooldown and recent loser should expire after duration."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Trigger cooldown
        for _ in range(2):
            loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # Manually set cooldown_until to the past AND expire recent loser
        risk_mgr._cooldowns["SPY"] = (2, datetime.now() - timedelta(minutes=1))
        risk_mgr._recent_losers["SPY"] = datetime.now() - timedelta(minutes=31)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved  # Both expired


# ── Trade Result Recording Tests ─────────────────────────────────


class TestTradeRecording:
    """Test trade result recording and metric tracking."""

    def test_win_increments_wins(self, risk_mgr):
        """A winning trade should increment the win counter."""
        win = _make_position("SPY", pnl=200.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        assert risk_mgr._daily_stats.winning_trades == 1
        assert risk_mgr._daily_stats.total_trades == 1

    def test_loss_increments_losses(self, risk_mgr):
        """A losing trade should increment the loss counter."""
        loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        assert risk_mgr._daily_stats.losing_trades == 1
        assert risk_mgr._daily_stats.total_trades == 1

    def test_loss_triggers_shutdown_when_at_cap(self, risk_mgr, mock_pm):
        """A trade result that pushes P&L past cap should trigger shutdown."""
        mock_pm.get_daily_pnl.return_value = -5_500.0

        loss = _make_position("SPY", pnl=-1000.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        assert risk_mgr.is_shutdown


# ── Force Close Tests ────────────────────────────────────────────


class TestForceClose:
    """Test force close signaling."""

    def test_force_close_triggers_shutdown(self, risk_mgr):
        """trigger_force_close should shut down trading."""
        risk_mgr.trigger_force_close("BLACK_SWAN_RED")

        assert risk_mgr.is_shutdown
        assert "FORCE_CLOSE" in risk_mgr._risk_events[0]

    def test_force_close_logged_in_events(self, risk_mgr):
        """Force close should be recorded in risk events."""
        risk_mgr.trigger_force_close("TEST_REASON")

        assert len(risk_mgr._risk_events) == 2  # FORCE_CLOSE + SHUTDOWN
        assert any("FORCE_CLOSE" in e for e in risk_mgr._risk_events)


# ── Daily Report Tests ───────────────────────────────────────────


class TestDailyReport:
    """Test daily risk report generation."""

    def test_empty_day_report(self, risk_mgr, mock_pm):
        """Report with no trades should have zero metrics."""
        mock_pm.get_daily_pnl.return_value = 0.0
        report = risk_mgr.generate_daily_report()

        assert report.total_trades == 0
        assert report.win_rate == 0.0
        assert report.total_pnl == 0.0
        assert not report.shutdown_triggered

    def test_report_with_trades(self, risk_mgr, mock_pm):
        """Report should reflect recorded trades."""
        mock_pm.get_daily_pnl.return_value = 500.0

        win = _make_position("SPY", pnl=300.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        loss = _make_position("QQQ", pnl=-100.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        win2 = _make_position("IWM", pnl=300.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win2)

        report = risk_mgr.generate_daily_report()

        assert report.total_trades == 3
        assert report.winning_trades == 2
        assert report.losing_trades == 1
        assert abs(report.win_rate - 0.6667) < 0.01
        assert report.total_pnl == 500.0

    def test_report_tracks_shutdown(self, risk_mgr, mock_pm):
        """Report should show if shutdown was triggered."""
        mock_pm.get_daily_pnl.return_value = -6_000.0
        mock_pm.get_position_count.return_value = 0

        risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)

        report = risk_mgr.generate_daily_report()
        assert report.shutdown_triggered


# ── Daily Reset Tests ────────────────────────────────────────────


class TestDailyReset:
    """Test daily state reset."""

    def test_reset_clears_shutdown(self, risk_mgr):
        """Reset should clear the shutdown flag."""
        risk_mgr._shutdown = True
        risk_mgr.reset_daily()

        assert not risk_mgr.is_shutdown

    def test_reset_clears_cooldowns(self, risk_mgr):
        """Reset should clear all cooldowns."""
        risk_mgr._cooldowns["SPY"] = (2, datetime.now() + timedelta(minutes=30))
        risk_mgr.reset_daily()

        assert len(risk_mgr._cooldowns) == 0

    def test_reset_clears_counters(self, risk_mgr):
        """Reset should zero all daily counters."""
        risk_mgr._daily_stats.total_trades = 10
        risk_mgr._daily_stats.winning_trades = 7
        risk_mgr._daily_stats.losing_trades = 3
        risk_mgr.reset_daily()

        assert risk_mgr._daily_stats.total_trades == 0
        assert risk_mgr._daily_stats.winning_trades == 0
        assert risk_mgr._daily_stats.losing_trades == 0

    def test_reset_updates_balance(self, risk_mgr):
        """Reset with new balance should update the account balance."""
        risk_mgr.reset_daily(new_balance=120_000.0)

        assert risk_mgr.account_balance == 120_000.0
        assert risk_mgr.daily_loss_cap == 2_400.0  # 2% of 120k

    def test_reset_keeps_balance_if_none(self, risk_mgr):
        """Reset without new_balance should keep current balance."""
        risk_mgr.reset_daily()
        assert risk_mgr.account_balance == 100_000.0


# ── Integration-Style Tests ──────────────────────────────────────


class TestRiskManagerIntegration:
    """Test realistic sequences of risk manager operations."""

    def test_full_day_scenario(self, risk_mgr, mock_pm):
        """Simulate a full trading day with mixed results."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Morning: two wins on SPY
        for _ in range(2):
            win = _make_position("SPY", pnl=150.0)
            risk_mgr.record_trade_result(win)

        # SPY should still be tradeable (no losses)
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

        # Midday: two losses on QQQ → recent loser block
        for _ in range(2):
            loss = _make_position("QQQ", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # QQQ should be blocked by recent loser
        result = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason

        # SPY should still be fine
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

        # Generate report
        report = risk_mgr.generate_daily_report()
        assert report.total_trades == 4
        assert report.winning_trades == 2
        assert report.losing_trades == 2
