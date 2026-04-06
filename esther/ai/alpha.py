"""Alpha Agent — Market Condition Analyzer (runs on Sonnet).

Alpha is the first agent in the pipeline. Before any debate happens,
Alpha scans the broader market environment and produces a Market Condition
Report that all other agents receive as context.

    Alpha 🌐 — The macro strategist. Reads the room before anyone speaks.
               Analyzes VIX regime, sector rotation, SPY/QQQ trends,
               economic calendar, and overnight moves to set the stage.

Alpha runs ONCE per scan cycle (not per ticker), and its report is
shared with Kimi, Riki, Abi, and Kage for every debate in that cycle.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic
import openai
import structlog
from pydantic import BaseModel

from esther.core.config import config, env

logger = structlog.get_logger(__name__)


ALPHA_SYSTEM_PROMPT = """You are Alpha, the macro strategist. Your job is to read the market environment BEFORE any individual trade debates begin.

You are NOT bullish or bearish on any specific ticker. You analyze the OVERALL market conditions and provide a situational awareness report.

Your analysis MUST include:

1. MARKET REGIME: Is this a trending, ranging, or volatile environment? (Based on VIX, SPY trend, breadth)
2. RISK LEVEL: LOW / MODERATE / HIGH / EXTREME — how risky is it to trade today?
3. SECTOR BIAS: Which sectors are leading/lagging? Any rotation signals?
4. KEY EVENTS: Any economic events, FOMC, earnings, or catalysts today that could move markets?
5. OVERNIGHT CONTEXT: What happened in futures/overseas markets that sets today's tone?
6. RECOMMENDED POSTURE:
   - AGGRESSIVE: Strong trends, low VIX, clear direction → size up, more trades
   - NORMAL: Standard conditions → trade the plan
   - DEFENSIVE: Elevated VIX, mixed signals → reduce size, fewer trades
   - CASH: Extreme conditions → sit out or hedge only
7. SIZE_MODIFIER: A float from 0.0 to 1.5 — multiplier for position sizing today
   - 0.0 = don't trade
   - 0.5 = half size
   - 1.0 = normal
   - 1.5 = aggressive

Format your response EXACTLY like this:
REGIME: [TRENDING_UP/TRENDING_DOWN/RANGING/VOLATILE]
RISK_LEVEL: [LOW/MODERATE/HIGH/EXTREME]
SECTOR_BIAS: [brief description]
KEY_EVENTS: [list any events or "None"]
OVERNIGHT: [brief overnight context]
POSTURE: [AGGRESSIVE/NORMAL/DEFENSIVE/CASH]
SIZE_MODIFIER: [0.0-1.5]
SUMMARY: [2-3 sentence overall market read]

Be specific. Use actual numbers. No fluff."""


class AlphaReport(BaseModel):
    """Output of Alpha's market condition analysis."""

    regime: str = "RANGING"
    risk_level: str = "MODERATE"
    sector_bias: str = ""
    key_events: str = "None"
    overnight: str = ""
    posture: str = "NORMAL"
    size_modifier: float = 1.0
    summary: str = ""
    raw_response: str = ""


class AlphaAgent:
    """Market condition analyzer — runs once per scan cycle.

    Uses Sonnet by default (configurable). Produces an AlphaReport
    that is injected into every debate's context for that cycle.
    """

    def __init__(self):
        self._cfg = config().ai
        self._env = env()
        self._last_report: AlphaReport | None = None

        self._anthropic_client = None
        self._groq_client = None
        self._ollama_client = None

        anthropic_key = self._env.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=anthropic_key)

        groq_key = self._cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            self._groq_client = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=groq_key,
            )

        self._ollama_client = openai.AsyncOpenAI(
            base_url=self._cfg.ollama_base_url or "http://127.0.0.1:11434/v1",
            api_key="ollama",
        )

        self._model = getattr(self._cfg, "alpha_model", None) or "claude-sonnet-4-20250514"
        self._groq_model = self._cfg.groq_model or "llama-3.3-70b-versatile"
        self._ollama_model = self._cfg.ollama_model or "qwen2.5-coder:7b"

        logger.info("alpha_agent_init", model=self._model)

    async def analyze(
        self,
        vix_level: float,
        spy_price: float,
        spy_change_pct: float,
        qqq_price: float = 0.0,
        qqq_change_pct: float = 0.0,
        sage_intel: dict | None = None,
        account_balance: float = 100_000,
        daily_pnl: float = 0.0,
    ) -> AlphaReport:
        """Run Alpha's market condition analysis. Called once per scan cycle."""
        prompt = self._build_prompt(
            vix_level, spy_price, spy_change_pct,
            qqq_price, qqq_change_pct, sage_intel,
            account_balance, daily_pnl,
        )

        logger.info("alpha_analyzing", vix=vix_level, spy=spy_price, spy_chg=spy_change_pct)

        raw_response = await self._call_ai(prompt)
        report = self._parse_response(raw_response)
        self._last_report = report

        logger.info(
            "alpha_report",
            regime=report.regime,
            risk=report.risk_level,
            posture=report.posture,
            size_mod=report.size_modifier,
        )

        return report

    @property
    def last_report(self) -> AlphaReport | None:
        return self._last_report

    def get_debate_context(self) -> str:
        """Format Alpha's report as context for the debate agents."""
        if not self._last_report:
            return ""

        r = self._last_report
        return (
            f"\n--- ALPHA'S MARKET CONDITION REPORT ---\n"
            f"REGIME: {r.regime}\n"
            f"RISK LEVEL: {r.risk_level}\n"
            f"POSTURE: {r.posture} (size modifier: {r.size_modifier}x)\n"
            f"SECTOR BIAS: {r.sector_bias}\n"
            f"KEY EVENTS: {r.key_events}\n"
            f"OVERNIGHT: {r.overnight}\n"
            f"SUMMARY: {r.summary}\n"
            f"--- END ALPHA REPORT ---\n"
        )

    def _build_prompt(self, vix_level, spy_price, spy_change_pct, qqq_price, qqq_change_pct, sage_intel, account_balance, daily_pnl) -> str:
        parts = [
            f"VIX: {vix_level:.1f}",
            f"SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%)",
        ]
        if qqq_price > 0:
            parts.append(f"QQQ: ${qqq_price:.2f} ({qqq_change_pct:+.2f}%)")
        parts.append(f"ACCOUNT BALANCE: ${account_balance:,.0f}")
        parts.append(f"TODAY'S PnL: ${daily_pnl:+,.0f}")

        if sage_intel:
            if sage_intel.get("intel_brief"):
                parts.append(f"\nSAGE INTEL: {sage_intel['intel_brief']}")
            if sage_intel.get("flow_direction"):
                parts.append(f"FLOW: {sage_intel['flow_direction']} (bias {sage_intel.get('flow_bias', 0):+.1f})")
            if sage_intel.get("regime"):
                parts.append(f"REGIME DATA: {sage_intel['regime']}")
            if sage_intel.get("calendar_events"):
                parts.append(f"CALENDAR: {sage_intel['calendar_events']}")

        return "\n".join(parts)

    async def _call_ai(self, prompt: str) -> str:
        """Call AI with fallback chain: Anthropic → Groq → Ollama."""
        if self._anthropic_client:
            try:
                response = await self._anthropic_client.messages.create(
                    model=self._model,
                    max_tokens=512,
                    temperature=0.3,
                    system=ALPHA_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text
            except Exception as e:
                logger.warning("alpha_anthropic_failed", error=str(e)[:200])

        if self._groq_client:
            try:
                response = await self._groq_client.chat.completions.create(
                    model=self._groq_model,
                    max_tokens=512,
                    temperature=0.3,
                    messages=[
                        {"role": "system", "content": ALPHA_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning("alpha_groq_failed", error=str(e)[:200])

        try:
            response = await self._ollama_client.chat.completions.create(
                model=self._ollama_model,
                max_tokens=512,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": ALPHA_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("alpha_all_backends_failed", error=str(e)[:200])
            return "REGIME: RANGING\nRISK_LEVEL: MODERATE\nPOSTURE: DEFENSIVE\nSIZE_MODIFIER: 0.75\nSUMMARY: All AI backends failed — defaulting to defensive posture."

    @staticmethod
    def _parse_response(raw: str) -> AlphaReport:
        report = AlphaReport(raw_response=raw)
        for line in raw.strip().split("\n"):
            line = line.strip()
            if line.startswith("REGIME:"):
                report.regime = line.split(":", 1)[1].strip().upper()
            elif line.startswith("RISK_LEVEL:"):
                report.risk_level = line.split(":", 1)[1].strip().upper()
            elif line.startswith("SECTOR_BIAS:"):
                report.sector_bias = line.split(":", 1)[1].strip()
            elif line.startswith("KEY_EVENTS:"):
                report.key_events = line.split(":", 1)[1].strip()
            elif line.startswith("OVERNIGHT:"):
                report.overnight = line.split(":", 1)[1].strip()
            elif line.startswith("POSTURE:"):
                report.posture = line.split(":", 1)[1].strip().upper()
            elif line.startswith("SIZE_MODIFIER:"):
                try:
                    val = float(line.split(":", 1)[1].strip())
                    report.size_modifier = max(0.0, min(1.5, val))
                except ValueError:
                    report.size_modifier = 1.0
            elif line.startswith("SUMMARY:"):
                report.summary = line.split(":", 1)[1].strip()
        return report
