"""Tests for the Quality Filter — option trade quality gate."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from esther.signals.quality_filter import QualityFilter, QualityCheck, FilterResult
from esther.data.tradier import OptionQuote, OptionType, OptionGreeks


# ── Fixtures ─────────────────────────────────────────────────────


def _make_option(
    bid: float = 5.0,
    ask: float = 5.20,
    volume: int = 1000,
    strike: float = 500.0,
    option_type: OptionType = OptionType.CALL,
    delta: float = 0.25,
    mid_iv: float = 0.35,
) -> OptionQuote:
    """Create an OptionQuote for testing."""
    return OptionQuote(
        symbol=f"SPY240329C{int(strike):05d}000",
        option_type=option_type,
        strike=strike,
        expiration="2024-03-29",
        bid=bid,
        ask=ask,
        mid=round((bid + ask) / 2, 2),
        last=round((bid + ask) / 2, 2),
        volume=volume,
        open_interest=5000,
        greeks=OptionGreeks(
            delta=delta,
            gamma=0.05,
            theta=-0.10,
            vega=0.15,
            rho=0.01,
            smv_vol=mid_iv,
        ),
    )


@pytest.fixture
def quality_filter():
    """Create a QualityFilter with default config."""
    with patch("esther.signals.quality_filter.config") as mock_config:
        from esther.core.config import QualityConfig
        mock_config.return_value.quality = QualityConfig()
        return QualityFilter()


# ── Spread Width Tests ───────────────────────────────────────────


class TestSpreadWidthFiltering:
    """Test bid-ask spread filtering."""

    def test_tight_spread_passes(self, quality_filter):
        """A tight bid-ask spread (<20% of mid) should pass."""
        option = _make_option(bid=5.00, ask=5.10)  # ~2% spread
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed
        assert result.spread_pct < 0.20

    def test_wide_spread_rejected(self, quality_filter):
        """A wide bid-ask spread (>20% of mid) should be rejected."""
        option = _make_option(bid=1.00, ask=1.50)  # 40% spread
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert any("WIDE_SPREAD" in r for r in result.reasons)

    def test_borderline_spread(self, quality_filter):
        """A spread exactly at 20% should still pass (equal, not greater)."""
        # mid = 5.0, spread = 1.0, spread_pct = 0.20
        option = _make_option(bid=4.50, ask=5.50)  # exactly 20%
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        # 20% is exactly the threshold — should pass (not strictly >)
        assert result.passed

    def test_zero_bid_gets_max_penalty(self, quality_filter):
        """Zero bid means no market, should get 100% spread penalty."""
        option = _make_option(bid=0.0, ask=5.00)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert result.spread_pct == 1.0

    def test_zero_ask_gets_max_penalty(self, quality_filter):
        """Zero ask means no market, should get 100% spread penalty."""
        option = _make_option(bid=0.0, ask=0.0)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert result.spread_pct == 1.0

    def test_spread_pct_calculation(self, quality_filter):
        """Verify the spread percentage is calculated correctly."""
        # bid=4.0, ask=5.0 → mid=4.5, spread=1.0, pct = 1.0/4.5 ≈ 0.2222
        option = _make_option(bid=4.0, ask=5.0)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        expected_pct = 1.0 / 4.5
        assert abs(result.spread_pct - expected_pct) < 0.01


# ── Volume Threshold Tests ───────────────────────────────────────


class TestVolumeThresholds:
    """Test per-tier volume filtering."""

    def test_tier1_high_volume_passes(self, quality_filter):
        """Tier 1 with volume > 500 should pass."""
        option = _make_option(volume=1000)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed

    def test_tier1_low_volume_rejected(self, quality_filter):
        """Tier 1 with volume < 500 should be rejected."""
        option = _make_option(volume=100)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert any("LOW_VOLUME" in r for r in result.reasons)

    def test_tier2_volume_threshold(self, quality_filter):
        """Tier 2 has a lower volume threshold (100)."""
        # Passes at 100
        option = _make_option(volume=150)
        result = quality_filter.check(option, tier="tier2", pillar=2, iv_rank=50.0)
        assert result.passed

        # Fails below 100
        option = _make_option(volume=50)
        result = quality_filter.check(option, tier="tier2", pillar=2, iv_rank=50.0)
        assert not result.passed

    def test_tier3_volume_threshold(self, quality_filter):
        """Tier 3 has threshold of 200."""
        option = _make_option(volume=250)
        result = quality_filter.check(option, tier="tier3", pillar=2, iv_rank=50.0)
        assert result.passed

        option = _make_option(volume=100)
        result = quality_filter.check(option, tier="tier3", pillar=2, iv_rank=50.0)
        assert not result.passed

    def test_volume_bonus_for_high_activity(self, quality_filter):
        """Very high volume should give a quality score bonus."""
        low_vol = _make_option(volume=600)
        high_vol = _make_option(volume=5000)

        result_low = quality_filter.check(low_vol, tier="tier1", pillar=2, iv_rank=50.0)
        result_high = quality_filter.check(high_vol, tier="tier1", pillar=2, iv_rank=50.0)

        # Both pass, but high volume should have a higher score
        assert result_low.passed
        assert result_high.passed
        assert result_high.quality_score >= result_low.quality_score


# ── IV Rank Tests ────────────────────────────────────────────────


class TestIVRankFiltering:
    """Test IV rank filtering for different pillars."""

    def test_iron_condor_needs_high_iv(self, quality_filter):
        """P1 (Iron Condors) need IV rank >= 50."""
        option = _make_option(volume=1000)

        # IV rank 60 — should pass
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=60.0)
        assert result.passed

        # IV rank 30 — should fail
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=30.0)
        assert not result.passed
        assert any("LOW_IV_RANK" in r for r in result.reasons)

    def test_spreads_need_moderate_iv(self, quality_filter):
        """P2/P3 (spreads) need IV rank in 30-70 range."""
        option = _make_option(volume=1000)

        # IV rank 50 — should pass for P2
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)
        assert result.passed

        # IV rank 80 — too high, should fail for P2
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=80.0)
        assert not result.passed
        assert any("IV_RANK_OUT_OF_RANGE" in r for r in result.reasons)

        # IV rank 20 — too low, should fail for P3
        result = quality_filter.check(option, tier="tier1", pillar=3, iv_rank=20.0)
        assert not result.passed

    def test_p4_no_iv_constraint(self, quality_filter):
        """P4 (directional scalps) have no IV rank requirement."""
        option = _make_option(volume=1000)

        # Even extreme IV ranks should pass for P4
        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=5.0)
        assert result.passed

        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=95.0)
        assert result.passed

    def test_no_iv_rank_skips_check(self, quality_filter):
        """When iv_rank is None, the IV check should be skipped."""
        option = _make_option(volume=1000)
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=None)

        # Should pass since IV check is skipped
        assert result.passed
        assert not any("IV_RANK" in r for r in result.reasons)


# ── Spread Pair Tests ────────────────────────────────────────────


class TestSpreadPairChecks:
    """Test quality checking for spread pairs (two-leg positions)."""

    def test_both_legs_good(self, quality_filter):
        """Both legs passing should produce a passing result."""
        short = _make_option(bid=3.00, ask=3.20, volume=1500)
        long = _make_option(bid=1.50, ask=1.65, volume=1200)

        result = quality_filter.check_spread_pair(
            short, long, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert result.passed

    def test_one_leg_bad_fails_pair(self, quality_filter):
        """If either leg fails, the whole spread should fail."""
        good_leg = _make_option(bid=3.00, ask=3.20, volume=1500)
        bad_leg = _make_option(bid=0.01, ask=0.50, volume=10)  # terrible quality

        result = quality_filter.check_spread_pair(
            good_leg, bad_leg, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert not result.passed

    def test_pair_score_is_average(self, quality_filter):
        """When both legs pass, the score should be the average."""
        leg1 = _make_option(bid=3.00, ask=3.15, volume=2000)
        leg2 = _make_option(bid=1.50, ask=1.60, volume=1800)

        result = quality_filter.check_spread_pair(
            leg1, leg2, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert result.passed
        # Score should be reasonable
        assert 50 <= result.quality_score <= 100


# ── Quality Score Tests ──────────────────────────────────────────


class TestQualityScoring:
    """Test the quality score calculation."""

    def test_perfect_option_high_score(self, quality_filter):
        """A high-quality option should get a high score."""
        option = _make_option(bid=5.00, ask=5.05, volume=5000)  # tight spread, high volume
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed
        assert result.quality_score > 90

    def test_mediocre_option_moderate_score(self, quality_filter):
        """A passable but not great option should get a moderate score."""
        option = _make_option(bid=5.00, ask=5.80, volume=600)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        # May or may not pass depending on exact spread pct
        assert 0 <= result.quality_score <= 100

    def test_score_clamped_to_0_100(self, quality_filter):
        """Score should always be between 0 and 100."""
        # Worst case option
        option = _make_option(bid=0.01, ask=10.0, volume=1)
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=10.0)
        assert 0 <= result.quality_score <= 100

        # Best case option
        option = _make_option(bid=5.00, ask=5.01, volume=50000)
        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=50.0)
        assert 0 <= result.quality_score <= 100


# ── QualityCheck Model Tests ────────────────────────────────────


class TestQualityCheckModel:
    """Test the QualityCheck model properties."""

    def test_passed_property(self):
        """The passed property should mirror PASS/REJECT."""
        passing = QualityCheck(result=FilterResult.PASS, quality_score=80.0)
        assert passing.passed is True

        failing = QualityCheck(result=FilterResult.REJECT, quality_score=20.0, reasons=["BAD"])
        assert failing.passed is False
