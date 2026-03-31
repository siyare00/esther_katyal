"""Tests for the PreMarketResearcher and PreMarketReport."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from esther.signals.premarket import PreMarketReport, PreMarketResearcher

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# PreMarketReport model tests
# ---------------------------------------------------------------------------


class TestPreMarketReport:
    def test_default_report(self):
        report = PreMarketReport(generated_at=datetime.now(ET))
        assert report.spy_price == 0.0
        assert report.flow_direction == ""
        assert report.confidence == 0.0
        assert report.sizing_modifier == 1.0
        assert report.chop_warning is False
        assert report.key_levels == {}
        assert report.top_flow_alerts == []
        assert report.watchlist_in_zone == []

    def test_report_with_values(self):
        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=634.09,
            spx_price=6368.0,
            vix_level=31.05,
            futures_direction="DOWN",
            flow_direction="BEARISH",
            flow_bias_score=-14.1,
            confidence=0.72,
            recommended_pillars=[1],
            vix_regime="IC_SWEET_SPOT",
        )
        assert report.spy_price == 634.09
        assert report.flow_direction == "BEARISH"
        assert report.recommended_pillars == [1]


# ---------------------------------------------------------------------------
# PreMarketResearcher tests
# ---------------------------------------------------------------------------


@pytest.fixture
def researcher():
    """Create a PreMarketResearcher with mocked dependencies."""
    flow = MagicMock()
    levels = MagicMock()
    regime = MagicMock()
    calendar = MagicMock()
    watchlist = MagicMock()

    r = PreMarketResearcher(
        symbols=["SPY", "SPX"],
        flow_analyzer=flow,
        level_tracker=levels,
        regime_detector=regime,
        calendar_module=calendar,
        watchlist_monitor=watchlist,
    )
    return r


class TestAnalyzeFlowDirection:
    @pytest.mark.asyncio
    async def test_neutral_when_no_entries(self, researcher):
        researcher._flow.get_flow = AsyncMock(return_value=[])

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "NEUTRAL"
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_bullish_direction(self, researcher):
        mock_summary = MagicMock()
        mock_summary.flow_bias_score = 35.0
        mock_summary.total_call_premium = 1_000_000
        mock_summary.total_put_premium = 200_000

        researcher._flow.get_flow = AsyncMock(return_value=[MagicMock()])
        researcher._flow.analyze_flow = MagicMock(return_value=mock_summary)

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "BULLISH"
        assert score == 35.0

    @pytest.mark.asyncio
    async def test_bearish_direction(self, researcher):
        mock_summary = MagicMock()
        mock_summary.flow_bias_score = -30.0
        mock_summary.total_call_premium = 200_000
        mock_summary.total_put_premium = 1_000_000

        researcher._flow.get_flow = AsyncMock(return_value=[MagicMock()])
        researcher._flow.analyze_flow = MagicMock(return_value=mock_summary)

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "BEARISH"
        assert score == -30.0


class TestCheckEconomicCalendar:
    @pytest.mark.asyncio
    async def test_no_events(self, researcher):
        researcher._calendar.get_events_today = MagicMock(return_value=[])
        researcher._calendar.is_event_day = MagicMock(return_value=False)
        researcher._calendar.should_reduce_size = MagicMock(
            return_value=(False, 1.0)
        )

        result = await researcher.check_economic_calendar()
        assert result["is_event_day"] is False
        assert result["event_name"] == ""

    @pytest.mark.asyncio
    async def test_cpi_event_day(self, researcher):
        mock_event = MagicMock()
        mock_event.name = "CPI Release"
        mock_event.impact = MagicMock()
        mock_event.impact.value = "HIGH"
        # Make it match EventImpact.HIGH comparison
        from esther.signals.calendar import EventImpact

        mock_event.impact = EventImpact.HIGH

        researcher._calendar.get_events_today = MagicMock(
            return_value=[mock_event]
        )
        researcher._calendar.is_event_day = MagicMock(return_value=True)
        researcher._calendar.should_reduce_size = MagicMock(
            return_value=(True, 0.5)
        )

        result = await researcher.check_economic_calendar()
        assert result["is_event_day"] is True
        assert result["event_name"] == "CPI Release"
        assert result["sizing_modifier"] == 0.5


class TestGenerateTradePlan:
    @pytest.mark.asyncio
    async def test_ic_sweet_spot(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=634.0,
            spx_price=6340.0,
            vix_level=22.0,
            flow_bias_score=5.0,
            regime_state="BULLISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "IC_SWEET_SPOT"
        assert 1 in result.recommended_pillars
        assert result.recommended_direction == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_bearish_plan(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=620.0,
            spx_price=6200.0,
            vix_level=32.0,
            flow_bias_score=-55.0,
            regime_state="BEARISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "ELEVATED"
        assert result.recommended_direction == "BEAR"
        assert 2 in result.recommended_pillars
        assert 4 in result.recommended_pillars  # strong bear momentum

    @pytest.mark.asyncio
    async def test_panic_sizing(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=580.0,
            spx_price=5800.0,
            vix_level=42.0,
            flow_bias_score=-10.0,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "PANIC"
        assert result.sizing_modifier == 0.25

    @pytest.mark.asyncio
    async def test_event_day_sizing(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=0.65
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            flow_bias_score=0.0,
            is_event_day=True,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.sizing_modifier == 0.5

    @pytest.mark.asyncio
    async def test_chop_warning(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            flow_bias_score=3.0,
            overnight_range_pct=0.3,
            regime_state="BULLISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.chop_warning is True
        # Confidence should be reduced by chop penalty
        assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_ic_strikes_generated(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=22.0,
            flow_bias_score=0.0,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.ic_strikes != ""
        assert "calls" in result.ic_strikes
        assert "puts" in result.ic_strikes


class TestFormatTelegramReport:
    def test_basic_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=634.09,
            spx_price=6368.0,
            vix_level=31.05,
            futures_direction="DOWN",
            overnight_range_pct=0.8,
            key_levels={
                "SPY": {
                    "pm_high": 636.50,
                    "pm_low": 632.10,
                    "prev_close": 645.09,
                    "prev_high": 646.0,
                    "prev_low": 641.0,
                    "sma_200": 661.0,
                    "sma_50": 650.0,
                }
            },
            flow_direction="BEARISH",
            flow_bias_score=-14.1,
            put_call_ratio=1.5,
            top_flow_alerts=[
                {
                    "symbol": "SPX",
                    "strike": 6480,
                    "type": "call",
                    "premium": 112000,
                    "side": "buy",
                    "has_sweep": False,
                },
            ],
            dark_pool_summary="NVDA selling at $166.60 (50,000 shares)",
            max_pain={"SPY": 653.0},
            regime_state="BEARISH",
            sma_20=640.0,
            sma_50=650.0,
            sma_200=661.0,
            is_event_day=False,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            ic_strikes="IC: 6440/6450 calls, 6300/6290 puts",
            confidence=0.72,
            sizing_modifier=1.0,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)

        assert "ESTHER PRE-MARKET REPORT" in msg
        assert "Tue Mar 31" in msg
        assert "$634.09" in msg
        assert "6,368" in msg
        assert "31.05" in msg
        assert "🔴" in msg  # VIX >= 30
        assert "BEARISH" in msg
        assert "PM High: $636.50" in msg
        assert "Max Pain: $653" in msg
        assert "SPX 6480C $112K" in msg
        assert "NVDA selling" in msg
        assert "IC: " in msg or "IC Zone:" in msg
        assert "72%" in msg

    def test_event_day_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=25.0,
            futures_direction="FLAT",
            is_event_day=True,
            event_name="CPI Release",
            expected_move=45.0,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.5,
            sizing_modifier=0.5,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "CPI Release" in msg
        assert "±45 pts" in msg
        assert "half (event day)" in msg

    def test_leap_watchlist_formatting(self, researcher):
        # Mock the watchlist entry lookup
        mock_entry = MagicMock()
        mock_entry.symbol = "NVDA"
        mock_entry.current_price = 166.0
        mock_entry.buy_zone_low = 155.0
        mock_entry.buy_zone_high = 165.0
        researcher._watchlist.watchlist = [mock_entry]

        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            futures_direction="UP",
            watchlist_approaching=["NVDA"],
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.6,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "LEAP WATCH" in msg
        assert "NVDA" in msg
        assert "approaching" in msg

    def test_chop_warning_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            futures_direction="FLAT",
            chop_warning=True,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.3,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "CHOP WARNING" in msg
