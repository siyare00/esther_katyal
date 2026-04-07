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

IRON CONDOR RULE (P1 trades): When VIX is between 25-35, Iron Condors are HIGH PROBABILITY setups.
Elevated VIX = fat premium = wide expected move already priced in. If strikes are outside the expected move,
APPROVE or REDUCE unless there is a specific catalyst that will move price beyond the wings.
Do NOT reject ICs purely due to market uncertainty — that uncertainty IS the reason to sell premium.

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

    AI Backend Fallback Chain (never stops):
        Primary → Groq (fast, free 70B)
        Fallback 1 → Anthropic Claude (best quality)
        Fallback 2 → Ollama local (always available)
        On rate limit / error, automatically cascades to next backend.
    """

    # Fallback priority: groq (fast+free) → gemini (fast+cheap) → anthropic (best) → ollama (local)
    BACKEND_PRIORITY = ["groq", "gemini", "anthropic", "ollama"]

    def __init__(self):
        self._cfg = config().ai
        self._env = env()
        self._primary_backend = self._cfg.ai_backend.lower()

        # Build all available clients upfront
        self._clients: dict[str, Any] = {}
        self._models: dict[str, str] = {}

        # Groq — primary model + fallback models on separate rate limit pools
        groq_key = self._cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")
        self._groq_models: list[str] = []
        if groq_key:
            self._clients["groq"] = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=groq_key,
            )
            primary_groq = self._cfg.groq_model or "llama-3.3-70b-versatile"
            self._models["groq"] = primary_groq
            self._groq_models = [primary_groq]
            fallback_models = getattr(self._cfg, "groq_fallback_models", None) or []
            for m in fallback_models:
                if m not in self._groq_models:
                    self._groq_models.append(m)

        # Gemini — OpenAI-compatible via Google's endpoint
        gemini_key = self._cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        self._gemini_models: list[str] = []
        if gemini_key:
            self._clients["gemini"] = openai.AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=gemini_key,
            )
            primary_gemini = self._cfg.gemini_model or "gemini-2.5-flash"
            self._models["gemini"] = primary_gemini
            self._gemini_models = [primary_gemini]
            gemini_fallbacks = getattr(self._cfg, "gemini_fallback_models", None) or []
            for m in gemini_fallbacks:
                if m not in self._gemini_models:
                    self._gemini_models.append(m)

        # Anthropic
        anthropic_key = self._env.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            self._clients["anthropic"] = anthropic.AsyncAnthropic(api_key=anthropic_key)
            self._models["anthropic"] = self._cfg.model or "claude-sonnet-4-20250514"

        # Ollama (local, always available if running)
        self._clients["ollama"] = openai.AsyncOpenAI(
            base_url=self._cfg.ollama_base_url or "http://127.0.0.1:11434/v1",
            api_key="ollama",
        )
        self._models["ollama"] = self._cfg.ollama_model or "glm-4.7-flash:latest"

        # Build ordered fallback chain starting with primary
        self._fallback_chain = []
        if self._primary_backend in self._clients:
            self._fallback_chain.append(self._primary_backend)
        for backend in self.BACKEND_PRIORITY:
            if backend not in self._fallback_chain and backend in self._clients:
                self._fallback_chain.append(backend)

        # Legacy compat
        self._backend = self._primary_backend
        self._client = self._clients.get(self._primary_backend)

        logger.info("ai_debate_init",
                     primary=self._primary_backend,
                     fallback_chain=self._fallback_chain,
                     groq_models=self._groq_models,
                     gemini_models=self._gemini_models,
                     available=list(self._clients.keys()))

    async def _call_openai_compat(
        self,
        client: Any,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call an OpenAI-compatible backend (Groq, Ollama)."""
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    async def _call_anthropic(
        self,
        client: Any,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Anthropic backend."""
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    async def _chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Chat with automatic fallback — never stops.

        For Groq: cycles through all models (each has separate rate limits)
        before moving to next backend. Chain: Groq models → Anthropic → Ollama.
        """
        _max_tokens = max_tokens or self._cfg.max_tokens
        _temperature = temperature if temperature is not None else self._cfg.temperature
        last_error = None

        for backend in self._fallback_chain:
            client = self._clients[backend]

            if backend == "groq":
                for groq_model in self._groq_models:
                    try:
                        return await self._call_openai_compat(
                            client, groq_model, system_prompt, user_prompt,
                            _max_tokens, _temperature,
                        )
                    except Exception as e:
                        logger.warning("ai_backend_failed",
                                       backend=f"groq/{groq_model}",
                                       error=str(e)[:200])
                        last_error = e
                        continue

            elif backend == "gemini":
                for gemini_model in self._gemini_models:
                    try:
                        return await self._call_openai_compat(
                            client, gemini_model, system_prompt, user_prompt,
                            _max_tokens, _temperature,
                        )
                    except Exception as e:
                        logger.warning("ai_backend_failed",
                                       backend=f"gemini/{gemini_model}",
                                       error=str(e)[:200])
                        last_error = e
                        continue

            elif backend == "anthropic":
                try:
                    return await self._call_anthropic(
                        client, self._models[backend], system_prompt,
                        user_prompt, _max_tokens, _temperature,
                    )
                except Exception as e:
                    logger.warning("ai_backend_failed",
                                   backend=backend, error=str(e)[:200])
                    last_error = e
                    continue

            elif backend == "ollama":
                try:
                    return await self._call_openai_compat(
                        client, self._models[backend], system_prompt,
                        user_prompt, _max_tokens, _temperature,
                    )
                except Exception as e:
                    logger.warning("ai_backend_failed",
                                   backend=backend, error=str(e)[:200])
                    last_error = e
                    continue

        raise RuntimeError(f"All AI backends failed. Last error: {last_error}")

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

        # Use average confidence of Kimi + Kage
        # If Kimi returns 0 (parse failure), fall back to Kage confidence only
        kimi_conf = kimi_parsed.get("confidence", 0)
        kage_conf = kage_parsed.get("confidence", 0)
        if kimi_conf == 0 and kage_conf > 0:
            final_confidence = kage_conf  # Kimi parse failed, trust Kage
        elif kimi_conf > 0 and kage_conf > 0:
            final_confidence = (kimi_conf + kage_conf) // 2  # Average
        else:
            final_confidence = max(kimi_conf, kage_conf)
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

        # VIX strategic interpretation — tell the AI what VIX means for strategy
        if data.vix_level > 0:
            parts.append("")
            if 25.0 <= data.vix_level <= 35.0:
                parts.append(f"⚡ VIX STRATEGY SIGNAL: VIX={data.vix_level:.1f} is in the IRON CONDOR SWEET SPOT (25-35). "
                              f"Elevated IV means fat premium. SuperLuckeee strategy: PRIORITIZE P1 Iron Condors. "
                              f"This is a HIGH-PROBABILITY setup. Lean toward APPROVE for IC trades.")
            elif data.vix_level > 35.0:
                parts.append(f"⚠️ VIX CAPITULATION: VIX={data.vix_level:.1f} — extreme fear, consider only P4 directional scalps.")
            elif data.vix_level < 15.0:
                parts.append(f"VIX LOW: VIX={data.vix_level:.1f} — low premium, ICs less attractive.")

        # P1 IC context — remind AI what an IC needs
        if data.pillar == 1:
            parts.append("IC STRATEGY: Iron Condor profits when price stays INSIDE the wings. "
                         "Approve if you believe SPX/SPY will NOT reach the short strikes by expiry.")

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
        Robust: handles markdown bold (**VERDICT:**), extra spaces, and numeric extraction.

        Returns:
            Dict with parsed fields.
        """
        import re as _re
        result: dict[str, Any] = {
            "verdict": "REJECT",
            "confidence": 0,
            "key_concern": "",
            "reasoning": "",
            "key_factor": "",
            "kimi_response": "",
        }

        for line in raw.strip().split("\n"):
            line = line.strip().lstrip("*").rstrip("*").strip()
            if line.upper().startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper().split()[0]
                if v in ("APPROVE", "REDUCE", "REJECT", "INVERT"):
                    result["verdict"] = v
            elif line.upper().startswith("CONFIDENCE:"):
                raw_val = line.split(":", 1)[1].strip()
                # Extract first number found (handles "75", "75/100", "75%", etc.)
                nums = _re.findall(r'\d+', raw_val)
                if nums:
                    result["confidence"] = max(0, min(100, int(nums[0])))
            elif line.upper().startswith("KEY_CONCERN:"):
                result["key_concern"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("KEY_FACTOR:"):
                result["key_factor"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("KIMI_RESPONSE:"):
                result["kimi_response"] = line.split(":", 1)[1].strip()

        return result
