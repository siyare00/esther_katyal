"""Neo Agent — Opus-Powered Self-Healing Watchdog (runs on Opus).

Neo is the system guardian with SELF-HEALING powers. When an error occurs,
Neo doesn't just diagnose — it FIXES the code, hot-reloads the module,
and lets the other agents continue trading without interruption.

    Neo 🛡️ — The self-healing watchdog. Monitors Esther while she trades.
              Detects errors → reads the broken code → writes a fix →
              hot-reloads the module → agents keep trading on fixed code.

Self-Healing Flow:
    1. Error caught in pipeline
    2. Neo reads the traceback + source code of the failing file
    3. Neo (Opus) generates a code patch (old_code → new_code)
    4. Patch is applied to the file on disk
    5. Module is hot-reloaded into the running process
    6. Other agents (Alpha, Kimi, Riki, Abi, Kage) continue trading on fixed code
    7. Patch is logged for operator review

Safety Rails:
    - Only patches files under esther/ (not system files)
    - Max 3 auto-fix attempts per error before escalating to operator
    - Backs up original file before patching
    - Validates syntax before applying patch
    - Won't patch engine.py core loop (too dangerous for live changes)
"""

from __future__ import annotations

import ast
import importlib
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import openai
import structlog
from pydantic import BaseModel

from esther.core.config import config, env

logger = structlog.get_logger(__name__)

# Files Neo is allowed to patch (safe for hot-reload)
PATCHABLE_MODULES = {
    "esther.signals.bias_engine",
    "esther.signals.black_swan",
    "esther.signals.quality_filter",
    "esther.signals.inversion_engine",
    "esther.signals.reentry",
    "esther.signals.sage",
    "esther.signals.ifvg",
    "esther.signals.flow",
    "esther.signals.levels",
    "esther.signals.regime",
    "esther.signals.calendar",
    "esther.signals.watchlist",
    "esther.signals.premarket",
    "esther.ai.debate",
    "esther.ai.sizing",
    "esther.ai.alpha",
    "esther.execution.pillars",
    "esther.execution.position_manager",
    "esther.risk.risk_manager",
    "esther.risk.journal",
    "esther.data.tradier",
    "esther.data.alpaca",
    "esther.core.config",
}

# Files Neo must NEVER patch (core loop, too dangerous)
NEVER_PATCH = {
    "esther.core.engine",
    "esther.ai.neo",
}


NEO_SYSTEM_PROMPT = """You are Neo, the self-healing watchdog for the Esther trading bot. You run on Claude Opus because you need deep reasoning to diagnose AND FIX complex issues.

You don't just suggest fixes — you WRITE THE ACTUAL CODE PATCH that will be applied live while trading continues.

When analyzing an error, you will receive:
- The full traceback
- The SOURCE CODE of the file that errored
- Recent error history

Your response MUST include these sections:

1. ERROR_TYPE: Classify it (API_ERROR, CONFIG_ERROR, LOGIC_BUG, RATE_LIMIT, BROKER_ERROR, DATA_ERROR)
2. SEVERITY: LOW / MEDIUM / HIGH / CRITICAL
3. ROOT_CAUSE: What actually went wrong (be specific, reference line numbers)
4. CAN_AUTO_FIX: YES or NO — can you write a code patch to fix this?
5. If CAN_AUTO_FIX is YES, include a PATCH section:

PATCH_FILE: [relative path, e.g., esther/signals/bias_engine.py]
PATCH_OLD:
```python
[exact code to replace — must match the file exactly]
```
PATCH_NEW:
```python
[replacement code — the fix]
```
PATCH_REASON: [one-line explanation of what the patch does]

6. IMPACT: What trades or positions are affected
7. WORKAROUND: If auto-fix fails, how to keep trading

Rules for patches:
- The PATCH_OLD must be an EXACT substring of the source file (whitespace matters!)
- Keep patches MINIMAL — fix the bug, don't refactor
- Don't change function signatures (other code depends on them)
- Add try/except safety nets around fragile code paths
- Prefer defensive fixes (handle None, add defaults) over structural changes
- If the error is from an external API (broker, AI backend), add retry/fallback logic
- NEVER change import statements (can break module loading)

Format your full response EXACTLY like this:
ERROR_TYPE: [type]
SEVERITY: [level]
ROOT_CAUSE: [specific cause with line numbers]
CAN_AUTO_FIX: [YES/NO]
PATCH_FILE: [path]
PATCH_OLD:
```python
[old code]
```
PATCH_NEW:
```python
[new code]
```
PATCH_REASON: [one line]
IMPACT: [what's affected]
WORKAROUND: [fallback if patch fails]"""


NEO_HEALTH_PROMPT = """You are Neo, the self-healing watchdog. This is a periodic HEALTH CHECK.

Analyze the system state and report:
HEALTH: [HEALTHY/DEGRADED/UNHEALTHY/CRITICAL]
ISSUES: [list of issues found, or "None"]
POSITIONS: [summary of open position health]
RECOMMENDATIONS: [what to do]

If you detect config issues that can be fixed, include a PATCH section (same format as error fixes)."""


class NeoPatch(BaseModel):
    """A code patch that Neo wants to apply."""
    file_path: str = ""
    old_code: str = ""
    new_code: str = ""
    reason: str = ""
    applied: bool = False
    backed_up: bool = False
    reloaded: bool = False


class NeoAlert(BaseModel):
    """An alert from Neo about a system issue."""
    timestamp: str = ""
    error_type: str = ""
    severity: str = "LOW"
    root_cause: str = ""
    can_auto_fix: bool = False
    patch: NeoPatch | None = None
    impact: str = ""
    workaround: str = ""
    raw_response: str = ""
    should_stop: bool = False
    healed: bool = False


class NeoHealthCheck(BaseModel):
    """Result of Neo's periodic health check."""
    timestamp: str = ""
    health: str = "HEALTHY"
    issues: list[str] = []
    positions_summary: str = ""
    recommendations: list[str] = []
    patches_applied: list[NeoPatch] = []
    raw_response: str = ""


class NeoAgent:
    """Self-healing watchdog — diagnoses errors AND fixes them live.

    When an error occurs:
    1. Reads the source code of the failing module
    2. Sends traceback + source to Opus for diagnosis
    3. Opus returns a code patch (old → new)
    4. Neo validates the patch (syntax check)
    5. Backs up the original file
    6. Applies the patch
    7. Hot-reloads the module
    8. Other agents continue trading on fixed code

    Safety:
    - Only patches files in PATCHABLE_MODULES
    - Max 3 auto-fix attempts per unique error
    - Validates syntax before applying
    - Backs up originals to .neo_backup/
    """

    def __init__(self, health_check_interval: int = 10):
        self._cfg = config().ai
        self._env = env()
        self._health_check_interval = health_check_interval
        self._scan_count_since_check = 0
        self._error_history: list[NeoAlert] = []
        self._consecutive_errors = 0
        self._max_error_history = 50
        self._patch_history: list[NeoPatch] = []
        self._fix_attempts: dict[str, int] = {}
        self._max_fix_attempts = 3

        self._project_root = Path(__file__).resolve().parent.parent.parent
        self._backup_dir = self._project_root / ".neo_backup"
        self._backup_dir.mkdir(exist_ok=True)

        self._opus_client = None
        self._groq_client = None

        anthropic_key = self._env.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            self._opus_client = anthropic.AsyncAnthropic(api_key=anthropic_key)

        groq_key = self._cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            self._groq_client = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=groq_key,
            )

        self._opus_model = getattr(self._cfg, "neo_model", None) or "claude-opus-4-20250514"
        self._sonnet_model = "claude-sonnet-4-20250514"
        self._groq_model = self._cfg.groq_model or "llama-3.3-70b-versatile"

        logger.info("neo_agent_init", opus_model=self._opus_model, check_interval=health_check_interval, mode="self_healing")

    async def on_error(self, error: Exception, context: str = "", symbol: str = "", component: str = "") -> NeoAlert:
        """Called when an error occurs — diagnoses AND auto-fixes if possible."""
        self._consecutive_errors += 1

        error_key = f"{component}:{type(error).__name__}:{str(error)[:100]}"
        self._fix_attempts[error_key] = self._fix_attempts.get(error_key, 0) + 1
        attempt = self._fix_attempts[error_key]

        tb = traceback.format_exception(type(error), error, error.__traceback__)
        tb_str = "".join(tb[-8:])

        source_code = self._extract_source_from_traceback(error)

        prompt = (
            f"ERROR in Esther Trading Bot (attempt {attempt}/{self._max_fix_attempts}):\n\n"
            f"COMPONENT: {component}\n"
            f"SYMBOL: {symbol or 'N/A'}\n"
            f"CONTEXT: {context}\n"
            f"ERROR TYPE: {type(error).__name__}\n"
            f"ERROR MESSAGE: {str(error)}\n"
            f"TRACEBACK:\n{tb_str}\n"
            f"CONSECUTIVE ERRORS: {self._consecutive_errors}\n"
        )

        if source_code:
            prompt += f"\nSOURCE CODE OF FAILING FILE:\n```python\n{source_code}\n```\n"

        if self._error_history:
            recent = self._error_history[-3:]
            prompt += f"\nRECENT ERROR HISTORY ({len(recent)} recent):\n"
            for alert in recent:
                prompt += f"  - [{alert.severity}] {alert.error_type}: {alert.root_cause[:100]}"
                if alert.healed:
                    prompt += " [HEALED]"
                prompt += "\n"

        if self._patch_history:
            prompt += f"\nRECENT PATCHES APPLIED ({len(self._patch_history)}):\n"
            for p in self._patch_history[-3:]:
                prompt += f"  - {p.file_path}: {p.reason} [{'OK' if p.applied else 'FAILED'}]\n"

        # Always use Opus for self-healing
        raw = await self._call_ai(prompt, use_opus=True)
        alert = self._parse_alert(raw)
        alert.timestamp = datetime.now().isoformat()

        # Attempt auto-fix
        if alert.can_auto_fix and alert.patch and attempt <= self._max_fix_attempts:
            patch = alert.patch
            healed = await self._apply_patch(patch)
            alert.healed = healed

            if healed:
                self._consecutive_errors = 0
                logger.info("neo_self_healed", file=patch.file_path, reason=patch.reason, attempt=attempt)
            else:
                logger.warning("neo_patch_failed", file=patch.file_path, reason=patch.reason, attempt=attempt)
        elif attempt > self._max_fix_attempts:
            logger.error("neo_max_attempts_exceeded", error_key=error_key, attempts=attempt)

        self._error_history.append(alert)
        if len(self._error_history) > self._max_error_history:
            self._error_history = self._error_history[-self._max_error_history:]

        alert.should_stop = alert.severity == "CRITICAL" and not alert.healed

        logger.warning(
            "neo_alert",
            severity=alert.severity, error_type=alert.error_type,
            root_cause=alert.root_cause[:200], can_auto_fix=alert.can_auto_fix,
            healed=alert.healed, should_stop=alert.should_stop,
            component=component, symbol=symbol,
        )

        return alert

    async def health_check(self, account_balance: float, daily_pnl: float, open_positions: int,
                           total_trades_today: int, scan_count: int, errors_today: int,
                           rejection_count: int = 0, extra_context: str = "") -> NeoHealthCheck | None:
        """Periodic health check — runs every N scan cycles."""
        self._scan_count_since_check += 1

        force_check = errors_today >= 5 or rejection_count >= 10
        if not force_check and self._scan_count_since_check < self._health_check_interval:
            return None

        self._scan_count_since_check = 0

        prompt = (
            f"HEALTH CHECK — Esther Trading Bot\n\n"
            f"ACCOUNT BALANCE: ${account_balance:,.0f}\n"
            f"TODAY'S PnL: ${daily_pnl:+,.0f} ({daily_pnl / max(account_balance, 1) * 100:+.2f}%)\n"
            f"OPEN POSITIONS: {open_positions}\n"
            f"TRADES TODAY: {total_trades_today}\n"
            f"SCAN CYCLE: #{scan_count}\n"
            f"ERRORS TODAY: {errors_today}\n"
            f"TRADE REJECTIONS: {rejection_count}\n"
            f"PATCHES APPLIED TODAY: {len(self._patch_history)}\n"
            f"HEALED ERRORS: {sum(1 for a in self._error_history if a.healed)}\n"
        )

        if self._error_history:
            prompt += f"\nERROR SUMMARY ({len(self._error_history)} total):\n"
            type_counts: dict[str, int] = {}
            for alert in self._error_history:
                t = alert.error_type or "UNKNOWN"
                type_counts[t] = type_counts.get(t, 0) + 1
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
                prompt += f"  {t}: {c}x\n"

        if extra_context:
            prompt += f"\nADDITIONAL CONTEXT:\n{extra_context}\n"

        use_opus = errors_today >= 5 or (daily_pnl < -account_balance * 0.05)
        raw = await self._call_ai(prompt, use_opus=use_opus)
        check = self._parse_health_check(raw)
        check.timestamp = datetime.now().isoformat()

        logger.info("neo_health_check", health=check.health, issues=len(check.issues), pnl=daily_pnl, errors=errors_today)

        if check.health == "HEALTHY":
            self._consecutive_errors = 0

        return check

    def on_trade_success(self) -> None:
        """Called after a successful trade."""
        self._consecutive_errors = 0

    # ── Self-Healing Core ────────────────────────────────────────

    def _extract_source_from_traceback(self, error: Exception) -> str:
        """Read the source file that caused the error from the traceback."""
        tb = error.__traceback__
        if not tb:
            return ""

        while tb.tb_next:
            tb = tb.tb_next

        filename = tb.tb_frame.f_code.co_filename

        if "esther/" not in filename and "esther\\" not in filename:
            return ""

        try:
            source_path = Path(filename)
            if source_path.exists():
                content = source_path.read_text()
                lines = content.split("\n")
                if len(lines) > 300:
                    error_line = tb.tb_lineno
                    start = max(0, error_line - 50)
                    end = min(len(lines), error_line + 50)
                    content = "\n".join(
                        f"{i+1}: {line}" for i, line in enumerate(lines[start:end], start=start)
                    )
                return content
        except Exception:
            pass

        return ""

    async def _apply_patch(self, patch: NeoPatch) -> bool:
        """Validate, backup, apply a code patch, and hot-reload the module."""
        file_path = self._project_root / patch.file_path
        if not file_path.exists():
            logger.error("neo_patch_file_not_found", path=str(file_path))
            return False

        module_name = patch.file_path.replace("/", ".").replace("\\", ".").removesuffix(".py")
        if module_name in NEVER_PATCH:
            logger.warning("neo_patch_blocked", module=module_name, reason="in NEVER_PATCH list")
            return False

        if module_name not in PATCHABLE_MODULES:
            logger.warning("neo_patch_blocked", module=module_name, reason="not in PATCHABLE_MODULES")
            return False

        try:
            original_content = file_path.read_text()
        except Exception as e:
            logger.error("neo_patch_read_failed", error=str(e))
            return False

        if patch.old_code not in original_content:
            logger.warning("neo_patch_old_code_not_found", file=patch.file_path, old_code_preview=patch.old_code[:100])
            return False

        new_content = original_content.replace(patch.old_code, patch.new_code, 1)

        # Validate syntax
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            logger.error("neo_patch_syntax_error", file=patch.file_path, error=str(e))
            return False

        # Backup original
        backup_path = None
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{patch.file_path.replace('/', '_')}_{timestamp}.bak"
            backup_path = self._backup_dir / backup_name
            shutil.copy2(file_path, backup_path)
            patch.backed_up = True
            logger.info("neo_backup_created", backup=str(backup_path))
        except Exception as e:
            logger.warning("neo_backup_failed", error=str(e))

        # Write patched file
        try:
            file_path.write_text(new_content)
            patch.applied = True
            logger.info("neo_patch_applied", file=patch.file_path, reason=patch.reason)
        except Exception as e:
            logger.error("neo_patch_write_failed", error=str(e))
            if patch.backed_up and backup_path:
                try:
                    shutil.copy2(backup_path, file_path)
                except Exception:
                    pass
            return False

        # Hot-reload the module
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
                patch.reloaded = True
                logger.info("neo_module_reloaded", module=module_name)
            else:
                patch.reloaded = True  # Will be loaded fresh on next import
        except Exception as e:
            logger.error("neo_reload_failed", module=module_name, error=str(e))

        self._patch_history.append(patch)
        return patch.applied

    def get_patch_summary(self) -> str:
        """Get a summary of all patches applied this session."""
        if not self._patch_history:
            return "No patches applied this session."
        lines = [f"Neo Patches Applied ({len(self._patch_history)}):"]
        for p in self._patch_history:
            status = "APPLIED+RELOADED" if p.reloaded else "APPLIED" if p.applied else "FAILED"
            lines.append(f"  [{status}] {p.file_path}: {p.reason}")
        return "\n".join(lines)

    # ── AI Calls ─────────────────────────────────────────────────

    async def _call_ai(self, prompt: str, use_opus: bool = False) -> str:
        """Call AI backend — Opus for self-healing, Sonnet/Groq for health checks."""
        system = NEO_SYSTEM_PROMPT

        if self._opus_client:
            model = self._opus_model if use_opus else self._sonnet_model
            try:
                response = await self._opus_client.messages.create(
                    model=model, max_tokens=2048, temperature=0.1,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text
            except Exception as e:
                logger.warning("neo_anthropic_failed", model=model, error=str(e)[:200])

        if self._groq_client:
            try:
                response = await self._groq_client.chat.completions.create(
                    model=self._groq_model, max_tokens=2048, temperature=0.1,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning("neo_groq_failed", error=str(e)[:200])

        return (
            "ERROR_TYPE: AI_FAILURE\nSEVERITY: MEDIUM\n"
            "ROOT_CAUSE: All AI backends unavailable for Neo self-healing\n"
            "CAN_AUTO_FIX: NO\n"
            "IMPACT: Neo cannot auto-fix — trading continues with caution\n"
            "WORKAROUND: Monitor logs manually, restart if errors persist"
        )

    # ── Parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_alert(raw: str) -> NeoAlert:
        """Parse Neo's response including code patch."""
        alert = NeoAlert(raw_response=raw)

        patch_file = ""
        patch_old = ""
        patch_new = ""
        patch_reason = ""

        in_old_block = False
        in_new_block = False
        old_lines: list[str] = []
        new_lines: list[str] = []

        for line in raw.split("\n"):
            stripped = line.strip()

            if in_old_block:
                if stripped == "```":
                    in_old_block = False
                    patch_old = "\n".join(old_lines)
                else:
                    old_lines.append(line)
                continue

            if in_new_block:
                if stripped == "```":
                    in_new_block = False
                    patch_new = "\n".join(new_lines)
                else:
                    new_lines.append(line)
                continue

            if stripped.startswith("PATCH_OLD:"):
                continue
            if stripped == "```python" and not patch_old and not in_new_block:
                if not old_lines and not patch_old:
                    in_old_block = True
                    continue
                elif patch_old and not patch_new:
                    in_new_block = True
                    continue

            if stripped.startswith("PATCH_NEW:"):
                continue

            if stripped == "```python" and patch_old and not patch_new:
                in_new_block = True
                continue

            if stripped.startswith("ERROR_TYPE:"):
                alert.error_type = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("SEVERITY:"):
                alert.severity = stripped.split(":", 1)[1].strip().upper()
            elif stripped.startswith("ROOT_CAUSE:"):
                alert.root_cause = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("CAN_AUTO_FIX:"):
                val = stripped.split(":", 1)[1].strip().upper()
                alert.can_auto_fix = val == "YES"
            elif stripped.startswith("PATCH_FILE:"):
                patch_file = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("PATCH_REASON:"):
                patch_reason = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("IMPACT:"):
                alert.impact = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("WORKAROUND:"):
                alert.workaround = stripped.split(":", 1)[1].strip()

        if alert.can_auto_fix and patch_file and patch_old and patch_new:
            alert.patch = NeoPatch(
                file_path=patch_file, old_code=patch_old,
                new_code=patch_new, reason=patch_reason,
            )

        return alert

    @staticmethod
    def _parse_health_check(raw: str) -> NeoHealthCheck:
        """Parse Neo's health check response."""
        check = NeoHealthCheck(raw_response=raw)
        for line in raw.strip().split("\n"):
            line = line.strip()
            if line.startswith("HEALTH:"):
                check.health = line.split(":", 1)[1].strip().upper()
            elif line.startswith("ISSUES:"):
                issues_str = line.split(":", 1)[1].strip()
                if issues_str.lower() != "none":
                    check.issues = [i.strip() for i in issues_str.split(";") if i.strip()]
            elif line.startswith("POSITIONS:"):
                check.positions_summary = line.split(":", 1)[1].strip()
            elif line.startswith("RECOMMENDATIONS:"):
                rec_str = line.split(":", 1)[1].strip()
                if rec_str.lower() != "none":
                    check.recommendations = [r.strip() for r in rec_str.split(";") if r.strip()]
        return check
