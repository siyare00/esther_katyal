"""Tests for the Bias Engine — directional bias scoring and pillar eligibility."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pytest

from esther.signals.bias_engine import BiasEngine, BiasScore, Pillar
from esther.data.tradier import Bar


# ── Fixtures ─────────────────────────────────────────────────────


def _make_bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> list[Bar]:
    """Generate a list of Bar objects from close prices.

    If highs/lows/volumes are not provided, they are derived from closes.
    """
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n

    return [
        Bar(
            timestamp=datetime(2024, 3, i + 1) if i < 28 else datetime(2024, 4, i - 27),
            open=closes[i],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
        )
        for i in range(n)
    ]


def _uptrend_bars(n: int = 30, start: float = 500.0, step: float = 1.5) -> list[Bar]:
    """Generate an uptrending series of bars."""
    closes = [start + i * step for i in range(n)]
    highs = [c + 2.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return _make_bars(closes, highs, lows)


def _downtrend_bars(n: int = 30, start: float = 500.0, step: float = 1.5) -> list[Bar]:
    """Generate a downtrending series of bars."""
    closes = [start - i * step for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    return _make_bars(closes, highs, lows)


def _flat_bars(n: int = 30, price: float = 500.0) -> list[Bar]:
    """Generate a flat/sideways series of bars."""
    # Small random-ish oscillation around the price
    closes = [price + (i % 3 - 1) * 0.5 for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return _make_bars(closes, highs, lows)


@pytest.fixture
def engine():
    """Create a BiasEngine with default config."""
    with patch("esther.signals.bias_engine.config") as mock_config:
        from esther.core.config import BiasConfig
        mock_config.return_value.bias = BiasConfig()
        return BiasEngine()


# ── Bias Calculation Tests ───────────────────────────────────────


class TestBiasComputation:
    """Test bias score calculation with various market conditions."""

    def test_uptrend_produces_positive_bias(self, engine):
        """An uptrending market should produce a bullish (positive) bias."""
        bars = _uptrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=18.0)

        assert result.score > 0, f"Expected positive bias for uptrend, got {result.score}"
        assert result.direction == "BULL"
        assert result.symbol == "SPY"

    def test_downtrend_produces_negative_bias(self, engine):
        """A downtrending market should produce a bearish (negative) bias."""
        bars = _downtrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=22.0)

        assert result.score < 0, f"Expected negative bias for downtrend, got {result.score}"
        assert result.direction == "BEAR"

    def test_flat_market_produces_neutral_bias(self, engine):
        """A flat/sideways market should produce a near-neutral bias."""
        bars = _flat_bars(30)
        result = engine.compute_bias("QQQ", bars, vix_level=17.0)

        assert -30 <= result.score <= 30, f"Expected neutral-ish bias for flat market, got {result.score}"

    def test_score_clamped_to_range(self, engine):
        """Bias score should always be between -100 and +100."""
        # Extreme uptrend
        bars = _uptrend_bars(30, step=10.0)
        result = engine.compute_bias("SPX", bars, vix_level=12.0)
        assert -100 <= result.score <= 100

        # Extreme downtrend
        bars = _downtrend_bars(30, start=500.0, step=10.0)
        result = engine.compute_bias("SPX", bars, vix_level=35.0)
        assert -100 <= result.score <= 100

    def test_insufficient_bars_returns_neutral(self, engine):
        """With fewer than 25 bars, should return neutral score of 0."""
        bars = _flat_bars(10)
        result = engine.compute_bias("SPY", bars, vix_level=20.0)

        assert result.score == 0.0
        assert 1 in result.active_pillars  # defaults to P1

    def test_components_are_populated(self, engine):
        """The components dict should have all 8 indicator scores (incl. flow, regime, levels)."""
        bars = _uptrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=18.0)

        expected_keys = {"vwap", "ema_cross", "rsi", "price_action", "vix", "flow", "regime", "levels", "macro"}
        assert set(result.components.keys()) == expected_keys

        # Each component should be numeric and bounded
        for key, value in result.components.items():
            assert isinstance(value, float), f"{key} is not float"
            assert -100 <= value <= 100, f"{key}={value} out of range"

    def test_current_price_override(self, engine):
        """Passing a current_price should use it instead of last close."""
        bars = _flat_bars(30, price=500.0)

        # Override with a much higher price (above VWAP)
        result_high = engine.compute_bias("SPY", bars, vix_level=18.0, current_price=520.0)
        result_low = engine.compute_bias("SPY", bars, vix_level=18.0, current_price=480.0)

        # Higher price should produce higher (more bullish) bias
        assert result_high.score > result_low.score


class TestVixScore:
    """Test VIX contribution to bias."""

    def test_panic_vix_is_bearish(self, engine):
        """VIX > 35 = capitulation zone, VIX 30-35 = IC sweet spot (mildly bearish, not panic).

        SuperLuckeee: "used when IV is high (VIX at 30)" — IC premium is fat.
        VIX 35 is the transition to capitulation (-60), not shutdown (-80).
        """
        score = engine._vix_score(36.0)
        assert score == -60.0  # Capitulation zone
        score_30 = engine._vix_score(32.0)
        assert score_30 == -30.0  # IC sweet spot — mildly bearish, not panic

    def test_elevated_vix_is_mildly_bearish(self, engine):
        """VIX 25-30 should be moderately bearish."""
        score = engine._vix_score(27.0)
        assert score == -40.0

    def test_above_average_vix_is_contrarian_bullish(self, engine):
        """VIX 20-25 is contrarian bullish (fear = opportunity)."""
        score = engine._vix_score(22.0)
        assert score == 20.0

    def test_normal_vix_is_neutral(self, engine):
        """VIX 15-20 should be neutral."""
        score = engine._vix_score(17.0)
        assert score == 0.0

    def test_low_vix_is_slightly_bearish(self, engine):
        """VIX < 15 = complacency, slight correction risk."""
        score = engine._vix_score(12.0)
        assert score == -15.0


class TestRSIScore:
    """Test RSI contribution to bias."""

    def test_overbought_rsi_is_bearish(self, engine):
        """RSI > 70 should produce a bearish score (mean reversion)."""
        # Need enough bars for RSI calc
        # Build a consistently up series that pushes RSI high
        closes = np.array([100.0 + i * 0.8 for i in range(30)])
        score = engine._rsi_score(closes)
        # RSI should be high, score should be negative
        assert score <= 0

    def test_oversold_rsi_is_bullish(self, engine):
        """RSI < 30 should produce a bullish score (mean reversion)."""
        closes = np.array([100.0 - i * 0.8 for i in range(30)])
        score = engine._rsi_score(closes)
        # RSI should be low, score should be positive
        assert score >= 0

    def test_neutral_rsi(self, engine):
        """RSI around 50 should produce a near-zero score."""
        # Alternating up/down to keep RSI near 50
        closes = np.array([100.0 + (i % 2) * 0.3 - 0.15 for i in range(30)])
        score = engine._rsi_score(closes)
        assert -25 <= score <= 25


# ── Pillar Eligibility Tests ────────────────────────────────────


class TestPillarEligibility:
    """Test that bias scores correctly map to active pillars."""

    def test_neutral_zone_activates_p1(self, engine):
        """Score in [-20, +20] should activate P1 (Iron Condors)."""
        pillars = engine._determine_pillars(0.0)
        assert 1 in pillars

        pillars = engine._determine_pillars(15.0)
        assert 1 in pillars

        pillars = engine._determine_pillars(-15.0)
        assert 1 in pillars

    def test_strong_bearish_activates_p2(self, engine):
        """Score <= -60 should activate P2 (Bear Call Spreads)."""
        pillars = engine._determine_pillars(-65.0)
        assert 2 in pillars

        pillars = engine._determine_pillars(-100.0)
        assert 2 in pillars

    def test_strong_bullish_activates_p3(self, engine):
        """Score >= +60 should activate P3 (Bull Put Spreads)."""
        pillars = engine._determine_pillars(65.0)
        assert 3 in pillars

        pillars = engine._determine_pillars(100.0)
        assert 3 in pillars

    def test_high_conviction_activates_p4(self, engine):
        """Score with |score| >= 40 should activate P4 (Directional Scalps)."""
        pillars = engine._determine_pillars(50.0)
        assert 4 in pillars

        pillars = engine._determine_pillars(-50.0)
        assert 4 in pillars

    def test_overlap_zone_p3_and_p4(self, engine):
        """Score of +65 should activate both P3 and P4."""
        pillars = engine._determine_pillars(65.0)
        assert 3 in pillars
        assert 4 in pillars

    def test_overlap_zone_p2_and_p4(self, engine):
        """Score of -65 should activate both P2 and P4."""
        pillars = engine._determine_pillars(-65.0)
        assert 2 in pillars
        assert 4 in pillars

    def test_no_p1_outside_neutral(self, engine):
        """Score outside [-20, +20] should NOT activate P1."""
        pillars = engine._determine_pillars(50.0)
        assert 1 not in pillars

        pillars = engine._determine_pillars(-50.0)
        assert 1 not in pillars

    def test_p4_not_activated_below_threshold(self, engine):
        """Score < 40 magnitude should NOT activate P4."""
        pillars = engine._determine_pillars(30.0)
        assert 4 not in pillars

        pillars = engine._determine_pillars(-30.0)
        assert 4 not in pillars

    def test_default_to_p1_when_nothing_matches(self, engine):
        """Scores in the gap zones (e.g., +30) should default to P1."""
        # Score of +30: outside P1 range (>20), below P3 (60), below P4 (40)
        pillars = engine._determine_pillars(30.0)
        # With default config, 30 > p1_high(20), < p3(60), < p4(40) → empty → defaults to P1
        assert 1 in pillars

    def test_pillars_always_sorted(self, engine):
        """Active pillars list should always be sorted."""
        for score in [-80, -50, -10, 0, 10, 50, 80]:
            pillars = engine._determine_pillars(float(score))
            assert pillars == sorted(pillars)


# ── EMA/RSI Computation Tests ───────────────────────────────────


class TestTechnicalHelpers:
    """Test the internal EMA and RSI computation methods."""

    def test_ema_follows_data(self):
        """EMA should trend with the data direction."""
        data = np.array([10.0 + i for i in range(20)])
        ema = BiasEngine._compute_ema(data, 9)

        # EMA should be increasing
        for i in range(5, len(ema)):
            assert ema[i] > ema[i - 1]

        # EMA should lag below the price in an uptrend
        assert ema[-1] < data[-1]

    def test_ema_first_value_equals_first_data(self):
        """EMA[0] should equal data[0]."""
        data = np.array([50.0, 55.0, 48.0, 52.0, 51.0])
        ema = BiasEngine._compute_ema(data, 3)
        assert ema[0] == 50.0

    def test_rsi_bounds(self):
        """RSI should always be between 0 and 100."""
        # All up
        closes_up = np.array([100.0 + i for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes_up, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100

        # All down
        closes_down = np.array([100.0 - i * 0.5 for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes_down, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_rsi_none_with_insufficient_data(self):
        """RSI returns None if not enough data for the period."""
        closes = np.array([100.0, 101.0, 99.0])
        rsi = BiasEngine._compute_rsi(closes, 14)
        assert rsi is None

    def test_rsi_100_for_all_gains(self):
        """RSI should be 100 if there are only gains (no losses)."""
        closes = np.array([100.0 + i * 2.0 for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes, 14)
        assert rsi == 100.0


# ── Edge Cases ───────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_extreme_positive_score(self, engine):
        """Extreme uptrend + low VIX should push score bullish.

        Note: With flow/regime/levels at 0 (no data), the score is more moderate.
        Core technicals only contribute ~45% of total weight (flow=25%, regime=10%, levels=10% = 0).
        Score of +20 is a clear bullish signal from technicals alone.
        """
        bars = _uptrend_bars(30, step=8.0)
        result = engine.compute_bias("SPY", bars, vix_level=12.0)
        assert result.score > 15, f"Expected bullish, got {result.score}"

    def test_extreme_negative_score(self, engine):
        """Extreme downtrend + high VIX should push score bearish.

        With flow/regime/levels at 0, score is moderated. -25 is a clear bearish signal.
        """
        bars = _downtrend_bars(30, start=500.0, step=8.0)
        result = engine.compute_bias("SPY", bars, vix_level=35.0)
        assert result.score < -25, f"Expected bearish, got {result.score}"

    def test_zero_volume_bars(self, engine):
        """Bars with zero volume should not crash the VWAP calculation."""
        closes = [500.0 + i * 0.5 for i in range(30)]
        bars = _make_bars(closes, volumes=[0] * 30)
        # Should not raise
        result = engine.compute_bias("SPY", bars, vix_level=18.0)
        assert isinstance(result, BiasScore)

    def test_identical_bars(self, engine):
        """All identical bars should produce a neutral-ish score."""
        bars = _make_bars([500.0] * 30)
        result = engine.compute_bias("SPY", bars, vix_level=17.0)
        # With zero movement, most components should be near zero
        assert -30 <= result.score <= 30

    def test_single_spike_bar(self, engine):
        """A big spike on the last bar should affect bias."""
        closes = [500.0] * 29 + [520.0]  # 4% spike on last bar
        highs = [501.0] * 29 + [521.0]
        lows = [499.0] * 29 + [500.0]
        bars = _make_bars(closes, highs, lows)

        result = engine.compute_bias("SPY", bars, vix_level=18.0)
        # Spike should produce a bullish signal
        assert result.score > 0

    def test_bias_score_model_properties(self, engine):
        """Test the BiasScore model's direction property."""
        bullish = BiasScore(symbol="SPY", score=50.0, active_pillars=[3, 4], components={})
        assert bullish.direction == "BULL"

        bearish = BiasScore(symbol="SPY", score=-50.0, active_pillars=[2, 4], components={})
        assert bearish.direction == "BEAR"

        neutral = BiasScore(symbol="SPY", score=10.0, active_pillars=[1], components={})
        assert neutral.direction == "NEUTRAL"
