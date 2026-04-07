"""Bias Engine — Directional Bias Scoring.

Computes a directional bias score from -100 (extreme bear) to +100 (extreme bull)
for each ticker by combining multiple technical indicators and signal sources:

    Core technical:
    - VWAP position (price vs. VWAP)               weight: 0.15
    - EMA crossovers (9/21 EMA)                     weight: 0.10
    - RSI(14) mean reversion/momentum               weight: 0.10
    - VIX level (fear/greed)                         weight: 0.10
    - Price action patterns (candle structure)        weight: 0.10

    New integrated signals:
    - Order Flow (institutional positioning)          weight: 0.25
    - Market Regime (20/50 SMA cross)                weight: 0.10
    - Key Levels (support/resistance position)        weight: 0.10

The bias score determines which trading Pillar(s) are active:
    -30 to +30  → P1 (Iron Condors) — neutral range
    Below -25   → P2 (Bear Call Spreads) — strong bearish
    Above +25   → P3 (Bull Put Spreads) — strong bullish
    ±30+        → P4 (Directional Scalps) — high conviction directional

Multi-timeframe support: bias is calculated on 5m, 15m, 1hr, daily bars
separately, then combined with weights (5m: 0.30, 15m: 0.25, 1hr: 0.25, daily: 0.20).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import Bar
from esther.signals.calendar import CalendarModule
from esther.signals.flow import FlowAnalyzer, FlowEntry
from esther.signals.levels import LevelTracker
from esther.signals.regime import RegimeDetector

logger = structlog.get_logger(__name__)

# Multi-timeframe weights
TIMEFRAME_WEIGHTS = {
    "5m": 0.30,
    "15m": 0.25,
    "1hr": 0.25,
    "daily": 0.20,
}


class Pillar(int, Enum):
    P1_IRON_CONDOR = 1
    P2_BEAR_CALL = 2
    P3_BULL_PUT = 3
    P4_DIRECTIONAL = 4
    P5_BUTTERFLY = 5


class BiasScore(BaseModel):
    """Directional bias result for a single ticker."""

    symbol: str
    score: float  # -100 to +100
    active_pillars: list[int]
    components: dict[str, float]  # individual indicator scores
    confidence: float = 1.0  # 0-1, reduced on event days
    regime_state: str = ""  # current market regime
    timeframe_scores: dict[str, float] = {}  # per-timeframe scores

    @property
    def direction(self) -> str:
        if self.score > 20:
            return "BULL"
        elif self.score < -20:
            return "BEAR"
        return "NEUTRAL"


class BiasEngine:
    """Computes directional bias from multiple technical indicators and signal sources.

    Each indicator produces a sub-score from -100 to +100,
    then they're weighted and combined into the final bias.

    Integrates:
    - RegimeDetector: macro market regime (death/golden cross)
    - FlowAnalyzer: institutional order flow direction
    - CalendarModule: event day confidence reduction
    - LevelTracker: support/resistance position scoring
    """

    def __init__(self):
        self._cfg = config().bias
        self._regime = RegimeDetector()
        self._flow = FlowAnalyzer()
        self._calendar = CalendarModule()
        self._levels = LevelTracker()

    @property
    def regime_detector(self) -> RegimeDetector:
        """Access the regime detector for external use."""
        return self._regime

    @property
    def flow_analyzer(self) -> FlowAnalyzer:
        """Access the flow analyzer for external use."""
        return self._flow

    @property
    def calendar_module(self) -> CalendarModule:
        """Access the calendar module for external use."""
        return self._calendar

    @property
    def level_tracker(self) -> LevelTracker:
        """Access the level tracker for external use."""
        return self._levels

    def compute_bias(
        self,
        symbol: str,
        bars: list[Bar],
        vix_level: float,
        current_price: float | None = None,
        daily_bars: list[Bar] | None = None,
        flow_entries: list[FlowEntry] | None = None,
    ) -> BiasScore:
        """Compute the full bias score for a ticker.

        Args:
            symbol: Ticker symbol.
            bars: Recent OHLCV bars (at least 25 for EMA/RSI calculations).
                  These are the primary timeframe bars (e.g., 5m).
            vix_level: Current VIX reading.
            current_price: Current price (uses last bar close if not provided).
            daily_bars: Daily bars for regime detection (at least 50).
                        If None, regime adjustment is skipped.
            flow_entries: Pre-fetched flow entries for the symbol.
                          If None, flow component returns 0.

        Returns:
            BiasScore with the combined score, active pillars, and metadata.
        """
        if len(bars) < 25:
            logger.warning("insufficient_bars", symbol=symbol, count=len(bars))
            return BiasScore(
                symbol=symbol, score=0.0, active_pillars=[1], components={}
            )

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars])
        price = current_price or closes[-1]

        # --- Core technical components ---
        vwap_score = self._vwap_score(closes, highs, lows, volumes, price)
        ema_score = self._ema_cross_score(closes)
        rsi_score = self._rsi_score(closes)
        vix_score = self._vix_score(vix_level)
        pa_score = self._price_action_score(closes, highs, lows)

        # --- New integrated components ---

        # Flow bias (most important signal — weight 0.25)
        flow_score = 0.0
        if flow_entries:
            flow_score = self._flow.get_flow_bias_sync(flow_entries)

        # Regime adjustment (applied as additive bonus/penalty)
        regime_adjustment = 0.0
        regime_state = ""
        if daily_bars and len(daily_bars) >= 50:
            regime_result = self._regime.detect_regime(daily_bars)
            regime_adjustment = regime_result.bias_adjustment
            regime_state = regime_result.state.value

        # Levels bias (price position relative to key S/R)
        levels_score = self._levels.get_levels_bias(symbol, price)

        # Macro data bias (from FRED economic data)
        macro_bias = self._calendar.get_macro_bias()

        # Reversal detection (SuperLuckeee 3 Reversal Rules)
        reversal_boost = self.detect_reversal(bars)

        components = {
            "vwap": round(vwap_score, 2),
            "ema_cross": round(ema_score, 2),
            "rsi": round(rsi_score, 2),
            "vix": round(vix_score, 2),
            "price_action": round(pa_score, 2),
            "flow": round(flow_score, 2),
            "regime": round(regime_adjustment, 2),
            "levels": round(levels_score, 2),
            "macro": round(macro_bias, 2),
            "reversal": round(reversal_boost, 2),
        }

        # Weighted combination using new weights
        w = self._cfg.weights
        raw_score = (
            vwap_score * w.vwap
            + ema_score * w.ema_cross
            + rsi_score * w.rsi
            + vix_score * w.vix
            + pa_score * w.price_action
            + flow_score * w.flow
            + regime_adjustment * w.regime
            + levels_score * w.levels
            + macro_bias  # Additive: -50 to +50 direct contribution
            + reversal_boost  # SuperLuckeee reversal: ±25 boost
        )

        # Clamp to [-100, 100], guard against NaN/Inf from missing data
        if not np.isfinite(raw_score):
            raw_score = 0.0
        score = float(np.clip(raw_score, -100, 100))

        # Calendar confidence adjustment
        confidence = self._calendar.get_confidence_adjustment()
        if confidence < 1.0:
            # Scale the score toward neutral on event days
            score = score * confidence
            logger.info(
                "bias_confidence_reduced",
                symbol=symbol,
                confidence=confidence,
                adjusted_score=round(score, 2),
            )

        # Determine active pillars
        active = self._determine_pillars(score, vix_level if "vix_level" in dir() else 0.0)

        result = BiasScore(
            symbol=symbol,
            score=round(score, 2),
            active_pillars=active,
            components=components,
            confidence=round(confidence, 2),
            regime_state=regime_state,
        )

        logger.info(
            "bias_computed",
            symbol=symbol,
            score=result.score,
            direction=result.direction,
            pillars=active,
            confidence=confidence,
            regime=regime_state,
        )
        return result

    def compute_multi_timeframe_bias(
        self,
        symbol: str,
        bars_5m: list[Bar],
        bars_15m: list[Bar],
        bars_1hr: list[Bar],
        bars_daily: list[Bar],
        vix_level: float,
        current_price: float | None = None,
        flow_entries: list[FlowEntry] | None = None,
    ) -> BiasScore:
        """Compute bias using multi-timeframe analysis.

        Calculates bias on each timeframe separately, then combines
        using the timeframe weights:
            5m:  0.30 (most responsive to current action)
            15m: 0.25 (medium-term structure)
            1hr: 0.25 (trend direction)
            daily: 0.20 (macro context)

        Regime and calendar adjustments are applied once to the final score,
        not per-timeframe. Flow is also applied once (it's not timeframe-specific).

        Args:
            symbol: Ticker symbol.
            bars_5m: 5-minute bars (at least 25).
            bars_15m: 15-minute bars (at least 25).
            bars_1hr: 1-hour bars (at least 25).
            bars_daily: Daily bars (at least 50 for regime).
            vix_level: Current VIX level.
            current_price: Override current price.
            flow_entries: Pre-fetched flow entries.

        Returns:
            BiasScore with combined multi-timeframe score.
        """
        price = current_price

        # Calculate per-timeframe technical scores
        tf_scores: dict[str, float] = {}

        for tf_name, tf_bars, tf_weight in [
            ("5m", bars_5m, TIMEFRAME_WEIGHTS["5m"]),
            ("15m", bars_15m, TIMEFRAME_WEIGHTS["15m"]),
            ("1hr", bars_1hr, TIMEFRAME_WEIGHTS["1hr"]),
            ("daily", bars_daily, TIMEFRAME_WEIGHTS["daily"]),
        ]:
            if len(tf_bars) < 25:
                tf_scores[tf_name] = 0.0
                continue

            closes = np.array([b.close for b in tf_bars])
            highs = np.array([b.high for b in tf_bars])
            lows = np.array([b.low for b in tf_bars])
            volumes = np.array([b.volume for b in tf_bars])
            p = price or closes[-1]

            # Core technicals only for per-timeframe scoring
            vwap = self._vwap_score(closes, highs, lows, volumes, p)
            ema = self._ema_cross_score(closes)
            rsi = self._rsi_score(closes)
            pa = self._price_action_score(closes, highs, lows)

            # Equal weight within each timeframe for the technical core
            tf_score = (vwap + ema + rsi + pa) / 4.0
            tf_scores[tf_name] = round(tf_score, 2)

        # Weighted timeframe combination (technical component)
        tech_score = sum(
            tf_scores.get(tf, 0.0) * w
            for tf, w in TIMEFRAME_WEIGHTS.items()
        )

        # Now layer on the non-timeframe signals
        vix_score = self._vix_score(vix_level)
        flow_score = self._flow.get_flow_bias_sync(flow_entries) if flow_entries else 0.0
        regime_adjustment = 0.0
        regime_state = ""
        if bars_daily and len(bars_daily) >= 50:
            regime_result = self._regime.detect_regime(bars_daily)
            regime_adjustment = regime_result.bias_adjustment
            regime_state = regime_result.state.value

        levels_score = self._levels.get_levels_bias(
            symbol, price or bars_5m[-1].close if bars_5m else 0.0
        )

        # Final combination with all weights
        w = self._cfg.weights
        # Technical core gets the combined weights of vwap+ema+rsi+price_action
        tech_weight = w.vwap + w.ema_cross + w.rsi + w.price_action
        raw_score = (
            tech_score * tech_weight
            + vix_score * w.vix
            + flow_score * w.flow
            + regime_adjustment * w.regime
            + levels_score * w.levels
        )

        score = float(np.clip(raw_score, -100, 100))

        # Calendar adjustment
        confidence = self._calendar.get_confidence_adjustment()
        if confidence < 1.0:
            score *= confidence

        active = self._determine_pillars(score, vix_level if "vix_level" in dir() else 0.0)

        components = {
            "tech_5m": tf_scores.get("5m", 0.0),
            "tech_15m": tf_scores.get("15m", 0.0),
            "tech_1hr": tf_scores.get("1hr", 0.0),
            "tech_daily": tf_scores.get("daily", 0.0),
            "vix": round(vix_score, 2),
            "flow": round(flow_score, 2),
            "regime": round(regime_adjustment, 2),
            "levels": round(levels_score, 2),
        }

        result = BiasScore(
            symbol=symbol,
            score=round(score, 2),
            active_pillars=active,
            components=components,
            confidence=round(confidence, 2),
            regime_state=regime_state,
            timeframe_scores=tf_scores,
        )

        logger.info(
            "multi_tf_bias_computed",
            symbol=symbol,
            score=result.score,
            direction=result.direction,
            pillars=active,
            tf_scores=tf_scores,
            regime=regime_state,
        )
        return result

    def _vwap_score(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
        current_price: float,
    ) -> float:
        """Score based on price position relative to VWAP.

        VWAP = cumulative(typical_price * volume) / cumulative(volume)
        Price above VWAP → bullish; below → bearish.
        Score scaled by distance from VWAP as % of price.
        """
        typical_prices = (highs + lows + closes) / 3
        cum_tp_vol = np.cumsum(typical_prices * volumes)
        cum_vol = np.cumsum(volumes)

        # Avoid division by zero
        mask = cum_vol > 0
        if not mask.any():
            return 0.0

        vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else closes[-1]

        # Distance from VWAP as percentage
        distance_pct = ((current_price - vwap) / vwap) * 100

        # Scale: ±2% from VWAP = ±100 score
        return float(np.clip(distance_pct * 50, -100, 100))

    def _ema_cross_score(self, closes: np.ndarray) -> float:
        """Score based on EMA(fast) vs EMA(slow) crossover.

        Fast EMA above slow → bullish; below → bearish.
        Score based on distance between EMAs as % of price.
        Also checks for recent crossover (stronger signal).
        """
        fast_period = self._cfg.ema["fast"]
        slow_period = self._cfg.ema["slow"]

        fast_ema = self._compute_ema(closes, fast_period)
        slow_ema = self._compute_ema(closes, slow_period)

        # Current spread as percentage
        spread_pct = ((fast_ema[-1] - slow_ema[-1]) / slow_ema[-1]) * 100

        # Check for recent crossover (within last 3 bars) — amplify signal
        crossover_bonus = 0.0
        if len(fast_ema) >= 3 and len(slow_ema) >= 3:
            for i in range(-3, 0):
                prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
                curr_diff = fast_ema[i] - slow_ema[i]
                if prev_diff <= 0 < curr_diff:  # bullish crossover
                    crossover_bonus = 30.0
                elif prev_diff >= 0 > curr_diff:  # bearish crossover
                    crossover_bonus = -30.0

        # Scale: ±1% spread = ±70, plus crossover bonus
        score = spread_pct * 70 + crossover_bonus
        return float(np.clip(score, -100, 100))

    def _rsi_score(self, closes: np.ndarray) -> float:
        """Score based on RSI(14).

        RSI mapping to bias score:
            RSI > 70 (overbought) → bearish (mean reversion expected)
            RSI < 30 (oversold)   → bullish (mean reversion expected)
            RSI 45-55             → neutral
            RSI 55-70             → mildly bullish (momentum)
            RSI 30-45             → mildly bearish (momentum)

        This captures both momentum and mean reversion — RSI between 30-70
        is treated as momentum, extreme RSI as mean reversion.
        """
        rsi = self._compute_rsi(closes, self._cfg.rsi["period"])

        if rsi is None:
            return 0.0

        overbought = self._cfg.rsi["overbought"]
        oversold = self._cfg.rsi["oversold"]

        if rsi >= overbought:
            # Overbought → expect pullback → bearish
            return float(np.clip(-(rsi - overbought) * 3.3, -100, 0))
        elif rsi <= oversold:
            # Oversold → expect bounce → bullish
            return float(np.clip((oversold - rsi) * 3.3, 0, 100))
        else:
            # Middle zone: momentum reading
            # RSI 50 = neutral, RSI 60 = mildly bullish, RSI 40 = mildly bearish
            return float((rsi - 50) * 2.5)

    def _vix_score(self, vix_level: float) -> float:
        """Score based on VIX level.

        Low VIX (< 15) → complacency → slightly bearish (correction risk)
        Normal VIX (15-20) → neutral
        Elevated VIX (20-30) → fear → contrarian bullish (bounce expected)
        High VIX (> 30) → NOT automatically bearish. SuperLuckeee explicitly
            says "VIX at 30 = BEST time for iron condors" because premium is fat.
            Mildly bearish for direction but BULLISH for IC profitability.
        Extreme VIX (> 35) → capitulation zone, historical bottom (April 2025 pattern)
        """
        if vix_level > 35:
            return -60.0  # Capitulation — bearish but watch for reversal
        elif vix_level > 30:
            # VIX 30+ = elevated regime. NOT panic shutdown.
            # SuperLuckeee: "used when IV is high (VIX at 30)" = IC sweet spot.
            # Mildly bearish for direction, but P1 (IC) should be PRIORITIZED.
            return -30.0
        elif vix_level > 25:
            return -40.0  # High fear
        elif vix_level > 20:
            return 20.0  # Elevated but contrarian bullish
        elif vix_level > 15:
            return 0.0  # Normal
        else:
            return -15.0  # Complacent — slight correction risk

    def _price_action_score(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
    ) -> float:
        """Score based on recent price action patterns.

        Analyzes the last 5 bars for:
        - Higher highs / lower lows trend
        - Body size relative to range (conviction)
        - Closing position within range (buying/selling pressure)
        """
        if len(closes) < 5:
            return 0.0

        # Last 5 bars
        recent_closes = closes[-5:]
        recent_highs = highs[-5:]
        recent_lows = lows[-5:]

        score = 0.0

        # Higher highs count vs lower lows count
        hh_count = sum(
            1 for i in range(1, len(recent_highs))
            if recent_highs[i] > recent_highs[i - 1]
        )
        ll_count = sum(
            1 for i in range(1, len(recent_lows))
            if recent_lows[i] < recent_lows[i - 1]
        )

        # More higher highs → bullish, more lower lows → bearish
        score += (hh_count - ll_count) * 15

        # Closing position within range for the last bar
        # Close near high = bullish pressure, close near low = bearish
        last_range = recent_highs[-1] - recent_lows[-1]
        if last_range > 0:
            close_position = (recent_closes[-1] - recent_lows[-1]) / last_range
            # 0.0 = closed at low, 1.0 = closed at high
            score += (close_position - 0.5) * 40

        # Net movement over 5 bars
        net_change = (recent_closes[-1] - recent_closes[0]) / recent_closes[0] * 100
        score += net_change * 20

        return float(np.clip(score, -100, 100))

    def is_choppy(
        self,
        bars: list[Bar],
        bias_score: float,
        vix_level: float,
    ) -> dict[str, Any]:
        """Detect range-bound/choppy market conditions.

        From @SuperLuckeee's Lever 1: "Skip chop."
        Chop KILLS directional traders but is GREAT for iron condors.

        When choppy:
        - P1 (IC) = ALLOWED (chop = free money for premium sellers)
        - P2/P3/P4 = BLOCKED (no directional edge in chop)

        Conditions checked:
        1. Price within tight range (< 0.3% from session midpoint)
        2. Bias score is super neutral (-15 to +15)
        3. ATR of last 10 bars < 50% of 20-bar ATR average
        4. VIX is flat/low (no fear = no direction)

        Args:
            bars: Recent OHLCV bars (at least 20).
            bias_score: Current bias engine score.
            vix_level: Current VIX reading.

        Returns:
            Dict with is_choppy, chop_score (0-100), reasons, and allowed_pillars.
        """
        reasons: list[str] = []
        chop_signals = 0
        total_signals = 4

        if len(bars) < 20:
            return {"is_choppy": False, "chop_score": 0.0, "reasons": ["insufficient_bars"], "allowed_pillars": [1, 2, 3, 4]}

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])

        # Signal 1: Tight price range — session midpoint test
        recent_high = float(highs[-10:].max())
        recent_low = float(lows[-10:].min())
        recent_mid = (recent_high + recent_low) / 2
        current_price = float(closes[-1])

        if recent_mid > 0:
            distance_from_mid_pct = abs(current_price - recent_mid) / recent_mid
            if distance_from_mid_pct < 0.003:  # < 0.3% from midpoint
                chop_signals += 1
                reasons.append(f"TIGHT_RANGE: price {distance_from_mid_pct:.2%} from session midpoint")

        # Signal 2: Super neutral bias
        if -15 <= bias_score <= 15:
            chop_signals += 1
            reasons.append(f"NEUTRAL_BIAS: score {bias_score:.1f} in dead zone [-15, +15]")

        # Signal 3: Declining ATR (volatility contracting)
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(float(tr))

        if len(trs) >= 20:
            atr_10 = sum(trs[-10:]) / 10
            atr_20 = sum(trs[-20:]) / 20
            if atr_20 > 0 and atr_10 / atr_20 < 0.50:
                chop_signals += 1
                reasons.append(f"ATR_CONTRACTING: 10-bar ATR is {atr_10/atr_20:.0%} of 20-bar (< 50%)")

        # Signal 4: Low/flat VIX
        if vix_level < 18:
            chop_signals += 1
            reasons.append(f"LOW_VIX: {vix_level:.1f} = complacent market, no fear = no direction")

        chop_score = (chop_signals / total_signals) * 100
        is_choppy = chop_signals >= 3  # Need 3 of 4 signals

        # When choppy: ICs are great, directional is death
        allowed_pillars = [1, 2, 3, 4] if not is_choppy else [1]

        if is_choppy:
            logger.warning(
                "chop_detected",
                chop_score=chop_score,
                signals=f"{chop_signals}/{total_signals}",
                reasons=reasons,
                allowed_pillars=allowed_pillars,
            )

        return {
            "is_choppy": is_choppy,
            "chop_score": round(chop_score, 1),
            "reasons": reasons,
            "allowed_pillars": allowed_pillars,
        }

    def detect_reversal(self, bars: list[Bar], premarket_low: float | None = None) -> float:
        """Detect reversal pattern per SuperLuckeee 3 Reversal Rules.

        Checks:
        1. Price held/bounced off premarket low (support)
        2. Last 5-minute candle closed GREEN above PM low → bullish reversal
        3. Reverse for bearish: price rejected premarket high + last candle RED

        Returns:
            Bias boost: positive for bullish reversal, negative for bearish, 0 if none.
        """
        if len(bars) < 3:
            return 0.0

        last_bar = bars[-1]
        prev_bar = bars[-2]

        # If premarket low not provided, estimate from first few bars of session
        if premarket_low is None:
            # Use lowest low of first 6 bars (~30 min of 5m bars) as proxy
            session_start_bars = bars[:min(6, len(bars))]
            premarket_low = min(b.low for b in session_start_bars)

        # Estimate premarket high similarly
        premarket_high = max(b.high for b in bars[:min(6, len(bars))])

        boost = 0.0

        # Bullish reversal: price dipped to PM low, last candle closed GREEN above it
        last_candle_green = last_bar.close > last_bar.open
        touched_pm_low = any(
            b.low <= premarket_low * 1.002 for b in bars[-5:]
        )
        if touched_pm_low and last_candle_green and last_bar.close > premarket_low:
            boost = 25.0
            logger.info(
                "reversal_bullish_detected",
                pm_low=premarket_low,
                last_close=last_bar.close,
                last_open=last_bar.open,
            )

        # Bearish reversal: price hit PM high, last candle closed RED below it
        last_candle_red = last_bar.close < last_bar.open
        touched_pm_high = any(
            b.high >= premarket_high * 0.998 for b in bars[-5:]
        )
        if touched_pm_high and last_candle_red and last_bar.close < premarket_high:
            boost = -25.0
            logger.info(
                "reversal_bearish_detected",
                pm_high=premarket_high,
                last_close=last_bar.close,
                last_open=last_bar.open,
            )

        return boost

    def is_ic_favorable_vix(self, vix_level: float) -> bool:
        """Check if VIX is in the iron condor sweet spot (25-35).

        SuperLuckeee: "The fastest way to grow a small account (iron condor strategy)
        — used when IV is high (volatility like the VIX is at 30)"

        When VIX is elevated, IC premium is fat and the strategy excels.
        This flag tells the engine to PRIORITIZE P1 (IC) even if directional
        signals suggest P2/P3/P4.

        Args:
            vix_level: Current VIX reading.

        Returns:
            True if ICs should be prioritized.
        """
        return 25.0 <= vix_level <= 35.0

    def _determine_pillars(self, score: float, vix: float = 0.0) -> list[int]:
        """Map bias score to active trading pillars.

        SuperLuckeee rule:
        - Strong directional bias (|score| > 35) + high VIX (>25) → P4 ONLY (buy puts/calls)
          This is the "tariff panic" scenario — market is moving hard, ride the wave
        - Neutral zone → P1 IC (sell premium)
        - Moderate directional → P2/P3 spreads
        """
        ranges = self._cfg.pillar_ranges
        active: list[int] = []

        # SuperLuckeee rule: High conviction + High VIX = BUY DIRECTIONAL, skip IC
        # When market is moving hard (|bias|>35) and VIX elevated (>25),
        # ICs get crushed — go directional instead
        strong_directional = abs(score) >= 35
        high_vix = vix >= 25.0
        if strong_directional and high_vix:
            # Pure directional mode — P4 scalps + P2/P3 spreads, NO IC
            if score <= ranges["p2_threshold"]:
                active.append(2)  # Bear call spread
            if score >= ranges["p3_threshold"]:
                active.append(3)  # Bull put spread
            active.append(4)      # Always directional scalp
            return sorted(active)

        # P1: Iron Condors — neutral zone (safe when market is calm)
        if ranges["p1_low"] <= score <= ranges["p1_high"]:
            active.append(1)

        # P2: Bear Call Spreads — strong bearish
        if score <= ranges["p2_threshold"]:
            active.append(2)

        # P3: Bull Put Spreads — strong bullish
        if score >= ranges["p3_threshold"]:
            active.append(3)

        # P4: Directional Scalps — high conviction either way
        if abs(score) >= ranges["p4_threshold"]:
            active.append(4)

        # P5: Butterfly Spreads — moderate conviction
        if "p5_threshold" in ranges and abs(score) >= ranges["p5_threshold"]:
            active.append(5)

        # Default to P1 if nothing activated (shouldn't happen, but safety net)
        if not active:
            active.append(1)

        return sorted(active)

    @staticmethod
    def _compute_ema(data: np.ndarray, period: int) -> np.ndarray:
        """Compute Exponential Moving Average."""
        multiplier = 2.0 / (period + 1)
        ema = np.zeros_like(data, dtype=float)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]
        return ema

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int = 14) -> float | None:
        """Compute RSI using Wilder's smoothing method."""
        if len(closes) < period + 1:
            return None

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Initial average gain/loss
        avg_gain = gains[:period].mean()
        avg_loss = losses[:period].mean()

        # Wilder's smoothing for remaining periods
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
