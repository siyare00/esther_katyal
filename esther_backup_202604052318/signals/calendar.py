"""Economic Calendar — Market Event Awareness.

Tracks market-moving economic events and adjusts trading behavior:
    - FOMC meetings (rate decisions, minutes)
    - CPI releases (inflation data, 8:30 AM ET)
    - PPI releases (producer prices, 8:30 AM ET)
    - NFP releases (jobs data, 8:30 AM ET)
    - OPEX dates (monthly 3rd Friday, weekly Fridays)

On event days, position sizes are reduced by the configured multiplier
(default 50%) to account for increased volatility and gap risk.

The 8:30 AM data candle is flagged specifically because CPI/PPI/NFP
drop at that exact time, creating massive moves in the first minute.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from esther.core.config import config

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class EventType(str, Enum):
    """Types of market-moving economic events."""

    FOMC = "FOMC"
    CPI = "CPI"
    PPI = "PPI"
    NFP = "NFP"
    OPEX_MONTHLY = "OPEX_MONTHLY"
    OPEX_WEEKLY = "OPEX_WEEKLY"


class EventImpact(str, Enum):
    """Expected market impact level."""

    HIGH = "HIGH"      # FOMC, CPI, NFP
    MEDIUM = "MEDIUM"  # PPI, OPEX monthly
    LOW = "LOW"        # OPEX weekly


class EconomicEvent(BaseModel):
    """A single economic calendar event."""

    date: date
    event_type: EventType
    name: str
    time_et: str = ""  # e.g., "08:30", "14:00", or "" if all-day
    impact: EventImpact = EventImpact.HIGH
    notes: str = ""


# --- 2025-2026 HARDCODED ECONOMIC CALENDAR ---
# These are confirmed/projected dates. Update annually.

FOMC_DATES_2026 = [
    date(2026, 1, 28),   # January meeting
    date(2026, 3, 18),   # March meeting
    date(2026, 5, 6),    # May meeting
    date(2026, 6, 17),   # June meeting
    date(2026, 7, 29),   # July meeting
    date(2026, 9, 16),   # September meeting
    date(2026, 11, 4),   # November meeting
    date(2026, 12, 16),  # December meeting
]

# CPI releases — typically 2nd or 3rd Tuesday/Wednesday of the month, 8:30 AM ET
CPI_DATES_2026 = [
    date(2026, 1, 14),
    date(2026, 2, 11),
    date(2026, 3, 11),
    date(2026, 4, 14),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 15),
    date(2026, 10, 13),
    date(2026, 11, 10),
    date(2026, 12, 9),
]

# PPI releases — typically day before or after CPI, 8:30 AM ET
PPI_DATES_2026 = [
    date(2026, 1, 15),
    date(2026, 2, 12),
    date(2026, 3, 12),
    date(2026, 4, 9),
    date(2026, 5, 13),
    date(2026, 6, 11),
    date(2026, 7, 15),
    date(2026, 8, 13),
    date(2026, 9, 16),
    date(2026, 10, 14),
    date(2026, 11, 12),
    date(2026, 12, 10),
]

# NFP (Non-Farm Payrolls) — first Friday of each month, 8:30 AM ET
NFP_DATES_2026 = [
    date(2026, 1, 2),
    date(2026, 2, 6),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 6, 5),
    date(2026, 7, 2),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
]


def _get_third_friday(year: int, month: int) -> date:
    """Calculate the third Friday of a given month (monthly OPEX)."""
    # Find the first day of the month
    first_day = date(year, month, 1)
    # Find first Friday: weekday() 4 = Friday
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    # Third Friday = first Friday + 14 days
    return first_friday + timedelta(days=14)


def _get_monthly_opex_dates(year: int) -> list[date]:
    """Get all monthly OPEX dates (3rd Friday) for a year."""
    return [_get_third_friday(year, month) for month in range(1, 13)]


class CalendarModule:
    """Economic calendar for market event awareness.

    Provides event lookups, size reduction recommendations, and
    expected move calculations. Integrates with the bias engine to
    reduce confidence on event days.
    """

    def __init__(self):
        self._cfg = config().calendar
        self._events = self._build_event_list()

    def _build_event_list(self) -> list[EconomicEvent]:
        """Build the complete event list from hardcoded dates."""
        events: list[EconomicEvent] = []

        if self._cfg.track_fomc:
            for d in FOMC_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.FOMC,
                    name=f"FOMC Rate Decision",
                    time_et="14:00",
                    impact=EventImpact.HIGH,
                    notes="Rate decision at 2 PM, press conference at 2:30 PM ET",
                ))

        if self._cfg.track_cpi:
            for d in CPI_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.CPI,
                    name="CPI Release",
                    time_et="08:30",
                    impact=EventImpact.HIGH,
                    notes="Consumer Price Index — 8:30 AM data candle",
                ))

        if self._cfg.track_ppi:
            for d in PPI_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.PPI,
                    name="PPI Release",
                    time_et="08:30",
                    impact=EventImpact.MEDIUM,
                    notes="Producer Price Index — 8:30 AM data candle",
                ))

        if self._cfg.track_nfp:
            for d in NFP_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.NFP,
                    name="Non-Farm Payrolls",
                    time_et="08:30",
                    impact=EventImpact.HIGH,
                    notes="Jobs report — 8:30 AM data candle, expect massive volume",
                ))

        if self._cfg.track_opex:
            # Monthly OPEX — 3rd Friday
            for d in _get_monthly_opex_dates(2026):
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.OPEX_MONTHLY,
                    name="Monthly OPEX",
                    time_et="",
                    impact=EventImpact.MEDIUM,
                    notes="Monthly options expiration — pin risk, gamma exposure",
                ))

            # Weekly OPEX — every Friday (that isn't already monthly)
            monthly_dates = set(_get_monthly_opex_dates(2026))
            start = date(2026, 1, 2)
            end = date(2026, 12, 31)
            d = start
            while d <= end:
                if d.weekday() == 4 and d not in monthly_dates:  # Friday
                    events.append(EconomicEvent(
                        date=d,
                        event_type=EventType.OPEX_WEEKLY,
                        name="Weekly OPEX",
                        time_et="",
                        impact=EventImpact.LOW,
                        notes="Weekly options expiration",
                    ))
                d += timedelta(days=1)

        events.sort(key=lambda e: e.date)
        logger.info("calendar_built", total_events=len(events))
        return events

    def get_events_today(self, today: date | None = None) -> list[EconomicEvent]:
        """Get all economic events scheduled for today.

        Args:
            today: Override date for testing. Defaults to current date.

        Returns:
            List of events happening today, sorted by time.
        """
        if today is None:
            today = datetime.now(ET).date()
        return [e for e in self._events if e.date == today]

    def get_events_this_week(
        self, today: date | None = None
    ) -> list[EconomicEvent]:
        """Get all economic events for the current week (Mon-Fri).

        Args:
            today: Override date. Defaults to current date.

        Returns:
            List of events this week, sorted by date.
        """
        if today is None:
            today = datetime.now(ET).date()

        # Find Monday of this week
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)

        return [e for e in self._events if monday <= e.date <= friday]

    def is_event_day(self, today: date | None = None) -> bool:
        """Check if today has any market-moving events.

        Args:
            today: Override date.

        Returns:
            True if there are HIGH or MEDIUM impact events today.
        """
        events = self.get_events_today(today)
        return any(
            e.impact in (EventImpact.HIGH, EventImpact.MEDIUM) for e in events
        )

    def is_830_data_candle(self, now: datetime | None = None) -> bool:
        """Check if we're in the 8:30 AM data candle window.

        CPI, PPI, and NFP all drop at exactly 8:30 AM ET.
        This flag indicates we should expect extreme volatility
        in the first 1-5 minutes after the release.

        Args:
            now: Override datetime.

        Returns:
            True if current time is between 8:28-8:35 AM ET AND
            today has an 8:30 AM event.
        """
        if now is None:
            now = datetime.now(ET)
        else:
            now = now.astimezone(ET) if now.tzinfo else now

        today = now.date()
        events = self.get_events_today(today)

        # Check if any event is at 8:30 AM
        has_830_event = any(e.time_et == "08:30" for e in events)
        if not has_830_event:
            return False

        # Check if current time is in the window (8:28 - 8:35)
        current_time = now.time()
        window_start = time(8, 28)
        window_end = time(8, 35)

        return window_start <= current_time <= window_end

    def get_expected_move(self, vix: float, dte: int) -> float:
        """Calculate the implied expected move from VIX.

        The VIX represents annualized expected volatility. We convert
        to a daily (or multi-day) expected move using:
            daily_move = (VIX / 100) * price * sqrt(dte / 252)

        For SPX at ~5800 with VIX at 20:
            daily_move = (20/100) * sqrt(1/252) ≈ 1.26% ≈ 73 points

        Args:
            vix: Current VIX level.
            dte: Days to expiration (1 for 0DTE).

        Returns:
            Expected move as a percentage (e.g., 1.26 = 1.26%).
        """
        if dte <= 0:
            dte = 1
        daily_vol = vix / math.sqrt(252)
        expected_pct = daily_vol * math.sqrt(dte)
        logger.debug(
            "expected_move_calculated",
            vix=vix,
            dte=dte,
            expected_pct=round(expected_pct, 4),
        )
        return round(expected_pct, 4)

    def should_reduce_size(
        self, today: date | None = None
    ) -> tuple[bool, float]:
        """Determine if position sizes should be reduced today.

        On event days, reduce size by the configured multiplier to
        account for increased volatility and gap risk.

        Args:
            today: Override date.

        Returns:
            Tuple of (should_reduce: bool, multiplier: float).
            Multiplier is 1.0 if no reduction needed, or the configured
            reduce_size_on_event value (default 0.5) on event days.
        """
        if not self.is_event_day(today):
            return False, 1.0

        events = self.get_events_today(today)
        multiplier = self._cfg.reduce_size_on_event

        # If there's a HIGH impact event, use full reduction
        # If only MEDIUM, use a gentler reduction (midpoint between 1.0 and configured)
        has_high = any(e.impact == EventImpact.HIGH for e in events)
        if not has_high:
            multiplier = (1.0 + multiplier) / 2.0  # e.g., 0.75 instead of 0.50

        event_names = [e.name for e in events]
        logger.info(
            "size_reduction_recommended",
            events=event_names,
            multiplier=multiplier,
            has_high_impact=has_high,
        )
        return True, multiplier

    def get_confidence_adjustment(self, today: date | None = None) -> float:
        """Get confidence adjustment factor for the bias engine.

        On event days, bias confidence should be reduced because
        the data release can invalidate any technical signal.

        Returns:
            Multiplier from 0.0 to 1.0. 1.0 = full confidence.
            On event days: 0.6-0.8 depending on impact.
        """
        if not self.is_event_day(today):
            return 1.0

        events = self.get_events_today(today)
        has_high = any(e.impact == EventImpact.HIGH for e in events)
        has_multiple = len([
            e for e in events
            if e.impact in (EventImpact.HIGH, EventImpact.MEDIUM)
        ]) > 1

        if has_high and has_multiple:
            return 0.5  # Very uncertain day
        elif has_high:
            return 0.65
        else:
            return 0.80

    # ── Macro Data Feed ──────────────────────────────────────────

    async def fetch_macro_data(self) -> dict[str, Any]:
        """Fetch actual economic data from FRED (Federal Reserve) API.

        Gets actual vs expected values for key economic indicators:
        - CPI (Consumer Price Index)
        - PPI (Producer Price Index)
        - NFP (Non-Farm Payrolls)
        - GDP (Gross Domestic Product)

        Stores results in data/macro_data.json.

        Returns:
            Dict with indicator data and computed macro_bias score.
        """
        import os
        fred_api_key = os.environ.get("FRED_API_KEY", "")

        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        macro_file = data_dir / "macro_data.json"

        # FRED series IDs for key indicators
        # CPI: CPIAUCSL (CPI for All Urban Consumers)
        # PPI: PPIACO (PPI All Commodities)
        # NFP: PAYEMS (Total Nonfarm Payrolls)
        # GDP: GDP (Gross Domestic Product)
        series_map = {
            "CPI": "CPIAUCSL",
            "PPI": "PPIACO",
            "NFP": "PAYEMS",
            "GDP": "GDP",
        }

        macro_data: dict[str, Any] = {
            "fetched_at": datetime.now().isoformat(),
            "indicators": {},
            "macro_bias": 0.0,
        }

        total_bias = 0.0

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for indicator, series_id in series_map.items():
                try:
                    # Fetch last 3 observations to compare current vs previous
                    params: dict[str, Any] = {
                        "series_id": series_id,
                        "sort_order": "desc",
                        "limit": 3,
                        "file_type": "json",
                    }
                    if fred_api_key:
                        params["api_key"] = fred_api_key

                    url = "https://api.stlouisfed.org/fred/series/observations"
                    resp = await client.get(url, params=params)

                    if resp.status_code != 200:
                        logger.warning(
                            "fred_fetch_failed",
                            indicator=indicator,
                            status=resp.status_code,
                        )
                        continue

                    result = resp.json()
                    observations = result.get("observations", [])

                    if len(observations) < 2:
                        continue

                    # Latest observation = actual, previous = "expected" baseline
                    latest = observations[0]
                    previous = observations[1]

                    try:
                        actual = float(latest.get("value", 0))
                        expected = float(previous.get("value", 0))
                    except (ValueError, TypeError):
                        continue

                    # Calculate surprise (actual - expected as % of expected)
                    if expected != 0:
                        surprise_pct = ((actual - expected) / abs(expected)) * 100
                    else:
                        surprise_pct = 0.0

                    # Determine bias contribution
                    indicator_bias = self._compute_indicator_bias(
                        indicator, actual, expected, surprise_pct
                    )
                    total_bias += indicator_bias

                    macro_data["indicators"][indicator] = {
                        "actual": actual,
                        "expected": expected,
                        "surprise_pct": round(surprise_pct, 4),
                        "bias_contribution": round(indicator_bias, 2),
                        "date": latest.get("date", ""),
                        "series_id": series_id,
                    }

                    logger.info(
                        "macro_indicator_fetched",
                        indicator=indicator,
                        actual=actual,
                        expected=expected,
                        surprise_pct=round(surprise_pct, 2),
                        bias=round(indicator_bias, 2),
                    )

                except Exception as e:
                    logger.error(
                        "macro_fetch_error",
                        indicator=indicator,
                        error=str(e),
                    )
                    continue

        # Clamp total bias to [-50, +50]
        macro_bias = max(-50.0, min(50.0, total_bias))
        macro_data["macro_bias"] = round(macro_bias, 2)

        # Save to file
        try:
            macro_file.write_text(json.dumps(macro_data, indent=2))
        except Exception as e:
            logger.error("macro_data_save_failed", error=str(e))

        logger.info("macro_data_fetched", bias=macro_bias, indicators=len(macro_data["indicators"]))
        return macro_data

    def _compute_indicator_bias(
        self,
        indicator: str,
        actual: float,
        expected: float,
        surprise_pct: float,
    ) -> float:
        """Compute bias contribution for a single macro indicator.

        Logic:
        - CPI higher than expected = hawkish = bearish (Fed tightens)
        - PPI higher than expected = hawkish = bearish (inflation upstream)
        - NFP higher than expected = hawkish = initially bearish (Fed stays tight)
        - GDP lower than expected = bearish (economy slowing)

        Args:
            indicator: "CPI", "PPI", "NFP", or "GDP".
            actual: Actual reported value.
            expected: Expected/previous value.
            surprise_pct: (actual - expected) / expected * 100.

        Returns:
            Bias contribution from -15 to +15 per indicator.
        """
        # Scale: 1% surprise ≈ ±10 bias points, capped at ±15
        base_magnitude = min(15.0, abs(surprise_pct) * 10)

        if indicator == "CPI":
            # CPI higher = hawkish = bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "PPI":
            # PPI higher = upstream inflation = bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "NFP":
            # Strong jobs = Fed stays tight = initially bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "GDP":
            # GDP lower = economy slowing = bearish
            # GDP higher = economy strong = bullish
            return base_magnitude if actual > expected else -base_magnitude

        return 0.0

    def get_macro_bias(self) -> float:
        """Get the cached macro bias score from data/macro_data.json.

        Returns:
            Macro bias score from -50 to +50. Returns 0.0 if no data.
        """
        macro_file = Path("data") / "macro_data.json"
        if not macro_file.exists():
            return 0.0

        try:
            data = json.loads(macro_file.read_text())
            # Only use data less than 24 hours old
            fetched_at = data.get("fetched_at", "")
            if fetched_at:
                fetch_time = datetime.fromisoformat(fetched_at)
                if (datetime.now() - fetch_time).total_seconds() > 86400:
                    return 0.0  # Data too old
            return float(data.get("macro_bias", 0.0))
        except (json.JSONDecodeError, Exception):
            return 0.0
