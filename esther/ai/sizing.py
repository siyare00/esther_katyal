"""AI Position Sizing — Kelly-Based Sizing with AI Adjustment and Capital Recycling.

Determines how many contracts to trade based on:
    - Kelly criterion as the mathematical baseline
    - AI (Claude) adjustment based on qualitative factors
    - Capital Recycler: compound winners, shrink losers

The Capital Recycler is the secret sauce:
    - After each win: increase size by 15% (compounding)
    - After each loss: decrease size by 20% (capital preservation)
    - This creates a natural cycle: winning streaks compound, losing streaks shrink
"""

from __future__ import annotations

from typing import Any

import os

import anthropic
import openai
import structlog
from pydantic import BaseModel

from esther.core.config import config, env

logger = structlog.get_logger(__name__)


class SizingInput(BaseModel):
    """Input data for position sizing."""

    symbol: str
    account_balance: float
    max_risk_per_trade: float  # dollar amount
    confidence: int  # 0-100 from debate
    recent_wins: int = 0
    recent_losses: int = 0
    current_streak: int = 0  # positive = wins, negative = losses
    vix_level: float = 20.0
    pillar: int = 1
    credit_or_debit: float = 0.0  # per contract
    max_loss_per_contract: float = 0.0  # max possible loss per contract
    daily_pnl: float = 0.0  # current daily P&L (negative = losses)
    daily_loss_cap: float = 0.0  # max allowed daily loss (positive number)


class SizingResult(BaseModel):
    """Position sizing output."""

    contracts: int
    max_risk: float  # total max risk for this position
    kelly_raw: float  # raw Kelly fraction
    kelly_adjusted: float  # after AI/recycler adjustments
    recycler_multiplier: float  # current recycler effect
    reasoning: str


class AISizer:
    """AI-enhanced position sizer with Kelly criterion and capital recycling.

    Sizing Pipeline:
    1. Calculate raw Kelly fraction from win rate and average win/loss
    2. Apply conservative fractional Kelly (default 25% of full Kelly)
    3. Capital Recycler adjusts based on recent streak
    4. AI reviews and can adjust ±30% based on qualitative assessment
    5. Final clamping to min/max contracts
    """

    def __init__(self):
        self._cfg = config().sizing
        self._ai_cfg = config().ai
        self._env = env()
        self._backend = self._ai_cfg.ai_backend.lower()
        if self._backend == "ollama":
            self._client = openai.AsyncOpenAI(
                base_url=self._ai_cfg.ollama_base_url,
                api_key="ollama",
            )
        elif self._backend == "groq":
            self._client = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self._ai_cfg.groq_api_key or os.environ.get("GROQ_API_KEY", ""),
            )
        else:
            self._client = anthropic.AsyncAnthropic(api_key=self._env.anthropic_api_key)

    async def _chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Unified chat method that handles both Ollama (OpenAI) and Anthropic backends."""
        _max_tokens = max_tokens or self._ai_cfg.max_tokens
        _temperature = temperature if temperature is not None else self._ai_cfg.temperature
        if self._backend == "ollama":
            _model = self._ai_cfg.ollama_model
        elif self._backend == "groq":
            _model = self._ai_cfg.groq_model
        else:
            _model = self._ai_cfg.model

        if self._backend == "ollama":
            response = await self._client.chat.completions.create(
                model=_model,
                max_tokens=_max_tokens,
                temperature=_temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content
        else:
            response = await self._client.messages.create(
                model=_model,
                max_tokens=_max_tokens,
                temperature=_temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text

    async def calculate_size(self, input_data: SizingInput) -> SizingResult:
        """Calculate optimal position size.

        Args:
            input_data: All inputs needed for sizing.

        Returns:
            SizingResult with recommended contracts and reasoning.
        """
        # Step 1: Kelly criterion baseline
        kelly_raw = self._kelly_criterion(input_data)

        # Step 2: Fractional Kelly (conservative)
        kelly_fraction = kelly_raw * self._cfg.kelly_fraction

        # Step 3: Capital Recycler adjustment
        recycler_mult = self._capital_recycler(input_data.current_streak)
        kelly_adjusted = kelly_fraction * recycler_mult

        # Step 4: Confidence scaling
        # Low confidence → smaller size, high confidence → closer to full size
        confidence_scale = input_data.confidence / 100.0
        kelly_adjusted *= confidence_scale

        # Step 5: Convert to contracts
        if input_data.max_loss_per_contract > 0:
            max_risk_amount = input_data.account_balance * kelly_adjusted
            contracts_from_kelly = int(max_risk_amount / input_data.max_loss_per_contract)
        else:
            contracts_from_kelly = self._cfg.min_contracts

        # Step 6: AI review (optional, can adjust ±30%)
        ai_reasoning = ""
        ai_adjustment = 1.0
        try:
            ai_result = await self._ai_review(input_data, contracts_from_kelly)
            ai_adjustment = ai_result.get("adjustment", 1.0)
            ai_reasoning = ai_result.get("reasoning", "")
        except Exception as e:
            logger.warning("ai_sizing_review_failed", error=str(e))
            ai_reasoning = "AI review unavailable, using Kelly + recycler only"

        # Apply AI adjustment (clamped to ±30%)
        ai_adjustment = max(0.7, min(1.3, ai_adjustment))
        final_contracts = int(contracts_from_kelly * ai_adjustment)

        # Clamp to configured min/max
        final_contracts = max(self._cfg.min_contracts, min(self._cfg.max_contracts, final_contracts))

        # Clamp to daily loss cap budget — don't size into a guaranteed rejection
        if input_data.daily_loss_cap > 0 and input_data.max_loss_per_contract > 0:
            remaining_budget = input_data.daily_loss_cap + input_data.daily_pnl  # pnl is negative when losing
            if remaining_budget > 0:
                max_affordable = int(remaining_budget / input_data.max_loss_per_contract)
                max_affordable = max(max_affordable, self._cfg.min_contracts)
                if max_affordable < final_contracts:
                    logger.info(
                        "size_clamped_to_daily_cap",
                        symbol=input_data.symbol,
                        original=final_contracts,
                        clamped=max_affordable,
                        remaining_budget=round(remaining_budget, 2),
                    )
                    final_contracts = max_affordable

        # Calculate total max risk
        max_risk = final_contracts * input_data.max_loss_per_contract

        result = SizingResult(
            contracts=final_contracts,
            max_risk=round(max_risk, 2),
            kelly_raw=round(kelly_raw, 4),
            kelly_adjusted=round(kelly_adjusted, 4),
            recycler_multiplier=round(recycler_mult, 4),
            reasoning=ai_reasoning or f"Kelly({kelly_raw:.2%}) × Fraction({self._cfg.kelly_fraction}) × Recycler({recycler_mult:.2f}) × Confidence({confidence_scale:.0%})",
        )

        logger.info(
            "position_sized",
            symbol=input_data.symbol,
            contracts=result.contracts,
            max_risk=result.max_risk,
            kelly_raw=result.kelly_raw,
            recycler=result.recycler_multiplier,
        )

        return result

    def _kelly_criterion(self, input_data: SizingInput) -> float:
        """Calculate raw Kelly criterion fraction.

        Kelly % = W - [(1 - W) / R]
        Where:
            W = win probability
            R = win/loss ratio (average win / average loss)

        If we don't have enough data, use reasonable defaults.
        """
        total_trades = input_data.recent_wins + input_data.recent_losses

        if total_trades < 5:
            # Not enough data — use conservative default
            return 0.02  # 2% of account

        win_rate = input_data.recent_wins / total_trades

        # Estimate win/loss ratio based on pillar
        # P1-P3 (credit spreads): small wins, larger losses → R ≈ 0.5-1.0
        # P4 (directional): variable → R ≈ 1.5-2.0
        if input_data.pillar == 4:
            win_loss_ratio = 1.5
        else:
            win_loss_ratio = 0.8  # typical for credit spreads

        kelly = win_rate - ((1 - win_rate) / win_loss_ratio)

        # Kelly can be negative (don't trade!) — clamp to 0
        return max(0.0, min(0.25, kelly))  # Cap at 25%

    def _capital_recycler(self, current_streak: int) -> float:
        """Apply the Capital Recycler multiplier based on win/loss streak.

        Winning streak → compound (increase size)
        Losing streak → protect (decrease size)

        The math:
        - 3 wins in a row: 1.15^3 = 1.52x size (52% larger)
        - 3 losses in a row: 1/(1.25^3) = 0.51x size (49% smaller)

        This naturally captures momentum while preventing blowups.
        """
        if current_streak > 0:
            # Winning streak — compound
            multiplier = self._cfg.win_streak_multiplier ** current_streak
            # Cap at 2x to prevent over-leveraging
            return min(2.0, multiplier)
        elif current_streak < 0:
            # Losing streak — protect
            losses = abs(current_streak)
            multiplier = 1.0 / (self._cfg.loss_streak_divisor ** losses)
            # Floor at 0.25x — always trade at least something
            return max(0.25, multiplier)
        else:
            return 1.0  # No streak

    async def _ai_review(
        self, input_data: SizingInput, kelly_contracts: int
    ) -> dict[str, Any]:
        """Have Claude review and adjust the Kelly-based sizing.

        The AI considers qualitative factors that pure math can't capture:
        - Is the market environment unusual?
        - Are there event risks (earnings, FOMC)?
        - Does the confidence level warrant more/less?
        """
        prompt = f"""Review this position sizing recommendation:

SYMBOL: {input_data.symbol}
PILLAR: P{input_data.pillar}
ACCOUNT BALANCE: ${input_data.account_balance:,.2f}
DEBATE CONFIDENCE: {input_data.confidence}/100
VIX: {input_data.vix_level:.1f}
CURRENT STREAK: {input_data.current_streak} ({'wins' if input_data.current_streak > 0 else 'losses' if input_data.current_streak < 0 else 'neutral'})
KELLY RECOMMENDS: {kelly_contracts} contracts
MAX LOSS PER CONTRACT: ${input_data.max_loss_per_contract:.2f}
TOTAL RISK: ${kelly_contracts * input_data.max_loss_per_contract:,.2f}
RISK AS % OF ACCOUNT: {(kelly_contracts * input_data.max_loss_per_contract / input_data.account_balance * 100) if input_data.account_balance > 0 else 0:.1f}%

Should I adjust the size? Consider:
1. Is the risk appropriate for the account size?
2. Does the VIX level suggest more or less caution?
3. Is the streak length concerning?
4. Any reason to deviate from Kelly?

Respond with EXACTLY two lines:
ADJUSTMENT: [0.7 to 1.3 multiplier]
REASONING: [one sentence explanation]"""

        system = """You are a risk-aware position sizing advisor. 
Your job is to review a Kelly criterion recommendation and suggest adjustments.
Be conservative — protecting capital is more important than maximizing returns.
High VIX (>25) = reduce size. Low confidence (<50) = reduce size. Long losing streak = reduce size.
Only increase size when everything aligns: high confidence, moderate VIX, winning streak."""

        text = await self._chat(
            system_prompt=system,
            user_prompt=prompt,
            max_tokens=256,
            temperature=0.2,
        )
        result: dict[str, Any] = {"adjustment": 1.0, "reasoning": ""}

        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("ADJUSTMENT:"):
                try:
                    result["adjustment"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()

        return result
