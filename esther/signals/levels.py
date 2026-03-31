"""Key Level Tracker — Track critical support/resistance levels per ticker.

Tracks:
    - Premarket Low (4:00 AM - 9:30 AM ET) — #1 entry support
    - Previous Day Close — reversal trigger
    - Previous Friday Close — weekly S/R
    - NWOG (New Week Opening Gap) — Friday close to Monday open gap
    - Fibonacci Retracements — 38.2%, 50%, 61.8%
    - Session High/Low — intraday tracking

Levels are stored per-symbol and persisted to JSON for cross-session reuse.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import Bar

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")
PREMARKET_START = time(4, 0)
PREMARKET_END = time(9, 30)

# Persistence path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEVELS_FILE = _PROJECT_ROOT / "data" / "key_levels.json"


class FibonacciLevels(BaseModel):
    """Fibonacci retracement levels calculated from a high/low range."""

    high: float
    low: float
    fib_382: float = 0.0
    fib_500: float = 0.0
    fib_618: float = 0.0

    def __init__(self, **data: Any):
        super().__init__(**data)
        range_size = self.high - self.low
        self.fib_382 = self.high - range_size * 0.382
        self.fib_500 = self.high - range_size * 0.500
        self.fib_618 = self.high - range_size * 0.618


class NWOGLevels(BaseModel):
    """New Week Opening Gap — gap between Friday close and Monday open."""

    friday_close: float
    monday_open: float
    gap_high: float = 0.0
    gap_low: float = 0.0
    gap_mid: float = 0.0

    def __init__(self, **data: Any):
        super().__init__(**data)
        self.gap_high = max(self.friday_close, self.monday_open)
        self.gap_low = min(self.friday_close, self.monday_open)
        self.gap_mid = (self.gap_high + self.gap_low) / 2.0


class DemandZone(BaseModel):
    """A demand/supply zone identified from chart structure."""

    zone_high: float
    zone_low: float
    zone_type: str = "demand"  # "demand" or "supply"
    strength: str = "moderate"  # "weak", "moderate", "strong"
    notes: str = ""


class KeyLevels(BaseModel):
    """Aggregated key levels for a single symbol on a given day."""

    symbol: str
    date: str  # ISO date string
    premarket_low: float | None = None
    premarket_high: float | None = None
    prev_day_close: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    prev_friday_close: float | None = None
    nwog: NWOGLevels | None = None
    fibonacci: FibonacciLevels | None = None
    session_high: float | None = None
    session_low: float | None = None
    sma_200: float | None = None  # 200-day SMA — major resistance/support
    sma_50: float | None = None   # 50-day SMA — floor level
    market_open: float | None = None  # Today's opening price
    demand_zones: list[DemandZone] = Field(default_factory=list)


class LevelTracker:
    """Tracks and manages key price levels for all symbols.

    Calculates premarket low, prev close, NWOG, fibs, and session
    extremes. Persists to JSON so levels survive restarts.
    """

    def __init__(self):
        self._cfg = config().levels
        self._levels: dict[str, KeyLevels] = {}
        self._load_persisted()

    def _load_persisted(self) -> None:
        """Load previously persisted levels from JSON file."""
        if LEVELS_FILE.exists():
            try:
                with open(LEVELS_FILE) as f:
                    raw = json.load(f)
                for symbol, data in raw.items():
                    self._levels[symbol] = KeyLevels.model_validate(data)
                logger.info("levels_loaded", count=len(self._levels))
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("levels_load_failed", error=str(e))

    def _persist(self) -> None:
        """Persist current levels to JSON file."""
        LEVELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {sym: lvl.model_dump() for sym, lvl in self._levels.items()}
        with open(LEVELS_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.debug("levels_persisted", count=len(self._levels))

    def calculate_premarket_levels(self, bars: list[Bar]) -> tuple[float | None, float | None]:
        """Calculate premarket low and high between 4:00 AM and 9:30 AM ET.

        Premarket low = #1 entry support. PM high = resistance for puts.
        Price holding PM low = bullish. Price breaking PM low = bearish.

        From SuperLuckeee's 6 key levels system:
        - PM Low: Support for calls entry
        - PM High: Resistance for puts entry
        - Works on individual stocks AND indices

        Args:
            bars: Intraday bars (1m or 5m) that include premarket hours.

        Returns:
            Tuple of (premarket_low, premarket_high), either may be None.
        """
        pm_bars = []
        for bar in bars:
            bar_time_et = bar.timestamp.astimezone(ET).time()
            if PREMARKET_START <= bar_time_et < PREMARKET_END:
                pm_bars.append(bar)

        if not pm_bars:
            logger.debug("no_premarket_bars", count=len(bars))
            return None, None

        pm_low = min(b.low for b in pm_bars)
        pm_high = max(b.high for b in pm_bars)
        logger.info("premarket_levels_calculated", pm_low=pm_low, pm_high=pm_high, bar_count=len(pm_bars))
        return pm_low, pm_high

    def calculate_premarket_low(self, bars: list[Bar]) -> float | None:
        """Calculate the lowest price between 4:00 AM and 9:30 AM ET.

        Legacy wrapper — prefer calculate_premarket_levels() for both values.
        """
        pm_low, _ = self.calculate_premarket_levels(bars)
        return pm_low

    def calculate_sma(self, daily_bars: list[Bar], period: int) -> float | None:
        """Calculate Simple Moving Average from daily bars.

        Used for 200SMA (major resistance/support) and 50SMA (floor level).
        SuperLuckeee: "SPY at 200SMA $661 = serious resistance."
        SuperLuckeee: "50SMA at $650-651, SPY doesn't stay below this for long."

        Args:
            daily_bars: Historical daily bars. Need at least `period` bars.
            period: SMA period (e.g., 200 or 50).

        Returns:
            The SMA value, or None if insufficient data.
        """
        if len(daily_bars) < period:
            return None
        closes = [b.close for b in daily_bars[-period:]]
        sma = sum(closes) / len(closes)
        return round(sma, 2)

    def calculate_nwog(
        self, friday_close: float, monday_open: float
    ) -> dict[str, float]:
        """Calculate the New Week Opening Gap.

        The NWOG is the gap between Friday's close and Monday's open.
        This gap zone acts as a magnet — price tends to fill the gap
        and the midpoint is a key reversal level.

        Args:
            friday_close: Friday's closing price.
            monday_open: Monday's opening price.

        Returns:
            Dict with gap_high, gap_low, gap_mid values.
        """
        nwog = NWOGLevels(friday_close=friday_close, monday_open=monday_open)
        logger.info(
            "nwog_calculated",
            friday_close=friday_close,
            monday_open=monday_open,
            gap_high=nwog.gap_high,
            gap_low=nwog.gap_low,
            gap_mid=nwog.gap_mid,
        )
        return {"gap_high": nwog.gap_high, "gap_low": nwog.gap_low, "gap_mid": nwog.gap_mid}

    def calculate_fibonacci(
        self, high: float, low: float
    ) -> dict[str, float]:
        """Calculate Fibonacci retracement levels for a given range.

        Standard retracement levels at 38.2%, 50%, and 61.8%.
        These are measured from the high — so fib_382 is closer to the high
        (shallower pullback) and fib_618 is closer to the low (deeper pullback).

        Args:
            high: The swing high price.
            low: The swing low price.

        Returns:
            Dict with fib_382, fib_500, fib_618 levels.
        """
        if high <= low:
            logger.warning("fibonacci_invalid_range", high=high, low=low)
            return {"fib_382": 0.0, "fib_500": 0.0, "fib_618": 0.0}

        fib = FibonacciLevels(high=high, low=low)
        result = {
            "fib_382": round(fib.fib_382, 2),
            "fib_500": round(fib.fib_500, 2),
            "fib_618": round(fib.fib_618, 2),
        }
        logger.info("fibonacci_calculated", high=high, low=low, **result)
        return result

    def update_session_extremes(self, symbol: str, bar: Bar) -> None:
        """Update the session high/low as intraday bars come in.

        Call this on every new bar to keep session extremes current.

        Args:
            symbol: Ticker symbol.
            bar: Latest OHLCV bar.
        """
        levels = self._levels.get(symbol)
        if levels is None:
            return

        updated = False
        if levels.session_high is None or bar.high > levels.session_high:
            levels.session_high = bar.high
            updated = True
        if levels.session_low is None or bar.low < levels.session_low:
            levels.session_low = bar.low
            updated = True

        if updated:
            logger.debug(
                "session_extremes_updated",
                symbol=symbol,
                high=levels.session_high,
                low=levels.session_low,
            )

    def build_levels(
        self,
        symbol: str,
        intraday_bars: list[Bar],
        daily_bars: list[Bar],
        friday_close: float | None = None,
        monday_open: float | None = None,
    ) -> KeyLevels:
        """Build all key levels for a symbol from available data.

        This is the main entry point — call once at start of day with
        premarket + daily bars, then use update_session_extremes() for
        intraday tracking.

        Args:
            symbol: Ticker symbol.
            intraday_bars: Today's intraday bars (including premarket if available).
            daily_bars: Historical daily bars (at least 5 for weekly levels).
            friday_close: Previous Friday's close (for NWOG calc on Mondays).
            monday_open: Monday's opening price (for NWOG calc).

        Returns:
            KeyLevels with all available level data populated.
        """
        today_str = datetime.now(ET).strftime("%Y-%m-%d")

        levels = KeyLevels(symbol=symbol, date=today_str)

        # Premarket low + high (The 6 Key Levels: #1 PM High, #2 PM Low)
        if self._cfg.track_pm_low and intraday_bars:
            pm_low, pm_high = self.calculate_premarket_levels(intraday_bars)
            levels.premarket_low = pm_low
            levels.premarket_high = pm_high

        # Previous day close (The 6 Key Levels: #5 Market Close)
        if self._cfg.track_prev_close and len(daily_bars) >= 2:
            levels.prev_day_close = daily_bars[-2].close
            levels.prev_day_high = daily_bars[-2].high
            levels.prev_day_low = daily_bars[-2].low
            logger.info(
                "prev_day_levels_set", symbol=symbol,
                close=levels.prev_day_close,
                high=levels.prev_day_high,
                low=levels.prev_day_low,
            )

        # 200SMA and 50SMA — major resistance/support
        if len(daily_bars) >= 200:
            levels.sma_200 = self.calculate_sma(daily_bars, 200)
            logger.info("sma_200_set", symbol=symbol, sma_200=levels.sma_200)
        if len(daily_bars) >= 50:
            levels.sma_50 = self.calculate_sma(daily_bars, 50)
            logger.info("sma_50_set", symbol=symbol, sma_50=levels.sma_50)

        # Previous Friday close (look back through daily bars to find last Friday)
        if daily_bars:
            for bar in reversed(daily_bars[:-1]):  # skip today
                bar_dt = bar.timestamp.astimezone(ET) if bar.timestamp.tzinfo else bar.timestamp
                if bar_dt.weekday() == 4:  # Friday
                    levels.prev_friday_close = bar.close
                    logger.info("prev_friday_close_set", symbol=symbol, close=bar.close)
                    break

        # NWOG
        if self._cfg.track_nwog and friday_close is not None and monday_open is not None:
            nwog_data = self.calculate_nwog(friday_close, monday_open)
            levels.nwog = NWOGLevels(
                friday_close=friday_close, monday_open=monday_open
            )

        # Fibonacci from previous day's high/low
        if len(daily_bars) >= 2:
            prev_day = daily_bars[-2]
            fib_data = self.calculate_fibonacci(prev_day.high, prev_day.low)
            levels.fibonacci = FibonacciLevels(high=prev_day.high, low=prev_day.low)

        # Session high/low from intraday bars
        if intraday_bars:
            session_bars = [
                b for b in intraday_bars
                if b.timestamp.astimezone(ET).time() >= PREMARKET_END
            ]
            if session_bars:
                levels.session_high = max(b.high for b in session_bars)
                levels.session_low = min(b.low for b in session_bars)

        self._levels[symbol] = levels
        self._persist()

        logger.info(
            "levels_built",
            symbol=symbol,
            pm_low=levels.premarket_low,
            prev_close=levels.prev_day_close,
            session_high=levels.session_high,
            session_low=levels.session_low,
        )
        return levels

    def get_key_levels(self, symbol: str) -> KeyLevels | None:
        """Get the current key levels for a symbol.

        Args:
            symbol: Ticker symbol.

        Returns:
            KeyLevels if available, None otherwise.
        """
        return self._levels.get(symbol)

    def is_at_support(
        self, price: float, levels: KeyLevels, tolerance_pct: float = 0.001
    ) -> bool:
        """Check if current price is near a support level.

        Support levels checked: premarket low, prev close (if below),
        session low, NWOG gap low, fibonacci 618 and 500 (deeper pullback = support).

        Args:
            price: Current price.
            levels: KeyLevels for the symbol.
            tolerance_pct: How close price must be (as fraction, default 0.1%).

        Returns:
            True if price is within tolerance of any support level.
        """
        support_levels: list[float] = []

        if levels.premarket_low is not None:
            support_levels.append(levels.premarket_low)
        if levels.prev_day_close is not None:
            support_levels.append(levels.prev_day_close)
        if levels.prev_day_low is not None:
            support_levels.append(levels.prev_day_low)
        if levels.session_low is not None:
            support_levels.append(levels.session_low)
        if levels.nwog is not None:
            support_levels.append(levels.nwog.gap_low)
            support_levels.append(levels.nwog.gap_mid)
        if levels.fibonacci is not None:
            support_levels.append(levels.fibonacci.fib_500)
            support_levels.append(levels.fibonacci.fib_618)
        if levels.prev_friday_close is not None:
            support_levels.append(levels.prev_friday_close)
        # 50SMA and 200SMA as support when price is above them
        if levels.sma_50 is not None and price >= levels.sma_50:
            support_levels.append(levels.sma_50)
        if levels.sma_200 is not None and price >= levels.sma_200:
            support_levels.append(levels.sma_200)
        # Demand zones
        for zone in levels.demand_zones:
            if zone.zone_type == "demand":
                support_levels.append(zone.zone_low)
                support_levels.append(zone.zone_high)

        for level in support_levels:
            if level <= 0:
                continue
            if abs(price - level) / level <= tolerance_pct:
                logger.info(
                    "at_support",
                    price=price,
                    level=round(level, 2),
                    distance_pct=round(abs(price - level) / level * 100, 4),
                )
                return True
        return False

    def is_at_resistance(
        self, price: float, levels: KeyLevels, tolerance_pct: float = 0.001
    ) -> bool:
        """Check if current price is near a resistance level.

        Resistance levels checked: session high, prev close (if above),
        NWOG gap high, fibonacci 382 (shallow pullback = resistance).

        Args:
            price: Current price.
            levels: KeyLevels for the symbol.
            tolerance_pct: How close price must be (as fraction, default 0.1%).

        Returns:
            True if price is within tolerance of any resistance level.
        """
        resistance_levels: list[float] = []

        if levels.session_high is not None:
            resistance_levels.append(levels.session_high)
        if levels.premarket_high is not None:
            resistance_levels.append(levels.premarket_high)
        if levels.prev_day_close is not None:
            resistance_levels.append(levels.prev_day_close)
        if levels.prev_day_high is not None:
            resistance_levels.append(levels.prev_day_high)
        if levels.nwog is not None:
            resistance_levels.append(levels.nwog.gap_high)
            resistance_levels.append(levels.nwog.gap_mid)
        if levels.fibonacci is not None:
            resistance_levels.append(levels.fibonacci.fib_382)
        if levels.prev_friday_close is not None:
            resistance_levels.append(levels.prev_friday_close)
        # 200SMA and 50SMA as resistance when price is below them
        if levels.sma_200 is not None and price < levels.sma_200:
            resistance_levels.append(levels.sma_200)
        if levels.sma_50 is not None and price < levels.sma_50:
            resistance_levels.append(levels.sma_50)
        # Supply zones
        for zone in levels.demand_zones:
            if zone.zone_type == "supply":
                resistance_levels.append(zone.zone_low)
                resistance_levels.append(zone.zone_high)

        for level in resistance_levels:
            if level <= 0:
                continue
            if abs(price - level) / level <= tolerance_pct:
                logger.info(
                    "at_resistance",
                    price=price,
                    level=round(level, 2),
                    distance_pct=round(abs(price - level) / level * 100, 4),
                )
                return True
        return False

    def get_levels_bias(self, symbol: str, current_price: float) -> float:
        """Get a bias score from -100 to +100 based on price position relative to key levels.

        Above key levels = bullish, below = bearish. Distance matters.

        Args:
            symbol: Ticker symbol.
            current_price: Current market price.

        Returns:
            Bias score from -100 (all below resistance) to +100 (all above support).
        """
        levels = self._levels.get(symbol)
        if levels is None:
            return 0.0

        scores: list[float] = []

        # Price vs premarket low
        if levels.premarket_low is not None and levels.premarket_low > 0:
            pct_from_pm = ((current_price - levels.premarket_low) / levels.premarket_low) * 100
            scores.append(float(min(max(pct_from_pm * 30, -100), 100)))

        # Price vs prev close
        if levels.prev_day_close is not None and levels.prev_day_close > 0:
            pct_from_close = ((current_price - levels.prev_day_close) / levels.prev_day_close) * 100
            scores.append(float(min(max(pct_from_close * 40, -100), 100)))

        # Price vs NWOG midpoint
        if levels.nwog is not None and levels.nwog.gap_mid > 0:
            pct_from_nwog = ((current_price - levels.nwog.gap_mid) / levels.nwog.gap_mid) * 100
            scores.append(float(min(max(pct_from_nwog * 25, -100), 100)))

        # Price vs session midpoint
        if levels.session_high is not None and levels.session_low is not None:
            session_mid = (levels.session_high + levels.session_low) / 2
            if session_mid > 0:
                pct_from_mid = ((current_price - session_mid) / session_mid) * 100
                scores.append(float(min(max(pct_from_mid * 35, -100), 100)))

        # Price vs 200SMA — SuperLuckeee's key macro level
        # Below 200SMA = strongly bearish, above = bullish
        if levels.sma_200 is not None and levels.sma_200 > 0:
            pct_from_200sma = ((current_price - levels.sma_200) / levels.sma_200) * 100
            # 200SMA gets heavy weighting — it's a regime-level signal
            scores.append(float(min(max(pct_from_200sma * 50, -100), 100)))

        # Price vs 50SMA — floor level
        if levels.sma_50 is not None and levels.sma_50 > 0:
            pct_from_50sma = ((current_price - levels.sma_50) / levels.sma_50) * 100
            scores.append(float(min(max(pct_from_50sma * 35, -100), 100)))

        if not scores:
            return 0.0

        avg_score = sum(scores) / len(scores)
        return round(min(max(avg_score, -100), 100), 2)
