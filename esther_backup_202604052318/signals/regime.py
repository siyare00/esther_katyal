"""Market Regime Detection — 20/50 SMA Cross System.

Detects macro regime changes using Simple Moving Average crossovers:
    - Golden Cross (20SMA > 50SMA) = BULLISH regime → +20 bias bonus
    - Death Cross (20SMA < 50SMA) = BEARISH regime → -30 bias penalty
    - Cross detected today = TRANSITIONING with extra urgency

The regime bias adjustment is applied across ALL tickers, not per-symbol.
This is a market-wide signal that shifts the entire trading posture.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import Bar

logger = structlog.get_logger(__name__)


class RegimeState(str, Enum):
    """Market regime states based on SMA crossover."""

    BULLISH = "BULLISH"           # Golden cross — 20SMA > 50SMA
    BEARISH = "BEARISH"           # Death cross — 20SMA < 50SMA
    TRANSITIONING = "TRANSITIONING"  # Cross detected today — extra urgency


class RegimeResult(BaseModel):
    """Result of regime detection analysis."""

    state: RegimeState
    sma_fast: float          # Current fast SMA value
    sma_slow: float          # Current slow SMA value
    spread_pct: float        # Spread between SMAs as percentage
    cross_today: bool        # Whether a cross happened on the last bar
    bias_adjustment: float   # The penalty/bonus to apply to bias engine
    bars_since_cross: int    # How many bars since the last cross


class RegimeDetector:
    """Detects market regime using SMA crossovers on SPX/SPY daily bars.

    The regime signal is the slowest-moving component of the bias engine.
    It takes many days for SMAs to cross, so regime changes are rare but
    significant. A death cross is a strong bearish signal that should
    reduce bullish exposure across all tickers.

    Typical usage:
        detector = RegimeDetector()
        result = detector.detect_regime(daily_bars)
        adjustment = result.bias_adjustment  # apply to all symbols
    """

    # Bias adjustments for each regime
    BULLISH_BONUS = 20.0
    BEARISH_PENALTY = -30.0
    TRANSITION_MULTIPLIER = 1.5  # Extra urgency when cross is fresh

    def __init__(self):
        self._cfg = config().regime
        self._last_result: RegimeResult | None = None

    def detect_regime(self, bars: list[Bar]) -> RegimeResult:
        """Detect the current market regime from daily bars.

        Requires at least sma_slow bars of data. Calculates 20-day and
        50-day SMAs, determines if we're in a golden or death cross,
        and checks if the cross happened on the most recent bar.

        Args:
            bars: Daily OHLCV bars for SPX or SPY. Must have at least
                  sma_slow (default 50) bars.

        Returns:
            RegimeResult with state, SMA values, and bias adjustment.
        """
        fast_period = self._cfg.sma_fast
        slow_period = self._cfg.sma_slow

        if len(bars) < slow_period:
            logger.warning(
                "insufficient_bars_for_regime",
                needed=slow_period,
                got=len(bars),
            )
            return RegimeResult(
                state=RegimeState.BULLISH,  # default to bullish (no data)
                sma_fast=0.0,
                sma_slow=0.0,
                spread_pct=0.0,
                cross_today=False,
                bias_adjustment=0.0,
                bars_since_cross=0,
            )

        closes = np.array([b.close for b in bars])

        # Calculate SMAs
        sma_fast_arr = self._compute_sma(closes, fast_period)
        sma_slow_arr = self._compute_sma(closes, slow_period)

        # Current values (last element)
        current_fast = float(sma_fast_arr[-1])
        current_slow = float(sma_slow_arr[-1])

        # Spread as percentage
        spread_pct = ((current_fast - current_slow) / current_slow) * 100 if current_slow > 0 else 0.0

        # Determine state
        if current_fast > current_slow:
            base_state = RegimeState.BULLISH
        else:
            base_state = RegimeState.BEARISH

        # Check for cross on the most recent bar
        cross_today = False
        if len(sma_fast_arr) >= 2 and len(sma_slow_arr) >= 2:
            prev_fast = sma_fast_arr[-2]
            prev_slow = sma_slow_arr[-2]
            prev_above = prev_fast > prev_slow
            curr_above = current_fast > current_slow

            if prev_above != curr_above:
                cross_today = True
                logger.warning(
                    "regime_cross_detected",
                    cross_type="golden" if curr_above else "death",
                    sma_fast=round(current_fast, 2),
                    sma_slow=round(current_slow, 2),
                )

        # Count bars since last cross
        bars_since_cross = self._count_bars_since_cross(sma_fast_arr, sma_slow_arr)

        # Final state — if cross happened today, it's transitioning
        state = RegimeState.TRANSITIONING if cross_today else base_state

        # Calculate bias adjustment
        if base_state == RegimeState.BULLISH:
            adjustment = self.BULLISH_BONUS
        else:
            adjustment = self.BEARISH_PENALTY

        # Amplify if cross is fresh (within last 3 bars)
        if cross_today or bars_since_cross <= 3:
            adjustment *= self.TRANSITION_MULTIPLIER

        result = RegimeResult(
            state=state,
            sma_fast=round(current_fast, 2),
            sma_slow=round(current_slow, 2),
            spread_pct=round(spread_pct, 4),
            cross_today=cross_today,
            bias_adjustment=round(adjustment, 2),
            bars_since_cross=bars_since_cross,
        )

        self._last_result = result

        logger.info(
            "regime_detected",
            state=state.value,
            sma_fast=result.sma_fast,
            sma_slow=result.sma_slow,
            spread_pct=result.spread_pct,
            adjustment=result.bias_adjustment,
        )
        return result

    def get_regime_bias_adjustment(self) -> float:
        """Get the current regime bias adjustment.

        Returns the penalty/bonus to apply to the bias engine.
        If no regime has been detected yet, returns 0.

        Returns:
            Float adjustment: +20 for bullish, -30 for bearish,
            amplified by 1.5x if cross is fresh.
        """
        if self._last_result is None:
            return 0.0
        return self._last_result.bias_adjustment

    def get_last_result(self) -> RegimeResult | None:
        """Get the most recent regime detection result."""
        return self._last_result

    @staticmethod
    def _compute_sma(data: np.ndarray, period: int) -> np.ndarray:
        """Compute Simple Moving Average.

        Uses a cumulative sum approach for efficiency.

        Args:
            data: Array of prices.
            period: SMA lookback period.

        Returns:
            Array of SMA values (same length as input, NaN-padded at start).
        """
        if len(data) < period:
            return np.full_like(data, np.nan, dtype=float)

        cumsum = np.cumsum(data, dtype=float)
        sma = np.full_like(data, np.nan, dtype=float)
        sma[period - 1] = cumsum[period - 1] / period
        for i in range(period, len(data)):
            sma[i] = (cumsum[i] - cumsum[i - period]) / period
        return sma

    @staticmethod
    def _count_bars_since_cross(
        sma_fast: np.ndarray, sma_slow: np.ndarray
    ) -> int:
        """Count how many bars since the last SMA cross.

        Walks backward from the most recent bar until we find a sign change.

        Args:
            sma_fast: Fast SMA array.
            sma_slow: Slow SMA array.

        Returns:
            Number of bars since the last crossover.
        """
        # Find valid range (both SMAs have values)
        valid_start = 0
        for i in range(len(sma_fast)):
            if not (np.isnan(sma_fast[i]) or np.isnan(sma_slow[i])):
                valid_start = i
                break

        if valid_start >= len(sma_fast) - 1:
            return len(sma_fast)  # No valid cross data

        # Current direction
        current_above = sma_fast[-1] > sma_slow[-1]

        # Walk backward
        count = 0
        for i in range(len(sma_fast) - 2, valid_start - 1, -1):
            if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
                break
            was_above = sma_fast[i] > sma_slow[i]
            if was_above != current_above:
                return count
            count += 1

        return count
