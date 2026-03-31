"""Quality Filter — Option Trade Quality Gate.

Filters out low-quality option trades before execution by checking:
    1. Bid-ask spread (liquidity)
    2. Volume (activity)
    3. IV Rank (volatility environment)

Every potential trade must pass this filter. Rejects are logged with reasons
so we can track what we're skipping and why.
"""

from __future__ import annotations

from enum import Enum

import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import OptionQuote

logger = structlog.get_logger(__name__)


class FilterResult(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


class SetupGrade(str, Enum):
    """Setup quality grade — only A_PLUS and A are tradeable."""

    A_PLUS = "A+"    # All signals aligned, high confidence → TRADE
    A = "A"          # Strong setup, minor concern → TRADE
    B = "B"          # Decent but missing confirmation → SKIP
    C = "C"          # Weak setup → SKIP
    REJECT = "REJECT"  # Fails hard rules → NEVER TRADE


class SetupAssessment(BaseModel):
    """Full A+ quality assessment for a potential trade.

    From @SuperLuckeee's 4 Levers:
    - Lever 1 (Win Rate): Only take A+ setups
    - Lever 4 (Bad Trades): Skip anything below A grade

    Minimum 70% confidence required. NOT 60%.
    """

    grade: SetupGrade
    confidence: float = 0.0  # 0-1, must be >= 0.70
    flow_aligned: bool = False
    level_confirmed: bool = False
    bias_strong: bool = False
    ai_confidence_met: bool = False
    reasons: list[str] = []

    @property
    def tradeable(self) -> bool:
        """Only A+ and A setups are tradeable."""
        return self.grade in (SetupGrade.A_PLUS, SetupGrade.A)


class QualityCheck(BaseModel):
    """Result of running an option through the quality filter."""

    result: FilterResult
    quality_score: float = 0.0  # 0-100, higher is better
    reasons: list[str] = []
    spread_pct: float = 0.0
    volume: int = 0
    iv_rank: float = 0.0

    @property
    def passed(self) -> bool:
        return self.result == FilterResult.PASS


class QualityFilter:
    """Gate that ensures we only trade liquid, well-priced options.

    Checks:
    - Bid-ask spread: Wide spreads eat into profits. Reject if > 20% of mid.
    - Volume: Low volume = hard to fill at expected price. Tier-specific minimums.
    - IV Rank: Sweet spot depends on strategy:
        - Spreads (P2/P3): 30-70 IV rank (selling premium in reasonable vol)
        - Iron Condors (P1): > 50 IV rank (want elevated vol to sell)
    """

    def __init__(self):
        self._cfg = config().quality

    def check(
        self,
        option: OptionQuote,
        tier: str = "tier1",
        pillar: int = 1,
        iv_rank: float | None = None,
    ) -> QualityCheck:
        """Run all quality checks on an option contract.

        Args:
            option: The option quote to evaluate.
            tier: Which ticker tier ("tier1", "tier2", "tier3") for volume thresholds.
            pillar: Which pillar (1-4) for IV rank requirements.
            iv_rank: Current IV rank for the underlying (0-100).
                     If None, IV rank check is skipped.

        Returns:
            QualityCheck with PASS/REJECT and detailed scoring.
        """
        reasons: list[str] = []
        score = 100.0  # Start perfect, deduct for issues

        # ── Check 1: Bid-Ask Spread ──────────────────────────────
        spread_pct = self._check_spread(option)

        if spread_pct > self._cfg.max_spread_pct:
            reasons.append(
                f"WIDE_SPREAD: {spread_pct:.1%} spread "
                f"(max: {self._cfg.max_spread_pct:.0%})"
            )
            score -= 40  # Major penalty
        else:
            # Scale penalty: tighter spread = higher score
            spread_penalty = (spread_pct / self._cfg.max_spread_pct) * 20
            score -= spread_penalty

        # ── Check 2: Volume ──────────────────────────────────────
        min_vol = self._cfg.min_volume.get(tier, 100)
        volume = option.volume

        # Alpaca paper returns volume=0 for options.  When spread is
        # tight enough (<5%) that confirms real liquidity — skip the
        # volume gate and apply a small penalty instead of a full reject.
        volume_bypass = volume == 0 and spread_pct < 0.05

        if volume < min_vol and not volume_bypass:
            reasons.append(
                f"LOW_VOLUME: {volume} contracts "
                f"(min for {tier}: {min_vol})"
            )
            score -= 30
        elif volume_bypass:
            # Tight spread but no volume data — accept with mild penalty
            score -= 5
        else:
            # Bonus for high volume
            vol_ratio = min(volume / (min_vol * 5), 1.0)  # cap at 5x threshold
            score += vol_ratio * 10  # Up to +10 bonus

        # ── Check 3: IV Rank ─────────────────────────────────────
        effective_iv = iv_rank if iv_rank is not None else 0.0

        if iv_rank is not None:
            iv_ok = self._check_iv_rank(effective_iv, pillar)
            if not iv_ok:
                if pillar == 1:
                    reasons.append(
                        f"LOW_IV_RANK: {effective_iv:.0f} "
                        f"(iron condors need > {self._cfg.iv_rank['iron_condor_min']})"
                    )
                else:
                    reasons.append(
                        f"IV_RANK_OUT_OF_RANGE: {effective_iv:.0f} "
                        f"(spreads best in {self._cfg.iv_rank['spread_min']}-"
                        f"{self._cfg.iv_rank['spread_max']})"
                    )
                score -= 20

        # Clamp score
        score = max(0.0, min(100.0, score))

        result = FilterResult.REJECT if reasons else FilterResult.PASS

        check = QualityCheck(
            result=result,
            quality_score=round(score, 1),
            reasons=reasons,
            spread_pct=round(spread_pct, 4),
            volume=volume,
            iv_rank=effective_iv,
        )

        logger.info(
            "quality_check",
            symbol=option.symbol,
            result=result.value,
            score=check.quality_score,
            spread_pct=f"{spread_pct:.1%}",
            volume=volume,
            reasons=reasons or "all_clear",
        )

        return check

    def check_spread_pair(
        self,
        short_leg: OptionQuote,
        long_leg: OptionQuote,
        tier: str = "tier1",
        pillar: int = 1,
        iv_rank: float | None = None,
    ) -> QualityCheck:
        """Check quality for a spread (two-leg) position.

        Evaluates both legs and returns the worst-case quality.
        """
        short_check = self.check(short_leg, tier, pillar, iv_rank)
        long_check = self.check(long_leg, tier, pillar, iv_rank)

        # Use the worse of the two
        if not short_check.passed:
            return short_check
        if not long_check.passed:
            return long_check

        # Both passed — average the scores
        avg_score = (short_check.quality_score + long_check.quality_score) / 2
        return QualityCheck(
            result=FilterResult.PASS,
            quality_score=round(avg_score, 1),
            spread_pct=max(short_check.spread_pct, long_check.spread_pct),
            volume=min(short_check.volume, long_check.volume),
            iv_rank=short_check.iv_rank,
        )

    def _check_spread(self, option: OptionQuote) -> float:
        """Calculate bid-ask spread as percentage of mid price.

        Returns:
            Spread as a decimal (e.g., 0.15 for 15%).
        """
        if option.bid <= 0 or option.ask <= 0:
            return 1.0  # No valid quotes = max penalty

        mid = (option.bid + option.ask) / 2
        if mid <= 0:
            return 1.0

        spread = option.ask - option.bid
        return spread / mid

    # ── A+ Setup Quality Gate ────────────────────────────────────

    # Bias thresholds for "strong enough" — not marginal, truly committed
    _STRONG_BIAS = {
        1: (-15, 15),      # P1 IC: must be truly neutral (tighter than -20/+20)
        2: -65,            # P2 bear call: must be strongly bearish (not just barely -60)
        3: 65,             # P3 bull put: must be strongly bullish (not just barely +60)
        4: 45,             # P4 directional: high conviction (not just barely ±40)
    }

    def assess_setup(
        self,
        symbol: str,
        pillar: int,
        bias_score: float,
        flow_bias: float,
        at_key_level: bool,
        ai_confidence: float = 0.70,
    ) -> SetupAssessment:
        """A+ Setup Quality Gate — the #1 lever for increasing win rate.

        From @SuperLuckeee's 4 Levers cheatsheet:
        - "Take only A+ setups"
        - "Avoid early low-quality entries"
        - "Skip chop"

        ALL conditions must align for A+ grade:
        1. Bias must be STRONG for the pillar (not marginal)
        2. Flow must AGREE with trade direction
        3. Price must be at a KEY LEVEL (support/resistance)
        4. AI debate confidence must be >= 0.70 (70%, NOT 60%)

        Args:
            symbol: Ticker symbol.
            pillar: Which pillar (1-4).
            bias_score: Current bias score (-100 to +100).
            flow_bias: Flow bias score (-100 to +100).
            at_key_level: Whether price is near support/resistance.
            ai_confidence: Kage's verdict confidence (0-1).

        Returns:
            SetupAssessment with grade, confidence, and reasons.
        """
        reasons: list[str] = []
        checks_passed = 0
        total_checks = 4

        # ── Check 1: Bias is strong enough ────────────────────
        bias_strong = False
        if pillar == 1:
            low, high = self._STRONG_BIAS[1]
            bias_strong = low <= bias_score <= high
            if not bias_strong:
                reasons.append(f"BIAS_MARGINAL: {bias_score:.1f} outside neutral zone [{low}, {high}] for IC")
        elif pillar == 2:
            threshold = self._STRONG_BIAS[2]
            bias_strong = bias_score <= threshold
            if not bias_strong:
                reasons.append(f"BIAS_WEAK: {bias_score:.1f} > {threshold} for bear call (need stronger bearish)")
        elif pillar == 3:
            threshold = self._STRONG_BIAS[3]
            bias_strong = bias_score >= threshold
            if not bias_strong:
                reasons.append(f"BIAS_WEAK: {bias_score:.1f} < {threshold} for bull put (need stronger bullish)")
        elif pillar == 4:
            threshold = self._STRONG_BIAS[4]
            bias_strong = abs(bias_score) >= threshold
            if not bias_strong:
                reasons.append(f"BIAS_WEAK: |{bias_score:.1f}| < {threshold} for directional (need higher conviction)")

        if bias_strong:
            checks_passed += 1

        # ── Check 2: Flow alignment ───────────────────────────
        flow_aligned = False
        if pillar == 1:
            # IC doesn't need flow direction — neutral is fine
            flow_aligned = abs(flow_bias) < 50  # Not extreme in either direction
            if not flow_aligned:
                reasons.append(f"FLOW_EXTREME: flow_bias {flow_bias:.1f} too directional for IC")
        elif pillar == 2:
            flow_aligned = flow_bias < -10  # Flow should be bearish
            if not flow_aligned:
                reasons.append(f"FLOW_MISALIGNED: flow {flow_bias:.1f} not bearish for bear call")
        elif pillar == 3:
            flow_aligned = flow_bias > 10  # Flow should be bullish
            if not flow_aligned:
                reasons.append(f"FLOW_MISALIGNED: flow {flow_bias:.1f} not bullish for bull put")
        elif pillar == 4:
            # P4 direction depends on bias — flow must agree
            if bias_score > 0:
                flow_aligned = flow_bias > 0
            else:
                flow_aligned = flow_bias < 0
            if not flow_aligned:
                reasons.append(f"FLOW_MISALIGNED: bias={bias_score:.1f} but flow={flow_bias:.1f} disagree")

        if flow_aligned:
            checks_passed += 1

        # ── Check 3: Key level confirmation ───────────────────
        level_confirmed = at_key_level
        if level_confirmed:
            checks_passed += 1
        else:
            reasons.append("NO_LEVEL: price not at key support/resistance")

        # ── Check 4: AI confidence >= 70% ─────────────────────
        ai_confidence_met = ai_confidence >= 0.70
        if ai_confidence_met:
            checks_passed += 1
        else:
            reasons.append(f"LOW_AI_CONFIDENCE: {ai_confidence:.0%} < 70% minimum")

        # ── Calculate grade ───────────────────────────────────
        confidence = checks_passed / total_checks

        if checks_passed == 4:
            grade = SetupGrade.A_PLUS
        elif checks_passed == 3 and ai_confidence_met and bias_strong:
            grade = SetupGrade.A  # Missing one non-critical check
        elif checks_passed == 3:
            grade = SetupGrade.B
        elif checks_passed == 2:
            grade = SetupGrade.C
        else:
            grade = SetupGrade.REJECT

        # Hard rule: below 70% AI confidence is always REJECT
        if not ai_confidence_met:
            grade = max(grade, SetupGrade.B)  # Can't be A+ or A without AI confidence
            if grade in (SetupGrade.A_PLUS, SetupGrade.A):
                grade = SetupGrade.B

        assessment = SetupAssessment(
            grade=grade,
            confidence=round(confidence, 2),
            flow_aligned=flow_aligned,
            level_confirmed=level_confirmed,
            bias_strong=bias_strong,
            ai_confidence_met=ai_confidence_met,
            reasons=reasons,
        )

        logger.info(
            "setup_assessed",
            symbol=symbol,
            pillar=pillar,
            grade=grade.value,
            confidence=f"{confidence:.0%}",
            flow_aligned=flow_aligned,
            level_confirmed=level_confirmed,
            bias_strong=bias_strong,
            ai_confidence=f"{ai_confidence:.0%}",
            tradeable=assessment.tradeable,
        )

        return assessment

    def _check_iv_rank(self, iv_rank: float, pillar: int) -> bool:
        """Check if IV rank is in the acceptable range for this pillar.

        Iron Condors (P1): Want elevated IV (> 50) to sell premium.
        Spreads (P2/P3): Want moderate IV (30-70) — not too cheap, not too wild.
        Directional (P4): No IV rank requirement — we're buying, not selling.
        """
        if pillar == 4:
            return True  # No IV constraint for directional

        if pillar == 1:
            return iv_rank >= self._cfg.iv_rank["iron_condor_min"]

        # P2, P3: spreads
        return (
            self._cfg.iv_rank["spread_min"]
            <= iv_rank
            <= self._cfg.iv_rank["spread_max"]
        )
