"""SAGE 🔭 — The Intel Officer. Always-on market intelligence gathering.

Sage is Esther's eyes and ears. While the other agents debate and judge
individual trades, Sage is constantly scanning the battlefield:

    - Sunday 8 PM: Weekly flow review + set weekly plan
    - Mon-Fri 6 AM: Overnight flow + dark pool changes
    - Mon-Fri 8:30 AM: Economic data releases (CPI/PPI/NFP)
    - Mon-Fri continuous: Real-time flow monitoring during market hours
    - Mon-Fri 4 PM: EOD summary + overnight positioning

Sage does NOT make trade decisions. He gathers, organizes, and delivers
intelligence to the team:
    - Kimi uses Sage's intel for risk analysis
    - Riki/Abi use Sage's intel to build their arguments
    - Kage uses Sage's intel for the final verdict

Sage is PURE DATA — no Claude API calls needed. Fast, cheap, always running.
He pulls from Unusual Whales, Tradier, economic calendars, and the LEAP watchlist.

The 5-agent structure:
    SAGE 🔭 (Intel) → KIMI 🔬 (Research/Risk) → RIKI 🐂 (Bull) + ABI 🐻 (Bear) → KIMI 🔬 (Challenge) → KAGE ⚖️ (Final)
"""

from __future__ import annotations

import json
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config, env
from esther.signals.flow import FlowAnalyzer, FlowSummary, UnusualWhalesClient
from esther.signals.levels import LevelTracker, KeyLevels
from esther.signals.regime import RegimeDetector, RegimeResult
from esther.signals.calendar import CalendarModule
from esther.signals.watchlist import WatchlistMonitor, WatchlistAlert
from esther.signals.premarket import PreMarketResearcher, PreMarketReport

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")

# Persistence
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INTEL_DIR = _PROJECT_ROOT / "data" / "intel"


class FlowIntel(BaseModel):
    """Flow intelligence snapshot."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(ET))
    spy_call_premium: float = 0.0
    spy_put_premium: float = 0.0
    spy_put_call_ratio: float = 0.0
    spy_bullish_premium: float = 0.0
    spy_bearish_premium: float = 0.0
    flow_bias_score: float = 0.0
    flow_direction: str = ""  # BULLISH, BEARISH, NEUTRAL
    max_pain_spy: float = 0.0
    net_delta: float = 0.0
    net_delta_direction: str = ""  # BULLISH or BEARISH
    top_alerts: list[dict] = []
    dark_pool_blocks: list[dict] = []
    sweep_count: int = 0
    floor_trade_count: int = 0


class MarketIntel(BaseModel):
    """Complete market intelligence package — Sage's output."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(ET))
    scan_type: str = ""  # "sunday_review", "overnight", "premarket", "intraday", "eod"

    # Prices
    spy_price: float = 0.0
    spx_price: float = 0.0
    vix_level: float = 0.0
    qqq_price: float = 0.0
    iwm_price: float = 0.0

    # Flow
    flow: FlowIntel = Field(default_factory=FlowIntel)

    # Levels
    key_levels: dict[str, Any] = {}  # {symbol: {pm_high, pm_low, etc}}

    # Regime
    regime_state: str = ""
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0

    # Calendar
    is_event_day: bool = False
    event_name: str = ""
    events_this_week: list[dict] = []

    # LEAP watchlist
    watchlist_alerts: list[str] = []
    watchlist_approaching: list[str] = []

    # Expected moves (from options pricing)
    spy_expected_move: float = 0.0
    spy_range_low: float = 0.0
    spy_range_high: float = 0.0
    spx_expected_move: float = 0.0
    spx_range_low: float = 0.0
    spx_range_high: float = 0.0

    # Sage's summary (plain text intelligence brief)
    intel_brief: str = ""

    # Risk flags
    risk_flags: list[str] = []


class Sage:
    """The Intel Officer — always-on market intelligence gathering.

    Sage runs continuously, pulling data from all sources and packaging
    it into MarketIntel objects that the rest of the team consumes.

    Usage:
        sage = Sage()
        intel = await sage.scan_now()  # Run appropriate scan for current time
        intel = await sage.sunday_review()  # Force Sunday review
        intel = await sage.overnight_scan()  # Force overnight scan
        brief = sage.get_latest_brief()  # Get last intel brief as text
    """

    def __init__(self):
        self._env = env()
        self._cfg = config()
        self._uw = UnusualWhalesClient(api_key=self._env.unusual_whales_api_key)
        self._flow = FlowAnalyzer()
        self._levels = LevelTracker()
        self._regime = RegimeDetector()
        self._calendar = CalendarModule()
        self._watchlist = WatchlistMonitor()
        self._premarket = PreMarketResearcher()

        self._latest_intel: MarketIntel | None = None
        self._intel_history: list[MarketIntel] = []

        # Ensure intel directory exists
        INTEL_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def latest(self) -> MarketIntel | None:
        """Get the most recent intelligence report."""
        return self._latest_intel

    # ── Auto-detect which scan to run ────────────────────────────

    async def scan_now(self) -> MarketIntel:
        """Run the appropriate scan based on current time.

        Sunday 6-10 PM → sunday_review
        Mon-Fri 4-9 AM → overnight_scan
        Mon-Fri 9-9:30 AM → premarket_scan
        Mon-Fri 9:30-4 PM → intraday_scan
        Mon-Fri 4-5 PM → eod_scan
        """
        now = datetime.now(ET)
        weekday = now.weekday()  # 0=Mon, 6=Sun
        hour = now.hour

        if weekday == 6 and hour >= 18:  # Sunday evening
            return await self.sunday_review()
        elif hour < 9:
            return await self.overnight_scan()
        elif hour == 9 and now.minute < 30:
            return await self.premarket_scan()
        elif 9 <= hour < 16:
            return await self.intraday_scan()
        elif hour >= 16:
            return await self.eod_scan()
        else:
            return await self.overnight_scan()

    # ── Sunday Night Review ──────────────────────────────────────

    async def sunday_review(self) -> MarketIntel:
        """Full weekly intelligence review — Sunday evening.

        Like @SuperLuckeee: "Every Sunday I plan the whole week
        based on orderflow and place bubbles where the flow is."
        """
        logger.info("sage_sunday_review_started")
        intel = MarketIntel(scan_type="sunday_review")

        # Pull Friday's flow data
        await self._populate_flow(intel)

        # Get prices from last close
        await self._populate_prices(intel)

        # Check economic calendar for the week
        await self._populate_calendar(intel)

        # Scan LEAP watchlist
        await self._populate_watchlist(intel)

        # Calculate expected moves
        self._calculate_expected_moves(intel)

        # Build the intel brief
        intel.intel_brief = self._build_sunday_brief(intel)

        # Risk flags
        self._assess_risk_flags(intel)

        # Store
        self._store_intel(intel)

        logger.info("sage_sunday_review_complete", brief_len=len(intel.intel_brief))
        return intel

    # ── Overnight Scan ───────────────────────────────────────────

    async def overnight_scan(self) -> MarketIntel:
        """Early morning scan — what happened overnight?"""
        logger.info("sage_overnight_scan_started")
        intel = MarketIntel(scan_type="overnight")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_calendar(intel)
        await self._populate_watchlist(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_overnight_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_overnight_scan_complete")
        return intel

    # ── Pre-Market Scan ──────────────────────────────────────────

    async def premarket_scan(self) -> MarketIntel:
        """9:00-9:30 AM scan — final intel before market open."""
        logger.info("sage_premarket_scan_started")
        intel = MarketIntel(scan_type="premarket")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_calendar(intel)
        await self._populate_watchlist(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_premarket_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_premarket_scan_complete")
        return intel

    # ── Intraday Scan ────────────────────────────────────────────

    async def intraday_scan(self) -> MarketIntel:
        """During market hours — quick flow check."""
        logger.info("sage_intraday_scan_started")
        intel = MarketIntel(scan_type="intraday")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_intraday_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        return intel

    # ── EOD Scan ─────────────────────────────────────────────────

    async def eod_scan(self) -> MarketIntel:
        """End of day — what happened today, what's the overnight setup?"""
        logger.info("sage_eod_scan_started")
        intel = MarketIntel(scan_type="eod")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_watchlist(intel)

        intel.intel_brief = self._build_eod_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_eod_scan_complete")
        return intel

    # ── Data Population Methods ──────────────────────────────────

    async def _populate_flow(self, intel: MarketIntel) -> None:
        """Pull flow data from Unusual Whales."""
        try:
            # SPY options volume
            vol_data = await self._uw.get_options_volume("SPY")
            if vol_data:
                v = vol_data[0] if isinstance(vol_data[0], dict) else {}
                intel.flow.spy_call_premium = float(v.get("call_premium", 0))
                intel.flow.spy_put_premium = float(v.get("put_premium", 0))
                intel.flow.spy_bullish_premium = float(v.get("bullish_premium", 0))
                intel.flow.spy_bearish_premium = float(v.get("bearish_premium", 0))

                if intel.flow.spy_call_premium > 0:
                    intel.flow.spy_put_call_ratio = intel.flow.spy_put_premium / intel.flow.spy_call_premium

            # Flow bias
            flow_entries = await self._flow.get_flow("SPY")
            if flow_entries:
                summary = self._flow.analyze_flow(flow_entries)
                intel.flow.flow_bias_score = summary.flow_bias_score

            # Direction
            if intel.flow.flow_bias_score > 20:
                intel.flow.flow_direction = "BULLISH"
            elif intel.flow.flow_bias_score < -20:
                intel.flow.flow_direction = "BEARISH"
            else:
                intel.flow.flow_direction = "NEUTRAL"

            # Max pain
            mp = await self._uw.get_max_pain("SPY")
            if mp:
                m = mp[0] if isinstance(mp, list) else mp
                if isinstance(m, dict):
                    intel.flow.max_pain_spy = float(m.get("max_pain", 0))

            # Greek exposure
            ge = await self._uw.get_greek_exposure("SPY")
            if ge:
                g = ge[0] if isinstance(ge, list) else ge
                if isinstance(g, dict):
                    cd = float(g.get("call_delta", 0))
                    pd = float(g.get("put_delta", 0))
                    intel.flow.net_delta = cd + pd
                    intel.flow.net_delta_direction = "BULLISH" if intel.flow.net_delta > 0 else "BEARISH"

            # Flow alerts
            alerts = await self._uw.get_flow_alerts(ticker="SPY", limit=5)
            intel.flow.top_alerts = [a if isinstance(a, dict) else {} for a in alerts[:5]]

            # Dark pool
            dp = await self._uw.get_dark_pool(limit=7)
            intel.flow.dark_pool_blocks = [d if isinstance(d, dict) else {} for d in dp[:7]]

            # Count sweeps and floor trades
            for a in alerts:
                if isinstance(a, dict):
                    if a.get("has_sweep"):
                        intel.flow.sweep_count += 1
                    if a.get("has_floor"):
                        intel.flow.floor_trade_count += 1

        except Exception as e:
            logger.error("sage_flow_error", error=str(e))

    async def _populate_prices(self, intel: MarketIntel) -> None:
        """Pull current/last close prices from Tradier."""
        try:
            live_key = self._env.tradier_live_api_key if hasattr(self._env, 'tradier_live_api_key') else self._env.tradier_api_key
            headers = {"Authorization": f"Bearer {live_key}", "Accept": "application/json"}

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.tradier.com/v1/markets/quotes",
                    headers=headers,
                    params={"symbols": "SPY,SPX,VIX,QQQ,IWM"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    quotes = data.get("quotes", {}).get("quote", [])
                    for q in quotes:
                        sym = q.get("symbol", "")
                        last = q.get("last", 0) or q.get("close", 0) or 0
                        if sym == "SPY":
                            intel.spy_price = float(last)
                        elif sym == "SPX":
                            intel.spx_price = float(last)
                        elif sym == "VIX":
                            intel.vix_level = float(last)
                        elif sym == "QQQ":
                            intel.qqq_price = float(last)
                        elif sym == "IWM":
                            intel.iwm_price = float(last)
        except Exception as e:
            logger.error("sage_price_error", error=str(e))

    async def _populate_calendar(self, intel: MarketIntel) -> None:
        """Check economic calendar."""
        try:
            event = self._calendar.get_todays_events()
            if event:
                intel.is_event_day = True
                intel.event_name = str(event)

            week_events = self._calendar.get_week_events()
            if week_events:
                intel.events_this_week = [
                    {"date": str(e.get("date", "")), "name": str(e.get("name", ""))}
                    for e in week_events
                ] if isinstance(week_events, list) else []
        except Exception as e:
            logger.warning("sage_calendar_error", error=str(e))

    async def _populate_watchlist(self, intel: MarketIntel) -> None:
        """Scan LEAP watchlist for buy zone alerts."""
        try:
            # Get prices for watchlist stocks
            live_key = self._env.tradier_live_api_key if hasattr(self._env, 'tradier_live_api_key') else self._env.tradier_api_key
            headers = {"Authorization": f"Bearer {live_key}", "Accept": "application/json"}

            symbols = [e.symbol for e in self._watchlist.watchlist if e.buy_zone_high > 0]
            if symbols:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://api.tradier.com/v1/markets/quotes",
                        headers=headers,
                        params={"symbols": ",".join(symbols)},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        quotes = data.get("quotes", {}).get("quote", [])
                        if isinstance(quotes, dict):
                            quotes = [quotes]
                        prices = {q["symbol"]: float(q.get("last", 0) or 0) for q in quotes}
                        self._watchlist.update_prices(prices)

                alerts = self._watchlist.scan()
                intel.watchlist_alerts = [a.symbol for a in alerts if a.status.value == "IN_ZONE"]
                intel.watchlist_approaching = [a.symbol for a in alerts if a.status.value == "ENTERING_ZONE"]

                # Also check the stored summary
                summary = self._watchlist.get_summary()
                if summary.get("in_zone_symbols"):
                    intel.watchlist_alerts = summary["in_zone_symbols"]
                if summary.get("approaching_symbols"):
                    intel.watchlist_approaching = summary["approaching_symbols"]
        except Exception as e:
            logger.warning("sage_watchlist_error", error=str(e))

    def _calculate_expected_moves(self, intel: MarketIntel) -> None:
        """Calculate expected moves from VIX."""
        if intel.vix_level > 0 and intel.spy_price > 0:
            # Expected daily move = SPY × VIX / sqrt(252) / 100
            import math
            daily_move = intel.spy_price * (intel.vix_level / 100) / math.sqrt(252)
            intel.spy_expected_move = round(daily_move, 2)
            intel.spy_range_low = round(intel.spy_price - daily_move, 2)
            intel.spy_range_high = round(intel.spy_price + daily_move, 2)

            if intel.spx_price > 0:
                spx_move = intel.spx_price * (intel.vix_level / 100) / math.sqrt(252)
                intel.spx_expected_move = round(spx_move, 2)
                intel.spx_range_low = round(intel.spx_price - spx_move, 2)
                intel.spx_range_high = round(intel.spx_price + spx_move, 2)

    def _assess_risk_flags(self, intel: MarketIntel) -> None:
        """Flag anything the team should be aware of."""
        flags = []

        if intel.vix_level >= 35:
            flags.append("🔴 VIX >= 35 — CAPITULATION ZONE — watch for reversal")
        elif intel.vix_level >= 30:
            flags.append("🟡 VIX 30+ — IC SWEET SPOT — elevated premium, favor P1")
        elif intel.vix_level >= 25:
            flags.append("🟡 VIX 25+ — ELEVATED — reduce directional, favor credit spreads")

        if intel.flow.spy_put_call_ratio > 3:
            flags.append(f"🐻 EXTREME PUT BUYING — P/C ratio {intel.flow.spy_put_call_ratio:.1f}x")
        elif intel.flow.spy_put_call_ratio > 2:
            flags.append(f"🐻 Heavy put buying — P/C ratio {intel.flow.spy_put_call_ratio:.1f}x")

        if intel.flow.net_delta < -200_000_000:
            flags.append(f"🐻 MASSIVE BEARISH DELTA — {intel.flow.net_delta:,.0f}")

        if intel.is_event_day:
            flags.append(f"📅 EVENT DAY: {intel.event_name} — reduce size 50%")

        if intel.flow.max_pain_spy > 0 and intel.spy_price > 0:
            gap = intel.flow.max_pain_spy - intel.spy_price
            if abs(gap) > 15:
                flags.append(f"🧲 MAX PAIN GAP: SPY ${intel.spy_price:.0f} vs max pain ${intel.flow.max_pain_spy:.0f} (${gap:+.0f})")

        intel.risk_flags = flags

    # ── Brief Builders ───────────────────────────────────────────

    def _build_sunday_brief(self, intel: MarketIntel) -> str:
        """Build Sunday night intelligence brief."""
        lines = [
            f"🔭 SAGE SUNDAY INTEL — Week of {datetime.now(ET).strftime('%b %d')}",
            "",
            f"📊 MARKET: SPY ${intel.spy_price:.2f} | SPX ${intel.spx_price:.2f} | VIX {intel.vix_level:.1f}",
            f"📈 QQQ ${intel.qqq_price:.2f} | IWM ${intel.iwm_price:.2f}",
            "",
            f"🐋 FLOW ({intel.flow.flow_direction}):",
            f"  Put/Call: {intel.flow.spy_put_call_ratio:.1f}x | Bias: {intel.flow.flow_bias_score:+.1f}",
            f"  Calls: ${intel.flow.spy_call_premium/1e9:.2f}B | Puts: ${intel.flow.spy_put_premium/1e9:.2f}B",
            f"  Net Delta: {intel.flow.net_delta:,.0f} ({intel.flow.net_delta_direction})",
            f"  Max Pain: ${intel.flow.max_pain_spy:.0f}",
            "",
            f"📐 EXPECTED MOVE:",
            f"  SPY: ±${intel.spy_expected_move:.0f} → ${intel.spy_range_low:.0f} — ${intel.spy_range_high:.0f}",
            f"  SPX: ±${intel.spx_expected_move:.0f} → ${intel.spx_range_low:.0f} — ${intel.spx_range_high:.0f}",
        ]

        if intel.risk_flags:
            lines.append("")
            lines.append("⚠️ RISK FLAGS:")
            for flag in intel.risk_flags:
                lines.append(f"  {flag}")

        if intel.watchlist_alerts or intel.watchlist_approaching:
            lines.append("")
            lines.append("📋 LEAP WATCHLIST:")
            for s in intel.watchlist_alerts:
                lines.append(f"  🟢 {s} IN BUY ZONE")
            for s in intel.watchlist_approaching:
                lines.append(f"  🟡 {s} approaching zone")

        return "\n".join(lines)

    def _build_overnight_brief(self, intel: MarketIntel) -> str:
        """Build overnight intelligence brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "OVERNIGHT INTEL")

    def _build_premarket_brief(self, intel: MarketIntel) -> str:
        """Build pre-market brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "PRE-MARKET INTEL")

    def _build_intraday_brief(self, intel: MarketIntel) -> str:
        """Build intraday quick brief."""
        return (
            f"🔭 SAGE: SPY ${intel.spy_price:.2f} | VIX {intel.vix_level:.1f} | "
            f"Flow: {intel.flow.flow_direction} ({intel.flow.flow_bias_score:+.1f}) | "
            f"P/C: {intel.flow.spy_put_call_ratio:.1f}x"
        )

    def _build_eod_brief(self, intel: MarketIntel) -> str:
        """Build end-of-day brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "EOD INTEL")

    # ── Persistence ──────────────────────────────────────────────

    def _store_intel(self, intel: MarketIntel) -> None:
        """Store intel to file and memory."""
        self._latest_intel = intel
        self._intel_history.append(intel)

        # Keep last 50 scans in memory
        if len(self._intel_history) > 50:
            self._intel_history = self._intel_history[-50:]

        # Persist to JSON
        try:
            filename = f"{intel.scan_type}_{datetime.now(ET).strftime('%Y-%m-%d_%H%M')}.json"
            filepath = INTEL_DIR / filename
            with open(filepath, "w") as f:
                json.dump(intel.model_dump(mode="json"), f, indent=2, default=str)
            logger.debug("sage_intel_stored", path=str(filepath))
        except Exception as e:
            logger.warning("sage_store_error", error=str(e))

    def get_latest_brief(self) -> str:
        """Get the most recent intel brief as plain text."""
        if self._latest_intel:
            return self._latest_intel.intel_brief
        return "No intel available yet. Run scan_now() first."

    def get_intel_for_debate(self) -> dict[str, Any]:
        """Package current intel for the AI debate system.

        This is what gets passed to Kimi/Riki/Abi/Kage as context.
        """
        if not self._latest_intel:
            return {}

        i = self._latest_intel
        return {
            "spy_price": i.spy_price,
            "spx_price": i.spx_price,
            "vix_level": i.vix_level,
            "flow_direction": i.flow.flow_direction,
            "flow_bias": i.flow.flow_bias_score,
            "put_call_ratio": i.flow.spy_put_call_ratio,
            "max_pain": i.flow.max_pain_spy,
            "net_delta": i.flow.net_delta,
            "net_delta_direction": i.flow.net_delta_direction,
            "expected_move_spy": i.spy_expected_move,
            "spy_range": f"${i.spy_range_low:.0f} — ${i.spy_range_high:.0f}",
            "regime_state": i.regime_state,
            "is_event_day": i.is_event_day,
            "event_name": i.event_name,
            "risk_flags": i.risk_flags,
            "watchlist_in_zone": i.watchlist_alerts,
            "intel_brief": i.intel_brief,
        }

    def format_telegram(self, intel: MarketIntel | None = None) -> str:
        """Format intel for Telegram delivery."""
        i = intel or self._latest_intel
        if not i:
            return "No intel available."
        return i.intel_brief
