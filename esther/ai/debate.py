"""AI Debate System — Four-Way Claude Debate for Trade Decisions.

Four AI personalities argue every trade before execution:

    Kimi 🔬 — The research analyst and risk quantifier. Provides cold,
              data-driven analysis before the debate, then acts as devil's
              advocate and co-judge after hearing both sides.

    Riki 🐂 — The eternal bull. Always finds reasons to go long.
              Optimistic, momentum-focused, sees opportunity everywhere.

    Abi 🐻  — The permanent bear. Always finds reasons to go short.
              Skeptical, risk-focused, sees danger everywhere.

    Kage ⚖️ — The final judge. Weighs all arguments objectively and makes
              the final call. Cold, analytical, no emotional bias.

5-Step Debate Flow (debate_with_kimi):
    1. Kimi researches → quantified risk/reward analysis
    2. Riki argues bull case (with Kimi's research)
    3. Abi argues bear case (with Kimi's research)
    4. Kimi challenges both sides → renders independent verdict
    5. Kage judges everything → renders final verdict
    → Consensus rule: Kage + Kimi must agree or trade is blocked

Legacy 3-Step Flow (debate):
    1. Riki argues bull case
    2. Abi argues bear case
    3. Kage judges
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


# ── System Prompts ───────────────────────────────────────────────

RIKI_SYSTEM_PROMPT = """You are Riki, the bull. Your job is to make the strongest possible case for going LONG on this trade.

Your personality:
- Eternally optimistic but not stupid — you back your arguments with data
- Momentum-focused: trends continue more often than they reverse
- You see opportunity where others see risk
- You find bullish signals in the technicals, fundamentals, and sentiment

Your task:
Given the market data, make the bull case. Include:
1. Technical reasons to be bullish (support levels, momentum, patterns)
2. How the current volatility environment favors bulls
3. Risk/reward assessment from the bullish perspective
4. Specific price targets and timeframes
5. What would need to happen for the bull case to fail

Be specific. Use the actual numbers provided. Don't be vague.
Respond in a structured format with clear sections."""

ABI_SYSTEM_PROMPT = """You are Abi, the bear. Your job is to make the strongest possible case for going SHORT on this trade.

Your personality:
- Perpetually skeptical but analytical — you argue with data, not fear
- Mean-reversion focused: what goes up must come down
- You see risk where others see opportunity
- You find bearish signals in the technicals, fundamentals, and sentiment

Your task:
Given the market data, make the bear case. Include:
1. Technical reasons to be bearish (resistance levels, divergences, patterns)
2. How the current volatility environment favors bears
3. Risk/reward assessment from the bearish perspective
4. Specific downside targets and timeframes
5. What would need to happen for the bear case to fail

Be specific. Use the actual numbers provided. Don't be vague.
Respond in a structured format with clear sections."""

KAGE_SYSTEM_PROMPT = """You are Kage, the judge. You've just heard the bull case (Riki) and the bear case (Abi) for a trade.

Your personality:
- Cold, analytical, zero emotional bias
- You weigh evidence, not rhetoric
- You're comfortable saying "no trade" if neither case is compelling
- You care about risk-adjusted returns, not being right

Your task:
Evaluate both arguments and deliver a verdict. Your response MUST include:

1. VERDICT: Exactly one of: BULL, BEAR, or NEUTRAL
2. CONFIDENCE: A number from 0-100 (how confident you are in the verdict)
3. REASONING: 2-3 sentences explaining your decision
4. KEY_FACTOR: The single most important factor that swayed your decision

Format your response EXACTLY like this:
VERDICT: [BULL/BEAR/NEUTRAL]
CONFIDENCE: [0-100]
REASONING: [Your reasoning here]
KEY_FACTOR: [Single most important factor]

Rules:
- NEUTRAL means "don't trade" — use it when both cases are roughly equal
- Confidence below 40 should almost always be NEUTRAL
- Consider the bias score from the technical system as additional context
- Weight recent price action heavily — the market is always right in the short term"""

KAGE_FULL_SYSTEM_PROMPT = """You are Kage, the final judge. You have seen EVERYTHING:
- Kimi's quantified research analysis
- Riki's bull case
- Abi's bear case
- Kimi's devil's advocate challenges and independent verdict

Your personality:
- Cold, analytical, zero emotional bias
- You weigh evidence, not rhetoric
- You're comfortable saying "no trade" if neither case is compelling
- You care about risk-adjusted returns, not being right

Your task:
Evaluate ALL arguments and Kimi's concerns, then deliver your final verdict.

CRITICAL: You MUST address Kimi's key concern directly. If you disagree with Kimi's verdict, you must explain why with data.

Your response MUST include:
1. VERDICT: Exactly one of: APPROVE, REDUCE, REJECT, or INVERT
   - APPROVE: Take the trade as proposed
   - REDUCE: Take the trade at 50% size (decent setup but elevated risk)
   - REJECT: Don't take this trade (risk too high or signal unclear)
   - INVERT: Flip the trade direction (strong signal the proposed direction is wrong)
2. CONFIDENCE: A number from 0-100
3. REASONING: 2-3 sentences explaining your decision
4. KEY_FACTOR: The single most important factor that swayed your decision
5. KIMI_RESPONSE: How you address Kimi's key concern

Format your response EXACTLY like this:
VERDICT: [APPROVE/REDUCE/REJECT/INVERT]
CONFIDENCE: [0-100]
REASONING: [Your reasoning]
KEY_FACTOR: [Single most important factor]
KIMI_RESPONSE: [Your response to Kimi's concern]"""

KIMI_RESEARCH_PROMPT = """You are Kimi, the research analyst and risk quantifier. Your job is to provide cold, quantified analysis BEFORE the debate begins. You are not bullish or bearish — you are a data machine.

Analyze and provide:
1. WIN PROBABILITY: Based on delta, historical patterns at this VIX/RSI/price level, estimate the % chance this trade profits
2. RISK/REWARD RATIO: Quantify max loss vs expected gain. Is it 1:1? 1:3? 3:1?
3. KEY CORRELATIONS: What other signals confirm or contradict? (VIX direction, flow direction, key level proximity, regime state)
4. LIQUIDITY FLAGS: Is the option liquid enough? Bid-ask spread issues? Volume concerns?
5. HISTORICAL CONTEXT: Last 3 times these conditions occurred on this ticker, what happened?
6. MAX LOSS SCENARIO: What's the worst case and how likely is it?

Be specific with numbers. No opinions — just data."""

KIMI_ADVOCATE_PROMPT = """You are Kimi, now acting as devil's advocate and co-judge. You have seen:
- Your own research
- Riki's bull argument
- Abi's bear argument

Your job now:
1. CHALLENGE RIKI: What data did the bull ignore? What risks are they downplaying?
2. CHALLENGE ABI: What opportunities is the bear missing? Are they being too cautious?
3. INDEPENDENT VERDICT: Based on everything, render your verdict:
   - APPROVE: Take the trade as proposed
   - REDUCE: Take the trade at 50% size (decent setup but some risk)
   - REJECT: Don't take this trade (risk too high or signal unclear)
   - INVERT: Flip the trade direction (strong signal the proposed direction is wrong)
4. CONFIDENCE: 0-100 how confident are you in your verdict?
5. KEY CONCERN: What is the single biggest risk the final judge MUST address?

Format the verdict section EXACTLY like this:
VERDICT: [APPROVE/REDUCE/REJECT/INVERT]
CONFIDENCE: [0-100]
KEY_CONCERN: [Single biggest risk]"""


class DebateInput(BaseModel):
    """Input data for the AI debate."""

    symbol: str
    current_price: float
    bias_score: float  # From BiasEngine
    vix_level: float
    rsi: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    daily_change_pct: float = 0.0
    volume: int = 0
    support_level: float | None = None
    resistance_level: float | None = None
    news_context: str = ""
    flow_bias: float = 0.0              # -100 (bearish flow) to +100 (bullish flow)
    flow_summary: str = ""              # Human-readable flow summary for debate context
    sage_intel: dict | None = None      # Sage's market intelligence package
    journal_lessons: str = ""           # Trade journal lessons for in-session learning

    # Optional context fields (populated by engine, used by debate context builder)
    pillar: int | None = None           # Which pillar strategy is being debated
    direction: str = ""                 # BULL, BEAR, or NEUTRAL
    tier: str = ""                      # tier1, tier2, tier3
    option_quality_score: float | None = None  # Quality filter score
    iv_rank: float | None = None        # Implied volatility rank (0-100)


class DebateVerdict(BaseModel):
    """Output of the debate system.

    Supports both the legacy 3-step flow and the full 5-step Kimi flow.
    Kimi fields are optional for backward compatibility with the legacy flow.
    """

    symbol: str
    verdict: str  # BULL, BEAR, NEUTRAL (legacy) or APPROVE, REDUCE, REJECT, INVERT
    confidence: int  # 0-100
    reasoning: str
    key_factor: str
    riki_argument: str  # Bull case
    abi_argument: str  # Bear case
    kage_analysis: str  # Judge's full response

    # Kimi fields (populated by debate_with_kimi, empty in legacy debate)
    kimi_research: str = ""
    kimi_challenge: str = ""
    kimi_verdict: str = ""
    kimi_confidence: int = 0
    consensus: bool = False
    consensus_action: str = ""
    size_modifier: float = 1.0


class AIDebate:
    """Four-way AI debate system for trade decisions.

    Two flows available:

    debate_with_kimi() — Full 5-step flow (DEFAULT):
        1. Kimi researches (quantified risk analysis)
        2. Riki argues bull case (with Kimi's data)
        3. Abi argues bear case (with Kimi's data)
        4. Kimi challenges both sides, renders independent verdict
        5. Kage sees everything, renders final verdict
        → Consensus rule enforced: Kage + Kimi must agree

    debate() — Legacy 3-step flow (fallback for speed/rate limits):
        1. Riki argues bull case
        2. Abi argues bear case
        3. Kage judges
    """

    def __init__(self):
        self._cfg = config().ai
        self._env = env()
        self._backend = self._cfg.ai_backend.lower()
        if self._backend == "ollama":
            self._client = openai.AsyncOpenAI(
                base_url=self._cfg.ollama_base_url,
                api_key="ollama",
            )
        elif self._backend == "groq":
            self._client = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self._cfg.groq_api_key or os.environ.get("GROQ_API_KEY", ""),
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
        _max_tokens = max_tokens or self._cfg.max_tokens
        _temperature = temperature if temperature is not None else self._cfg.temperature
        if self._backend == "ollama":
            _model = self._cfg.ollama_model
        elif self._backend == "groq":
            _model = self._cfg.groq_model
        else:
            _model = self._cfg.model

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

    async def debate(self, input_data: DebateInput) -> DebateVerdict:
        """Run the legacy three-way debate (Riki → Abi → Kage).

        Use this as a fallback when speed matters or API rate limits
        require fewer calls. For full analysis, use debate_with_kimi().

        Args:
            input_data: Market data and context for the debate.

        Returns:
            DebateVerdict with the final decision.
        """
        market_context = self._build_market_context(input_data)

        logger.info("debate_started", symbol=input_data.symbol, bias=input_data.bias_score, mode="legacy")

        # Phase 1 & 2: Get bull and bear cases
        riki_response = await self._get_argument(
            system_prompt=RIKI_SYSTEM_PROMPT,
            market_context=market_context,
            role_name="Riki (Bull)",
        )

        abi_response = await self._get_argument(
            system_prompt=ABI_SYSTEM_PROMPT,
            market_context=market_context,
            role_name="Abi (Bear)",
        )

        # Phase 3: Kage judges
        verdict = await self._get_verdict(
            market_context=market_context,
            bull_case=riki_response,
            bear_case=abi_response,
            bias_score=input_data.bias_score,
        )

        # Parse Kage's structured response
        parsed = self._parse_verdict(verdict)

        result = DebateVerdict(
            symbol=input_data.symbol,
            verdict=parsed.get("verdict", "NEUTRAL"),
            confidence=parsed.get("confidence", 0),
            reasoning=parsed.get("reasoning", ""),
            key_factor=parsed.get("key_factor", ""),
            riki_argument=riki_response,
            abi_argument=abi_response,
            kage_analysis=verdict,
        )

        logger.info(
            "debate_complete",
            symbol=input_data.symbol,
            verdict=result.verdict,
            confidence=result.confidence,
            key_factor=result.key_factor,
            mode="legacy",
        )

        return result

    async def debate_with_kimi(self, input_data: DebateInput) -> DebateVerdict:
        """Run the full 5-step debate with Kimi (DEFAULT).

        Flow:
            1. Kimi researches — quantified risk/reward analysis
            2. Riki argues bull case (with Kimi's research)
            3. Abi argues bear case (with Kimi's research)
            4. Kimi challenges both sides → independent verdict
            5. Kage final judge → must address Kimi's concerns
            → Consensus rule: Kage + Kimi must agree or trade blocked

        Args:
            input_data: Market data and context for the debate.

        Returns:
            DebateVerdict with consensus decision and all analyses.
        """
        market_context = self._build_market_context(input_data)

        logger.info("debate_started", symbol=input_data.symbol, bias=input_data.bias_score, mode="kimi")

        # ── Step 1: Kimi Research ────────────────────────────────
        kimi_research = await self._get_argument(
            system_prompt=KIMI_RESEARCH_PROMPT,
            market_context=market_context,
            role_name="Kimi (Research)",
        )
        logger.info("kimi_research_complete", symbol=input_data.symbol)

        # Build enriched context with Kimi's research for Riki and Abi
        enriched_context = (
            f"{market_context}\n\n"
            f"--- KIMI'S RESEARCH ANALYSIS ---\n{kimi_research}"
        )

        # ── Step 2: Riki argues bull case ────────────────────────
        riki_response = await self._get_argument(
            system_prompt=RIKI_SYSTEM_PROMPT,
            market_context=enriched_context,
            role_name="Riki (Bull)",
        )

        # ── Step 3: Abi argues bear case ─────────────────────────
        abi_response = await self._get_argument(
            system_prompt=ABI_SYSTEM_PROMPT,
            market_context=enriched_context,
            role_name="Abi (Bear)",
        )

        # ── Step 4: Kimi Devil's Advocate ────────────────────────
        kimi_challenge = await self._get_kimi_challenge(
            market_context=market_context,
            kimi_research=kimi_research,
            bull_case=riki_response,
            bear_case=abi_response,
        )
        kimi_parsed = self._parse_kimi_verdict(kimi_challenge)
        logger.info(
            "kimi_challenge_complete",
            symbol=input_data.symbol,
            kimi_verdict=kimi_parsed.get("verdict", "REJECT"),
            kimi_confidence=kimi_parsed.get("confidence", 0),
        )

        # ── Step 5: Kage Final Judge ─────────────────────────────
        kage_response = await self._get_full_verdict(
            market_context=market_context,
            kimi_research=kimi_research,
            bull_case=riki_response,
            bear_case=abi_response,
            kimi_challenge=kimi_challenge,
            bias_score=input_data.bias_score,
        )
        kage_parsed = self._parse_kimi_verdict(kage_response)  # Same format
        logger.info(
            "kage_verdict_complete",
            symbol=input_data.symbol,
            kage_verdict=kage_parsed.get("verdict", "REJECT"),
            kage_confidence=kage_parsed.get("confidence", 0),
        )

        # ── Consensus Logic ──────────────────────────────────────
        kimi_v = kimi_parsed.get("verdict", "REJECT").upper()
        kage_v = kage_parsed.get("verdict", "REJECT").upper()

        consensus, consensus_action, size_modifier = self._resolve_consensus(kimi_v, kage_v)

        # Map consensus_action to legacy verdict format for compatibility
        verdict_map = {
            "APPROVE": "BULL" if input_data.bias_score >= 0 else "BEAR",
            "REJECT": "NEUTRAL",
            "REDUCE": "BULL" if input_data.bias_score >= 0 else "BEAR",
            "INVERT": "BEAR" if input_data.bias_score >= 0 else "BULL",
        }
        legacy_verdict = verdict_map.get(consensus_action, "NEUTRAL")

        # Use lower confidence of the two for safety
        final_confidence = min(
            kimi_parsed.get("confidence", 0),
            kage_parsed.get("confidence", 0),
        )
        # If no consensus, slash confidence
        if not consensus:
            final_confidence = min(final_confidence, 20)

        result = DebateVerdict(
            symbol=input_data.symbol,
            verdict=legacy_verdict,
            confidence=final_confidence,
            reasoning=kage_parsed.get("reasoning", ""),
            key_factor=kage_parsed.get("key_factor", ""),
            riki_argument=riki_response,
            abi_argument=abi_response,
            kage_analysis=kage_response,
            kimi_research=kimi_research,
            kimi_challenge=kimi_challenge,
            kimi_verdict=kimi_v,
            kimi_confidence=kimi_parsed.get("confidence", 0),
            consensus=consensus,
            consensus_action=consensus_action,
            size_modifier=size_modifier,
        )

        logger.info(
            "debate_complete",
            symbol=input_data.symbol,
            verdict=result.verdict,
            confidence=result.confidence,
            consensus=result.consensus,
            consensus_action=result.consensus_action,
            size_modifier=result.size_modifier,
            kimi_verdict=kimi_v,
            kage_verdict=kage_v,
            mode="kimi",
        )

        return result

    @staticmethod
    def _resolve_consensus(
        kimi_verdict: str, kage_verdict: str
    ) -> tuple[bool, str, float]:
        """Apply the consensus rule between Kimi and Kage.

        Rules (in priority order):
        1. If EITHER says INVERT → INVERT (strong directional signal)
        2. If both agree exactly → use that verdict
        3. If EITHER says REDUCE and the other doesn't REJECT → REDUCE at 0.5x
        4. If one APPROVE and one REJECT → no consensus → auto-REJECT
        5. Any other disagreement → auto-REJECT

        Returns:
            (consensus, consensus_action, size_modifier)
        """
        kimi_v = kimi_verdict.upper()
        kage_v = kage_verdict.upper()

        # Rule 1: Either INVERT → INVERT
        if kimi_v == "INVERT" or kage_v == "INVERT":
            return True, "INVERT", 1.0

        # Rule 2: Exact agreement
        if kimi_v == kage_v:
            size = 0.5 if kimi_v == "REDUCE" else 1.0
            return True, kimi_v, size

        # Rule 3: One REDUCE, other not REJECT
        if kimi_v == "REDUCE" and kage_v != "REJECT":
            return True, "REDUCE", 0.5
        if kage_v == "REDUCE" and kimi_v != "REJECT":
            return True, "REDUCE", 0.5

        # Rule 4 & 5: Disagreement → REJECT
        return False, "REJECT", 1.0

    def _build_market_context(self, data: DebateInput) -> str:
        """Build the market data prompt that all personalities see."""
        parts = [
            f"SYMBOL: {data.symbol}",
            f"CURRENT PRICE: ${data.current_price:.2f}",
            f"DAILY CHANGE: {data.daily_change_pct:+.2f}%",
            f"VIX: {data.vix_level:.1f}",
            f"TECHNICAL BIAS SCORE: {data.bias_score:+.1f} (scale: -100 bear to +100 bull)",
        ]

        if data.rsi is not None:
            parts.append(f"RSI(14): {data.rsi:.1f}")
        if data.ema_fast is not None and data.ema_slow is not None:
            parts.append(f"EMA(9): ${data.ema_fast:.2f}, EMA(21): ${data.ema_slow:.2f}")
        if data.volume > 0:
            parts.append(f"VOLUME: {data.volume:,}")
        if data.support_level is not None:
            parts.append(f"KEY SUPPORT: ${data.support_level:.2f}")
        if data.resistance_level is not None:
            parts.append(f"KEY RESISTANCE: ${data.resistance_level:.2f}")
        if data.pillar is not None:
            parts.append(f"PILLAR: P{data.pillar}")
        if data.direction:
            parts.append(f"PROPOSED DIRECTION: {data.direction}")
        if data.tier:
            parts.append(f"TIER: {data.tier}")
        if data.iv_rank is not None:
            parts.append(f"IV RANK: {data.iv_rank:.1f}")
        if data.option_quality_score is not None:
            parts.append(f"OPTION QUALITY SCORE: {data.option_quality_score:.1f}")
        if data.flow_bias != 0.0:
            flow_dir = "BULLISH" if data.flow_bias > 20 else "BEARISH" if data.flow_bias < -20 else "NEUTRAL"
            parts.append(f"INSTITUTIONAL FLOW BIAS: {data.flow_bias:+.1f} ({flow_dir})")
        if data.flow_summary:
            parts.append(f"FLOW DETAILS: {data.flow_summary}")
        if data.news_context:
            parts.append(f"NEWS CONTEXT: {data.news_context}")

        # Sage's broader market intelligence
        if data.sage_intel:
            si = data.sage_intel
            parts.append("")
            parts.append("--- SAGE MARKET INTELLIGENCE ---")
            if si.get("intel_brief"):
                parts.append(si["intel_brief"])
            else:
                if si.get("flow_direction"):
                    parts.append(f"MARKET FLOW: {si['flow_direction']} (bias {si.get('flow_bias', 0):+.1f})")
                if si.get("put_call_ratio"):
                    parts.append(f"SPY PUT/CALL RATIO: {si['put_call_ratio']:.2f}")
                if si.get("max_pain"):
                    parts.append(f"SPY MAX PAIN: ${si['max_pain']:.0f}")
                if si.get("expected_move_spy"):
                    parts.append(f"SPY EXPECTED MOVE: ±${si['expected_move_spy']:.0f} ({si.get('spy_range', '')})")
                if si.get("net_delta"):
                    parts.append(f"NET DELTA: {si['net_delta']:,.0f} ({si.get('net_delta_direction', '')})")
                if si.get("is_event_day"):
                    parts.append(f"⚠️ EVENT DAY: {si.get('event_name', 'unknown')}")
                if si.get("risk_flags"):
                    for flag in si["risk_flags"]:
                        parts.append(f"  {flag}")

        # Journal lessons — what we've learned from recent trades
        if data.journal_lessons:
            parts.append("")
            parts.append("--- TRADE JOURNAL LESSONS (learn from these) ---")
            parts.append(data.journal_lessons)

        return "\n".join(parts)

    async def _get_argument(
        self,
        system_prompt: str,
        market_context: str,
        role_name: str,
    ) -> str:
        """Get an argument from one of the debate participants.

        Args:
            system_prompt: The personality's system prompt.
            market_context: Market data context.
            role_name: For logging.

        Returns:
            The argument text.
        """
        try:
            text = await self._chat(
                system_prompt=system_prompt,
                user_prompt=f"Analyze this trade opportunity:\n\n{market_context}",
            )
            logger.debug("debate_argument", role=role_name, length=len(text))
            return text

        except Exception as e:
            logger.error("debate_argument_failed", role=role_name, error=str(e))
            return f"[{role_name} unavailable: {str(e)}]"

    async def _get_verdict(
        self,
        market_context: str,
        bull_case: str,
        bear_case: str,
        bias_score: float,
    ) -> str:
        """Get Kage's verdict after hearing both sides (legacy 3-step).

        Args:
            market_context: Original market data.
            bull_case: Riki's bull argument.
            bear_case: Abi's bear argument.
            bias_score: Technical bias score for additional context.

        Returns:
            Kage's verdict text (structured format).
        """
        judge_prompt = f"""Here is the market data:

{market_context}

---

RIKI'S BULL CASE:
{bull_case}

---

ABI'S BEAR CASE:
{bear_case}

---

The technical bias system scored this {bias_score:+.1f} on a -100 to +100 scale.

Now deliver your verdict. Remember the exact format:
VERDICT: [BULL/BEAR/NEUTRAL]
CONFIDENCE: [0-100]
REASONING: [Your reasoning]
KEY_FACTOR: [Single most important factor]"""

        try:
            text = await self._chat(
                system_prompt=KAGE_SYSTEM_PROMPT,
                user_prompt=judge_prompt,
                max_tokens=512,
                temperature=0.3,
            )
            logger.debug("debate_verdict", length=len(text))
            return text

        except Exception as e:
            logger.error("debate_verdict_failed", error=str(e))
            return "VERDICT: NEUTRAL\nCONFIDENCE: 0\nREASONING: AI unavailable\nKEY_FACTOR: system_error"

    async def _get_kimi_challenge(
        self,
        market_context: str,
        kimi_research: str,
        bull_case: str,
        bear_case: str,
    ) -> str:
        """Get Kimi's devil's advocate challenge and independent verdict.

        Args:
            market_context: Original market data.
            kimi_research: Kimi's initial research output.
            bull_case: Riki's bull argument.
            bear_case: Abi's bear argument.

        Returns:
            Kimi's challenge text with embedded verdict.
        """
        challenge_prompt = f"""Here is the market data:

{market_context}

---

YOUR RESEARCH ANALYSIS:
{kimi_research}

---

RIKI'S BULL CASE:
{bull_case}

---

ABI'S BEAR CASE:
{bear_case}

---

Now challenge both sides and render your independent verdict.
Remember the exact format for your verdict section:
VERDICT: [APPROVE/REDUCE/REJECT/INVERT]
CONFIDENCE: [0-100]
KEY_CONCERN: [Single biggest risk the final judge MUST address]"""

        try:
            text = await self._chat(
                system_prompt=KIMI_ADVOCATE_PROMPT,
                user_prompt=challenge_prompt,
            )
            logger.debug("kimi_challenge", length=len(text))
            return text

        except Exception as e:
            logger.error("kimi_challenge_failed", error=str(e))
            return "VERDICT: REJECT\nCONFIDENCE: 0\nKEY_CONCERN: Kimi unavailable — defaulting to reject"

    async def _get_full_verdict(
        self,
        market_context: str,
        kimi_research: str,
        bull_case: str,
        bear_case: str,
        kimi_challenge: str,
        bias_score: float,
    ) -> str:
        """Get Kage's final verdict after seeing everything including Kimi.

        Args:
            market_context: Original market data.
            kimi_research: Kimi's initial research.
            bull_case: Riki's bull argument.
            bear_case: Abi's bear argument.
            kimi_challenge: Kimi's devil's advocate + verdict.
            bias_score: Technical bias score.

        Returns:
            Kage's full verdict text (structured format).
        """
        judge_prompt = f"""Here is the market data:

{market_context}

---

KIMI'S RESEARCH ANALYSIS:
{kimi_research}

---

RIKI'S BULL CASE:
{bull_case}

---

ABI'S BEAR CASE:
{bear_case}

---

KIMI'S DEVIL'S ADVOCATE CHALLENGE & INDEPENDENT VERDICT:
{kimi_challenge}

---

The technical bias system scored this {bias_score:+.1f} on a -100 to +100 scale.

Now deliver your final verdict. You MUST address Kimi's key concern.
Remember the exact format:
VERDICT: [APPROVE/REDUCE/REJECT/INVERT]
CONFIDENCE: [0-100]
REASONING: [Your reasoning]
KEY_FACTOR: [Single most important factor]
KIMI_RESPONSE: [Your response to Kimi's concern]"""

        try:
            text = await self._chat(
                system_prompt=KAGE_FULL_SYSTEM_PROMPT,
                user_prompt=judge_prompt,
                max_tokens=768,
                temperature=0.3,
            )
            logger.debug("debate_full_verdict", length=len(text))
            return text

        except Exception as e:
            logger.error("debate_full_verdict_failed", error=str(e))
            return "VERDICT: REJECT\nCONFIDENCE: 0\nREASONING: AI unavailable\nKEY_FACTOR: system_error\nKIMI_RESPONSE: N/A"

    def _parse_verdict(self, raw: str) -> dict[str, Any]:
        """Parse Kage's structured verdict response (legacy format).

        Expected format:
            VERDICT: BULL
            CONFIDENCE: 75
            REASONING: Strong momentum with...
            KEY_FACTOR: EMA crossover confirmed

        Returns:
            Dict with parsed fields.
        """
        result: dict[str, Any] = {
            "verdict": "NEUTRAL",
            "confidence": 0,
            "reasoning": "",
            "key_factor": "",
        }

        for line in raw.strip().split("\n"):
            line = line.strip()
            if line.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if v in ("BULL", "BEAR", "NEUTRAL"):
                    result["verdict"] = v
            elif line.startswith("CONFIDENCE:"):
                try:
                    c = int(line.split(":", 1)[1].strip())
                    result["confidence"] = max(0, min(100, c))
                except ValueError:
                    pass
            elif line.startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()
            elif line.startswith("KEY_FACTOR:"):
                result["key_factor"] = line.split(":", 1)[1].strip()

        # Safety: low confidence → force NEUTRAL
        if result["confidence"] < 30 and result["verdict"] != "NEUTRAL":
            logger.info(
                "verdict_overridden_low_confidence",
                original=result["verdict"],
                confidence=result["confidence"],
            )
            result["verdict"] = "NEUTRAL"

        return result

    def _parse_kimi_verdict(self, raw: str) -> dict[str, Any]:
        """Parse verdict from Kimi or Kage full format.

        Expected fields: VERDICT, CONFIDENCE, KEY_CONCERN, REASONING, KEY_FACTOR, KIMI_RESPONSE

        Returns:
            Dict with parsed fields.
        """
        result: dict[str, Any] = {
            "verdict": "REJECT",
            "confidence": 0,
            "key_concern": "",
            "reasoning": "",
            "key_factor": "",
            "kimi_response": "",
        }

        for line in raw.strip().split("\n"):
            line = line.strip()
            if line.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if v in ("APPROVE", "REDUCE", "REJECT", "INVERT"):
                    result["verdict"] = v
            elif line.startswith("CONFIDENCE:"):
                try:
                    c = int(line.split(":", 1)[1].strip())
                    result["confidence"] = max(0, min(100, c))
                except ValueError:
                    pass
            elif line.startswith("KEY_CONCERN:"):
                result["key_concern"] = line.split(":", 1)[1].strip()
            elif line.startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()
            elif line.startswith("KEY_FACTOR:"):
                result["key_factor"] = line.split(":", 1)[1].strip()
            elif line.startswith("KIMI_RESPONSE:"):
                result["kimi_response"] = line.split(":", 1)[1].strip()

        return result
