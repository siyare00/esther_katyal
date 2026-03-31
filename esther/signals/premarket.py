"""Pre-Market Research System — 4:00 AM to 9:30 AM ET Pipeline.

Runs the complete pre-market analysis pipeline that prepares everything
Esther needs before market open. Pulls flow data, builds key levels,
checks the economic calendar, scans the LEAP watchlist, and generates
a trade plan with recommended pillars and strike zones.

Schedule (all times ET):
    4:00 AM  — Wake up, start monitoring
    6:00 AM  — Pull overnight flow data, dark pool, check futures
    7:00 AM  — Build initial key levels (PM high/low developing)
    8:00 AM  — Full flow analysis, check economic calendar
    8:30 AM  — DATA CANDLE — watch for CPI/PPI/NFP releases
    9:00 AM  — Final bias computation, set trade plan
    9:15 AM  — Generate PreMarketReport and publish to engine
    9:30 AM  — Market open — hand off to main engine
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config, env
from esther.data.tradier import TradierClient
from esther.signals.calendar import CalendarModule, EventImpact
from esther.signals.flow import (
    FlowAnalyzer,
    FlowOptionType,
)
from esther.signals.levels import LevelTracker
from esther.signals.regime import RegimeDetector
from esther.signals.watchlist import BuyZoneStatus, WatchlistMonitor

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")

# Default symbols to track
DEFAULT_SYMBOLS = ["SPY", "SPX", "QQQ"]


# ---------------------------------------------------------------------------
# PreMarketReport — delivered to the engine at 9:15 AM
# ---------------------------------------------------------------------------


class PreMarketReport(BaseModel):
    """Complete pre-market analysis delivered to the engine at 9:15 AM."""

    generated_at: datetime

    # Market overview
    spy_price: float = 0.0
    spx_price: float = 0.0
    vix_level: float = 0.0
    futures_direction: str = ""  # "UP", "DOWN", "FLAT"
    overnight_range_pct: float = 0.0

    # Key levels for the day (6 levels per SuperLuckeee)
    key_levels: dict[str, dict] = Field(default_factory=dict)
    # {symbol: {pm_high, pm_low, prev_high, prev_low, prev_close, sma_200, sma_50}}

    # Flow analysis
    flow_direction: str = ""  # "BULLISH", "BEARISH", "NEUTRAL"
    flow_bias_score: float = 0.0
    top_flow_alerts: list[dict] = Field(default_factory=list)
    dark_pool_summary: str = ""
    put_call_ratio: float = 0.0
    max_pain: dict[str, float] = Field(default_factory=dict)

    # Regime
    regime_state: str = ""  # "BULLISH", "BEARISH", "TRANSITIONING"
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0

    # Calendar
    is_event_day: bool = False
    event_name: str = ""
    expected_move: float = 0.0

    # LEAP watchlist alerts
    watchlist_in_zone: list[str] = Field(default_factory=list)
    watchlist_approaching: list[str] = Field(default_factory=list)

    # Trade plan
    recommended_pillars: list[int] = Field(default_factory=list)
    recommended_direction: str = ""  # "BULL", "BEAR", "NEUTRAL"
    call_zones: str = ""
    put_zones: str = ""
    ic_strikes: str = ""
    confidence: float = 0.0

    # Risk adjustments
    sizing_modifier: float = 1.0
    vix_regime: str = ""  # "IC_SWEET_SPOT", "NORMAL", "ELEVATED", "PANIC"
    chop_warning: bool = False


# ---------------------------------------------------------------------------
# PreMarketResearcher
# ---------------------------------------------------------------------------


class PreMarketResearcher:
    """Runs the pre-market analysis pipeline from 4 AM to 9:30 AM ET.

    Coordinates all signal modules (flow, levels, regime, calendar,
    watchlist) into a single PreMarketReport that drives the trading
    engine's decisions for the day.

    Usage::

        researcher = PreMarketResearcher()
        report = await researcher.run_full_scan()
        telegram_msg = researcher.format_telegram_report(report)
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        flow_analyzer: FlowAnalyzer | None = None,
        level_tracker: LevelTracker | None = None,
        regime_detector: RegimeDetector | None = None,
        calendar_module: CalendarModule | None = None,
        watchlist_monitor: WatchlistMonitor | None = None,
    ) -> None:
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._flow = flow_analyzer or FlowAnalyzer()
        self._levels = level_tracker or LevelTracker()
        self._regime = regime_detector or RegimeDetector()
        self._calendar = calendar_module or CalendarModule()
        self._watchlist = watchlist_monitor or WatchlistMonitor()
        self._cfg = config()
        self._env = env()

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def run_full_scan(self) -> PreMarketReport:
        """Run the complete pre-market analysis pipeline.

        Executes all sub-scans in order and assembles the final report.
        Designed to be called at ~9:00-9:15 AM ET after data has settled.

        Returns:
            PreMarketReport with all fields populated.
        """
        report = PreMarketReport(generated_at=datetime.now(ET))

        # 1. Fetch current quotes for market overview
        try:
            await self._fetch_market_overview(report)
        except Exception as exc:
            logger.error("premarket_market_overview_failed", error=str(exc))

        # 2. Overnight flow + dark pool
        try:
            flow_data = await self.check_overnight_flow()
            report.top_flow_alerts = flow_data.get("top_alerts", [])
            report.dark_pool_summary = flow_data.get("dark_pool_summary", "")
            report.put_call_ratio = flow_data.get("put_call_ratio", 0.0)
        except Exception as exc:
            logger.error("premarket_overnight_flow_failed", error=str(exc))

        # 3. Build key levels
        try:
            levels_data = await self.build_daily_levels(self._symbols)
            report.key_levels = levels_data
        except Exception as exc:
            logger.error("premarket_levels_failed", error=str(exc))

        # 4. Flow direction analysis
        try:
            direction, score = await self.analyze_flow_direction()
            report.flow_direction = direction
            report.flow_bias_score = score
        except Exception as exc:
            logger.error("premarket_flow_analysis_failed", error=str(exc))

        # 5. Max pain
        try:
            await self._fetch_max_pain(report)
        except Exception as exc:
            logger.error("premarket_max_pain_failed", error=str(exc))

        # 6. Regime detection
        try:
            await self._detect_regime(report)
        except Exception as exc:
            logger.error("premarket_regime_failed", error=str(exc))

        # 7. Economic calendar
        try:
            cal_data = await self.check_economic_calendar()
            report.is_event_day = cal_data.get("is_event_day", False)
            report.event_name = cal_data.get("event_name", "")
            report.expected_move = cal_data.get("expected_move", 0.0)
        except Exception as exc:
            logger.error("premarket_calendar_failed", error=str(exc))

        # 8. LEAP watchlist scan
        try:
            wl_data = await self.scan_watchlist()
            report.watchlist_in_zone = wl_data.get("in_zone", [])
            report.watchlist_approaching = wl_data.get("approaching", [])
        except Exception as exc:
            logger.error("premarket_watchlist_failed", error=str(exc))

        # 9. Generate trade plan (uses all the above)
        try:
            report = await self.generate_trade_plan(report)
        except Exception as exc:
            logger.error("premarket_trade_plan_failed", error=str(exc))

        logger.info(
            "premarket_scan_complete",
            spy=report.spy_price,
            vix=report.vix_level,
            direction=report.flow_direction,
            confidence=report.confidence,
            pillars=report.recommended_pillars,
        )
        return report

    # ------------------------------------------------------------------
    # Sub-scan: Market overview (quotes)
    # ------------------------------------------------------------------

    async def _fetch_market_overview(self, report: PreMarketReport) -> None:
        """Fetch SPY, SPX, VIX quotes and derive futures direction."""
        async with TradierClient() as client:
            quotes = await client.get_quotes(["SPY", "SPX", "VIX"])

        quote_map = {q.symbol: q for q in quotes}

        spy_q = quote_map.get("SPY")
        spx_q = quote_map.get("SPX")
        vix_q = quote_map.get("VIX")

        if spy_q:
            report.spy_price = spy_q.last
            # Derive futures direction from change
            if spy_q.change > 0.15:
                report.futures_direction = "UP"
            elif spy_q.change < -0.15:
                report.futures_direction = "DOWN"
            else:
                report.futures_direction = "FLAT"
            # Overnight range as pct of close
            if spy_q.close and spy_q.close > 0:
                day_range = spy_q.high - spy_q.low
                report.overnight_range_pct = round(
                    (day_range / spy_q.close) * 100, 2
                )

        if spx_q:
            report.spx_price = spx_q.last

        if vix_q:
            report.vix_level = vix_q.last

        logger.info(
            "market_overview_fetched",
            spy=report.spy_price,
            spx=report.spx_price,
            vix=report.vix_level,
            futures=report.futures_direction,
        )

    # ------------------------------------------------------------------
    # Sub-scan: Overnight flow
    # ------------------------------------------------------------------

    async def check_overnight_flow(self) -> dict:
        """Pull UW flow alerts and dark pool from overnight session.

        Returns:
            Dict with top_alerts, dark_pool_summary, put_call_ratio.
        """
        result: dict[str, Any] = {
            "top_alerts": [],
            "dark_pool_summary": "",
            "put_call_ratio": 0.0,
        }

        # Flow alerts for SPY/SPX
        all_entries = []
        for symbol in ["SPY", "SPX"]:
            entries = await self._flow.get_flow(symbol)
            all_entries.extend(entries)

        if all_entries:
            summary = self._flow.analyze_flow(all_entries)
            result["put_call_ratio"] = summary.put_call_ratio

            # Top 5 alerts by premium
            top = sorted(all_entries, key=lambda e: e.premium, reverse=True)[:5]
            result["top_alerts"] = [
                {
                    "symbol": e.symbol,
                    "strike": e.strike,
                    "expiry": e.expiry,
                    "type": e.option_type.value,
                    "premium": e.premium,
                    "side": e.side.value,
                    "has_sweep": e.has_sweep,
                }
                for e in top
            ]

        # Dark pool
        dp_entries = await self._flow.get_dark_pool(limit=20)
        if dp_entries:
            # Summarize the biggest dark pool prints
            top_dp = sorted(dp_entries, key=lambda d: d.size, reverse=True)[:3]
            parts = []
            for dp in top_dp:
                direction = "buying" if dp.price >= dp.nbbo_ask else "selling"
                parts.append(
                    f"{dp.ticker} {direction} at ${dp.price:.2f} ({dp.size:,} shares)"
                )
            result["dark_pool_summary"] = "; ".join(parts)

        logger.info(
            "overnight_flow_checked",
            alert_count=len(result["top_alerts"]),
            pcr=result["put_call_ratio"],
            dark_pool=result["dark_pool_summary"][:80],
        )
        return result

    # ------------------------------------------------------------------
    # Sub-scan: Key levels
    # ------------------------------------------------------------------

    async def build_daily_levels(self, symbols: list[str]) -> dict:
        """Calculate all 6 key levels for each symbol.

        Uses Tradier for intraday (premarket) bars and daily bars.
        Returns dict of {symbol: {pm_high, pm_low, prev_high, prev_low,
        prev_close, sma_200, sma_50}}.
        """
        levels_data: dict[str, dict] = {}
        today = datetime.now(ET).date()

        async with TradierClient() as client:
            for symbol in symbols:
                try:
                    # Daily bars for SMA + prev day levels (need 200+ days)
                    start_date = today - timedelta(days=365)
                    daily_bars = await client.get_bars(
                        symbol, interval="daily", start=start_date, end=today
                    )

                    # Intraday bars for premarket levels
                    intraday_bars = await client.get_bars(
                        symbol,
                        interval="5min",
                        start=today,
                        end=today,
                    )

                    # Build via LevelTracker
                    key_lvls = self._levels.build_levels(
                        symbol=symbol,
                        intraday_bars=intraday_bars,
                        daily_bars=daily_bars,
                    )

                    levels_data[symbol] = {
                        "pm_high": key_lvls.premarket_high,
                        "pm_low": key_lvls.premarket_low,
                        "prev_high": key_lvls.prev_day_high,
                        "prev_low": key_lvls.prev_day_low,
                        "prev_close": key_lvls.prev_day_close,
                        "sma_200": key_lvls.sma_200,
                        "sma_50": key_lvls.sma_50,
                    }

                except Exception as exc:
                    logger.error(
                        "build_levels_failed", symbol=symbol, error=str(exc)
                    )
                    levels_data[symbol] = {}

        logger.info("daily_levels_built", symbols=list(levels_data.keys()))
        return levels_data

    # ------------------------------------------------------------------
    # Sub-scan: Flow direction
    # ------------------------------------------------------------------

    async def analyze_flow_direction(self) -> tuple[str, float]:
        """Determine overall flow direction and bias score.

        Aggregates flow from SPY + SPX and returns the direction
        label and the raw score (-100 to +100).
        """
        all_entries = []
        for symbol in ["SPY", "SPX"]:
            entries = await self._flow.get_flow(symbol)
            all_entries.extend(entries)

        if not all_entries:
            return "NEUTRAL", 0.0

        summary = self._flow.analyze_flow(all_entries)
        score = summary.flow_bias_score

        if score > 15:
            direction = "BULLISH"
        elif score < -15:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        logger.info(
            "flow_direction_analyzed",
            direction=direction,
            score=round(score, 1),
            call_prem=summary.total_call_premium,
            put_prem=summary.total_put_premium,
        )
        return direction, round(score, 1)

    # ------------------------------------------------------------------
    # Sub-scan: Max pain
    # ------------------------------------------------------------------

    async def _fetch_max_pain(self, report: PreMarketReport) -> None:
        """Fetch max pain data for tracked symbols."""
        for symbol in self._symbols:
            mp = await self._flow.get_max_pain(symbol)
            if mp:
                report.max_pain[symbol] = mp.max_pain

    # ------------------------------------------------------------------
    # Sub-scan: Regime
    # ------------------------------------------------------------------

    async def _detect_regime(self, report: PreMarketReport) -> None:
        """Detect market regime from SPY daily bars."""
        async with TradierClient() as client:
            today = datetime.now(ET).date()
            start = today - timedelta(days=365)
            daily_bars = await client.get_bars(
                "SPY", interval="daily", start=start, end=today
            )

        if daily_bars:
            result = self._regime.detect_regime(daily_bars)
            report.regime_state = result.state.value
            report.sma_20 = result.sma_fast
            report.sma_50 = result.sma_slow

            # Also grab 200SMA from level tracker
            sma_200 = self._levels.calculate_sma(daily_bars, 200)
            if sma_200:
                report.sma_200 = sma_200

    # ------------------------------------------------------------------
    # Sub-scan: Economic calendar
    # ------------------------------------------------------------------

    async def check_economic_calendar(self) -> dict:
        """Check if today is FOMC/CPI/PPI/NFP and get expected move.

        Returns:
            Dict with is_event_day, event_name, expected_move, sizing_modifier.
        """
        today = datetime.now(ET).date()
        events = self._calendar.get_events_today(today)
        is_event = self._calendar.is_event_day(today)
        should_reduce, modifier = self._calendar.should_reduce_size(today)

        event_name = ""
        if events:
            # Pick the highest-impact event name
            high_events = [
                e for e in events if e.impact == EventImpact.HIGH
            ]
            if high_events:
                event_name = high_events[0].name
            else:
                event_name = events[0].name

        # Expected move from VIX (will be updated later with actual VIX)
        # Use a placeholder — the caller sets real VIX after market overview
        expected_move = 0.0

        return {
            "is_event_day": is_event,
            "event_name": event_name,
            "expected_move": expected_move,
            "sizing_modifier": modifier,
        }

    # ------------------------------------------------------------------
    # Sub-scan: LEAP watchlist
    # ------------------------------------------------------------------

    async def scan_watchlist(self) -> dict:
        """Check LEAP watchlist for buy zone entries.

        Fetches current prices for all watchlist symbols via Tradier,
        then scans for stocks in or approaching their buy zones.

        Returns:
            Dict with in_zone and approaching symbol lists.
        """
        symbols = [
            e.symbol
            for e in self._watchlist.watchlist
            if e.buy_zone_high > 0
        ]
        if not symbols:
            return {"in_zone": [], "approaching": []}

        prices: dict[str, float] = {}
        try:
            async with TradierClient() as client:
                quotes = await client.get_quotes(symbols)
            for q in quotes:
                if q.last > 0:
                    prices[q.symbol] = q.last
        except Exception as exc:
            logger.error("watchlist_quotes_failed", error=str(exc))
            return {"in_zone": [], "approaching": []}

        alerts = self._watchlist.scan(prices)

        in_zone = [
            a.symbol for a in alerts if a.status == BuyZoneStatus.IN_ZONE
        ]
        approaching = [
            a.symbol
            for a in alerts
            if a.status == BuyZoneStatus.ENTERING_ZONE
        ]

        # Also include previously-identified stocks still in zone
        for entry in self._watchlist.get_in_zone():
            if entry.symbol not in in_zone:
                in_zone.append(entry.symbol)
        for entry in self._watchlist.get_approaching():
            if entry.symbol not in approaching:
                approaching.append(entry.symbol)

        logger.info(
            "watchlist_scanned",
            in_zone=in_zone,
            approaching=approaching,
        )
        return {"in_zone": in_zone, "approaching": approaching}

    # ------------------------------------------------------------------
    # Trade plan generation
    # ------------------------------------------------------------------

    async def generate_trade_plan(
        self, report: PreMarketReport
    ) -> PreMarketReport:
        """Set recommended pillars, directions, and strike zones.

        Uses VIX level, flow direction, regime, and calendar to determine
        the optimal trading plan for the day.

        Args:
            report: Partially filled PreMarketReport.

        Returns:
            Same report with trade plan fields populated.
        """
        vix = report.vix_level
        flow_score = report.flow_bias_score

        # --- VIX regime classification ---
        if 18 <= vix <= 25:
            report.vix_regime = "IC_SWEET_SPOT"
        elif vix < 18:
            report.vix_regime = "NORMAL"
        elif 25 < vix <= 35:
            report.vix_regime = "ELEVATED"
        else:
            report.vix_regime = "PANIC"

        # --- Expected move from VIX ---
        if vix > 0 and report.spx_price > 0:
            daily_vol = vix / math.sqrt(252)
            report.expected_move = round(
                report.spx_price * daily_vol / 100, 1
            )

        # --- Sizing modifier ---
        if report.is_event_day:
            report.sizing_modifier = 0.5
        elif report.vix_regime == "PANIC":
            report.sizing_modifier = 0.25
        elif report.vix_regime == "ELEVATED":
            report.sizing_modifier = 0.75
        else:
            report.sizing_modifier = 1.0

        # --- Chop warning ---
        # Chop = neutral flow + no clear regime + range-bound overnight
        if (
            abs(flow_score) < 10
            and report.overnight_range_pct < 0.5
            and report.regime_state != "TRANSITIONING"
        ):
            report.chop_warning = True

        # --- Pillar selection ---
        # P1 (Iron Condor) — VIX in sweet spot or elevated, neutral-ish flow
        # P2 (Bear Put Spread) — bearish flow
        # P3 (Bull Call Spread) — bullish flow
        # P4 (Directional momentum) — strong directional signal

        pillars: list[int] = []

        if report.vix_regime in ("IC_SWEET_SPOT", "ELEVATED"):
            pillars.append(1)  # ICs are primary when VIX is juicy

        if flow_score < -20:
            pillars.append(2)  # Bear spread
            if flow_score < -50:
                pillars.append(4)  # Strong bear momentum
        elif flow_score > 20:
            pillars.append(3)  # Bull spread
            if flow_score > 50:
                pillars.append(4)  # Strong bull momentum

        # Default to P1 if nothing else selected
        if not pillars:
            pillars.append(1)

        report.recommended_pillars = sorted(set(pillars))

        # --- Direction ---
        if flow_score > 15:
            report.recommended_direction = "BULL"
        elif flow_score < -15:
            report.recommended_direction = "BEAR"
        else:
            report.recommended_direction = "NEUTRAL"

        # --- Strike zones ---
        spy_levels = report.key_levels.get("SPY", {})
        spy_price = report.spy_price

        if spy_price > 0:
            # Round to nearest 5 for clean zones
            base = round(spy_price / 5) * 5

            # Call zone: above current price
            call_low = base + 5
            call_high = call_low + 5
            report.call_zones = f"SPY calls {call_low}-{call_high}"

            # Put zone: below current price
            put_high = base - 5
            put_low = put_high - 5
            report.put_zones = f"SPY puts {put_high}-{put_low}"

            # IC strikes: use expected move for short strikes
            if report.expected_move > 0:
                em = report.expected_move
                # SPX IC — short strikes at ~1 expected move, wings 10 wide
                spx_price = report.spx_price or spy_price * 10
                short_call = round((spx_price + em) / 10) * 10
                long_call = short_call + 10
                short_put = round((spx_price - em) / 10) * 10
                long_put = short_put - 10
                report.ic_strikes = (
                    f"IC: {short_call}/{long_call} calls, "
                    f"{short_put}/{long_put} puts"
                )

        # --- Confidence ---
        # Base confidence from flow conviction
        confidence = min(abs(flow_score) / 100, 0.5) + 0.3

        # Calendar adjustment
        cal_adj = self._calendar.get_confidence_adjustment()
        confidence *= cal_adj

        # Regime alignment boost
        if (
            report.regime_state == "BULLISH"
            and report.recommended_direction == "BULL"
        ) or (
            report.regime_state == "BEARISH"
            and report.recommended_direction == "BEAR"
        ):
            confidence += 0.1

        # Chop penalty
        if report.chop_warning:
            confidence *= 0.7

        report.confidence = round(min(max(confidence, 0.0), 1.0), 2)

        logger.info(
            "trade_plan_generated",
            pillars=report.recommended_pillars,
            direction=report.recommended_direction,
            vix_regime=report.vix_regime,
            confidence=report.confidence,
            sizing=report.sizing_modifier,
        )
        return report

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram_report(self, report: PreMarketReport) -> str:
        """Format the report as a Telegram message for Shawn.

        Produces a concise, emoji-rich summary of the pre-market analysis
        that fits well in a Telegram chat.

        Args:
            report: Completed PreMarketReport.

        Returns:
            Formatted string ready for Telegram.
        """
        now = report.generated_at
        day_str = now.strftime("%a %b %d")

        # VIX emoji
        if report.vix_level >= 30:
            vix_emoji = "🔴"
        elif report.vix_level >= 20:
            vix_emoji = "🟡"
        else:
            vix_emoji = "🟢"

        # Futures direction emoji
        futures_emoji = {
            "UP": "🟢",
            "DOWN": "🔴",
            "FLAT": "⚪",
        }.get(report.futures_direction, "⚪")

        lines = [
            f"⚡ ESTHER PRE-MARKET REPORT — {day_str}",
            "",
            "📊 MARKET",
            f"SPY: ${report.spy_price:.2f} | SPX: ${report.spx_price:,.0f} | VIX: {report.vix_level:.2f} {vix_emoji}",
            f"Futures: {report.futures_direction} {futures_emoji} | Overnight range: {report.overnight_range_pct:.1f}%",
        ]

        # Key levels (SPY)
        spy_lvls = report.key_levels.get("SPY", {})
        if spy_lvls:
            lines.append("")
            lines.append("📈 KEY LEVELS (SPY)")
            pm_high = spy_lvls.get("pm_high")
            pm_low = spy_lvls.get("pm_low")
            if pm_high is not None and pm_low is not None:
                lines.append(
                    f"PM High: ${pm_high:.2f} | PM Low: ${pm_low:.2f}"
                )
            prev_close = spy_lvls.get("prev_close")
            sma_200 = spy_lvls.get("sma_200")
            if prev_close is not None:
                sma_str = f" | 200SMA: ${sma_200:.0f}" if sma_200 else ""
                lines.append(f"Prev Close: ${prev_close:.2f}{sma_str}")
            spy_mp = report.max_pain.get("SPY")
            if spy_mp:
                lines.append(f"Max Pain: ${spy_mp:.0f}")

        # Flow
        lines.append("")
        lines.append("🐋 FLOW")
        lines.append(
            f"Direction: {report.flow_direction} ({report.flow_bias_score:+.1f})"
        )
        if report.put_call_ratio > 0:
            lines.append(f"Put/Call Ratio: {report.put_call_ratio:.2f}")
        if report.top_flow_alerts:
            top = report.top_flow_alerts[:3]
            alert_parts = []
            for a in top:
                strike = a.get("strike", 0)
                prem_k = a.get("premium", 0) / 1000
                otype = a.get("type", "?")[0].upper()
                alert_parts.append(
                    f"{a.get('symbol', '?')} {strike:.0f}{otype} ${prem_k:.0f}K"
                )
            lines.append(f"Top: {', '.join(alert_parts)}")
        if report.dark_pool_summary:
            lines.append(f"Dark pool: {report.dark_pool_summary}")

        # Calendar
        lines.append("")
        lines.append("📅 CALENDAR")
        if report.is_event_day:
            lines.append(f"⚠️ {report.event_name}")
            if report.expected_move > 0:
                lines.append(f"Expected move: ±{report.expected_move:.0f} pts")
        else:
            lines.append("No major events today")

        # Trade plan
        lines.append("")
        lines.append("🎯 TRADE PLAN")
        pillar_names = {
            1: "P1 (IC)",
            2: "P2 (Bear Spread)",
            3: "P3 (Bull Spread)",
            4: "P4 (Momentum)",
        }
        pillar_str = ", ".join(
            pillar_names.get(p, f"P{p}") for p in report.recommended_pillars
        )
        vix_note = ""
        if report.vix_regime == "IC_SWEET_SPOT":
            vix_note = f" — VIX {report.vix_level:.0f} = IC sweet spot"
        elif report.vix_regime == "PANIC":
            vix_note = f" — VIX {report.vix_level:.0f} ⚠️ PANIC"

        lines.append(f"Pillar: {pillar_str}{vix_note}")

        if report.ic_strikes:
            lines.append(f"IC Zone: {report.ic_strikes.replace('IC: ', '')}")
        if report.recommended_direction == "BULL" and report.call_zones:
            lines.append(f"Calls: {report.call_zones}")
        elif report.recommended_direction == "BEAR" and report.put_zones:
            lines.append(f"Puts: {report.put_zones}")

        # Sizing
        size_label = {
            1.0: "full",
            0.75: "reduced",
            0.5: "half (event day)",
            0.25: "quarter (panic)",
        }.get(report.sizing_modifier, f"{report.sizing_modifier:.0%}")
        lines.append(f"Size: {size_label}")
        lines.append(f"Confidence: {report.confidence:.0%}")

        if report.chop_warning:
            lines.append("⚠️ CHOP WARNING — low conviction, tight stops")

        # LEAP watchlist
        if report.watchlist_in_zone or report.watchlist_approaching:
            lines.append("")
            lines.append("📋 LEAP WATCH")
            for sym in report.watchlist_in_zone:
                entry = self._find_watchlist_entry(sym)
                if entry:
                    lines.append(
                        f"🟢 {sym} IN BUY ZONE "
                        f"(${entry.current_price:.0f} → "
                        f"${entry.buy_zone_low:.0f}-{entry.buy_zone_high:.0f})"
                    )
                else:
                    lines.append(f"🟢 {sym} IN BUY ZONE")
            for sym in report.watchlist_approaching:
                entry = self._find_watchlist_entry(sym)
                if entry:
                    lines.append(
                        f"🟡 {sym} approaching buy zone "
                        f"(${entry.current_price:.0f} → "
                        f"${entry.buy_zone_low:.0f}-{entry.buy_zone_high:.0f})"
                    )
                else:
                    lines.append(f"🟡 {sym} approaching buy zone")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_watchlist_entry(self, symbol: str) -> Any:
        """Find a watchlist entry by symbol."""
        for entry in self._watchlist.watchlist:
            if entry.symbol == symbol:
                return entry
        return None
