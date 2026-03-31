"""Trade Journal — Record Everything, Learn From It.

Every trade gets logged with full context:
    - Entry: symbol, direction, pillar, strike, delta, premium
    - Signals: bias score, flow bias, VIX, RSI, AI confidence
    - Market context: price levels, regime, time of day
    - Result: P/L, hold time, exit reason
    - Classification: good trade, bad trade, and WHY

The journal builds a learning database that Esther queries before
each new trade to find patterns in wins and losses.

From @SuperLuckeee's 4 Key Stats:
    1. Win Rate = Wins / Total Trades
    2. Average Win = Total Profit / Wins
    3. Average Loss = Total Losses / Losses
    4. Bad Trade % = Bad Trades / Total Trades

Storage: JSON Lines file for fast append + easy analysis.
"""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Default journal path
_JOURNAL_DIR = Path(__file__).parent.parent.parent / "data" / "journal"


class TradeEntry(BaseModel):
    """Complete record of a single trade."""

    # Identity
    id: str  # position ID
    date: str  # YYYY-MM-DD
    timestamp: str  # ISO datetime
    symbol: str
    pillar: int
    direction: str  # BULL or BEAR

    # Entry details
    strike: float = 0.0
    delta: float = 0.0
    entry_price: float = 0.0
    contracts: int = 0
    max_risk: float = 0.0

    # Signals at entry
    bias_score: float = 0.0
    flow_bias: float = 0.0
    vix_level: float = 0.0
    rsi: float | None = None
    ai_confidence: int = 0
    ai_verdict: str = ""
    regime: str = ""

    # Market context
    spy_price: float = 0.0
    time_of_day: str = ""  # "open", "midday", "power_hour", "close"
    day_of_week: str = ""

    # Result
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_minutes: float = 0.0
    exit_reason: str = ""  # PROFIT_TARGET, STOP_LOSS, TRAILING_STOP, EOD, FORCE

    # Classification
    won: bool = False
    is_bad_trade: bool = False
    bad_reasons: list[str] = Field(default_factory=list)

    # Lessons (auto-generated)
    lesson: str = ""


class TradeJournal:
    """Persistent trade journal with learning capabilities."""

    def __init__(self, journal_dir: Path | None = None):
        self._dir = journal_dir or _JOURNAL_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._today_file = self._dir / f"{date.today().isoformat()}.jsonl"
        self._entries: list[TradeEntry] = []
        self._load_today()

    def _load_today(self) -> None:
        """Load today's journal entries."""
        if self._today_file.exists():
            with open(self._today_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._entries.append(TradeEntry.model_validate_json(line))
                        except Exception:
                            pass
            logger.info("journal_loaded", date=date.today().isoformat(), entries=len(self._entries))

    def record(self, entry: TradeEntry) -> None:
        """Record a completed trade."""
        # Auto-classify time of day
        try:
            ts = datetime.fromisoformat(entry.timestamp)
            hour = ts.hour
            if hour < 10:
                entry.time_of_day = "open"
            elif hour < 12:
                entry.time_of_day = "midday"
            elif hour < 15:
                entry.time_of_day = "afternoon"
            else:
                entry.time_of_day = "power_hour"
            entry.day_of_week = ts.strftime("%A")
        except Exception:
            pass

        # Auto-generate lesson
        entry.lesson = self._generate_lesson(entry)

        # Append to file
        with open(self._today_file, "a") as f:
            f.write(entry.model_dump_json() + "\n")

        self._entries.append(entry)
        logger.info(
            "trade_journaled",
            id=entry.id,
            symbol=entry.symbol,
            pillar=entry.pillar,
            pnl=entry.pnl,
            won=entry.won,
            lesson=entry.lesson,
        )

    def _generate_lesson(self, entry: TradeEntry) -> str:
        """Auto-generate a lesson from this trade."""
        parts = []

        if entry.won:
            if entry.pnl_pct > 50:
                parts.append(f"Big winner ({entry.pnl_pct:.0f}%)")
            if entry.ai_confidence >= 80:
                parts.append("High AI confidence confirmed")
            if abs(entry.flow_bias) > 30:
                flow_dir = "bullish" if entry.flow_bias > 0 else "bearish"
                parts.append(f"Strong {flow_dir} flow aligned with direction")
        else:
            if entry.ai_confidence < 70:
                parts.append(f"Low confidence ({entry.ai_confidence}%) — should have skipped")
            if entry.exit_reason == "STOP_LOSS":
                parts.append("Hit hard stop — direction was wrong")
            if entry.hold_minutes < 5:
                parts.append("Stopped out too fast — entry timing was off")
            if entry.flow_bias != 0:
                flow_dir = "bullish" if entry.flow_bias > 0 else "bearish"
                if (entry.direction == "BULL" and entry.flow_bias < -20) or \
                   (entry.direction == "BEAR" and entry.flow_bias > 20):
                    parts.append(f"Traded AGAINST {flow_dir} flow — flow was right")
            if entry.is_bad_trade:
                parts.append(f"Bad trade: {', '.join(entry.bad_reasons)}")

        return "; ".join(parts) if parts else "Standard trade"

    def get_pattern_insights(self, symbol: str | None = None) -> dict[str, Any]:
        """Analyze recent trades for patterns.

        Returns insights like:
        - Best time of day for wins
        - Which pillars are winning/losing
        - Flow alignment correlation with wins
        - Confidence threshold that actually works
        """
        # Load last 5 days of data
        entries = self._load_recent_days(5)
        if symbol:
            entries = [e for e in entries if e.symbol == symbol]

        if not entries:
            return {"message": "No recent trade data"}

        total = len(entries)
        wins = [e for e in entries if e.won]
        losses = [e for e in entries if not e.won]

        insights: dict[str, Any] = {
            "total_trades": total,
            "win_rate": len(wins) / total * 100 if total > 0 else 0,
            "avg_winner": sum(e.pnl for e in wins) / len(wins) if wins else 0,
            "avg_loser": sum(e.pnl for e in losses) / len(losses) if losses else 0,
        }

        # Best pillar
        pillar_stats: dict[int, dict] = {}
        for e in entries:
            if e.pillar not in pillar_stats:
                pillar_stats[e.pillar] = {"wins": 0, "losses": 0, "pnl": 0}
            pillar_stats[e.pillar]["pnl"] += e.pnl
            if e.won:
                pillar_stats[e.pillar]["wins"] += 1
            else:
                pillar_stats[e.pillar]["losses"] += 1

        insights["pillar_performance"] = {
            f"P{k}": {
                "win_rate": f"{v['wins']/(v['wins']+v['losses'])*100:.0f}%",
                "total_pnl": f"${v['pnl']:+,.2f}",
                "trades": v["wins"] + v["losses"],
            }
            for k, v in sorted(pillar_stats.items())
        }

        # Confidence analysis
        high_conf = [e for e in entries if e.ai_confidence >= 75]
        low_conf = [e for e in entries if e.ai_confidence < 75]
        if high_conf:
            insights["high_confidence_win_rate"] = f"{sum(1 for e in high_conf if e.won)/len(high_conf)*100:.0f}%"
        if low_conf:
            insights["low_confidence_win_rate"] = f"{sum(1 for e in low_conf if e.won)/len(low_conf)*100:.0f}%"

        # Flow alignment
        flow_aligned = [e for e in entries if
                        (e.direction == "BULL" and e.flow_bias > 10) or
                        (e.direction == "BEAR" and e.flow_bias < -10)]
        flow_against = [e for e in entries if
                        (e.direction == "BULL" and e.flow_bias < -10) or
                        (e.direction == "BEAR" and e.flow_bias > 10)]
        if flow_aligned:
            insights["flow_aligned_win_rate"] = f"{sum(1 for e in flow_aligned if e.won)/len(flow_aligned)*100:.0f}%"
        if flow_against:
            insights["flow_against_win_rate"] = f"{sum(1 for e in flow_against if e.won)/len(flow_against)*100:.0f}%"

        # Time of day
        tod_stats: dict[str, dict] = {}
        for e in entries:
            tod = e.time_of_day or "unknown"
            if tod not in tod_stats:
                tod_stats[tod] = {"wins": 0, "losses": 0}
            if e.won:
                tod_stats[tod]["wins"] += 1
            else:
                tod_stats[tod]["losses"] += 1

        insights["time_of_day"] = {
            k: f"{v['wins']/(v['wins']+v['losses'])*100:.0f}% win rate ({v['wins']+v['losses']} trades)"
            for k, v in tod_stats.items()
            if v["wins"] + v["losses"] > 0
        }

        # Worst patterns (what to avoid)
        bad_trades = [e for e in entries if e.is_bad_trade]
        if bad_trades:
            all_reasons = []
            for e in bad_trades:
                all_reasons.extend(e.bad_reasons)
            from collections import Counter
            reason_counts = Counter(all_reasons).most_common(5)
            insights["top_bad_trade_reasons"] = [f"{r}: {c}x" for r, c in reason_counts]

        return insights

    def get_lessons_for_symbol(self, symbol: str, direction: str) -> list[str]:
        """Get relevant lessons before entering a trade.

        Called by the engine before placing a trade to check
        what we've learned about this symbol/direction combo.
        """
        entries = self._load_recent_days(5)
        relevant = [
            e for e in entries
            if e.symbol == symbol and e.direction == direction and e.lesson
        ]

        # Return the most recent lessons (losses first, they're more useful)
        losses_first = sorted(relevant, key=lambda e: (e.won, e.timestamp))
        return [e.lesson for e in losses_first[:5]]

    def _load_recent_days(self, days: int) -> list[TradeEntry]:
        """Load entries from the last N days."""
        entries = list(self._entries)  # today's entries
        for i in range(1, days):
            d = date.today() - __import__("datetime").timedelta(days=i)
            f = self._dir / f"{d.isoformat()}.jsonl"
            if f.exists():
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(TradeEntry.model_validate_json(line))
                            except Exception:
                                pass
        return entries

    def daily_summary(self) -> str:
        """Generate a human-readable daily summary."""
        if not self._entries:
            return "No trades recorded today."

        wins = [e for e in self._entries if e.won]
        losses = [e for e in self._entries if not e.won]
        total_pnl = sum(e.pnl for e in self._entries)

        lines = [
            f"📊 Trade Journal — {date.today().isoformat()}",
            f"Trades: {len(self._entries)} | Wins: {len(wins)} | Losses: {len(losses)}",
            f"Win Rate: {len(wins)/len(self._entries)*100:.0f}%",
            f"Total P/L: ${total_pnl:+,.2f}",
            "",
        ]

        if losses:
            lines.append("❌ Losses:")
            for e in losses:
                lines.append(f"  {e.symbol} P{e.pillar} {e.direction}: ${e.pnl:+,.2f} — {e.lesson}")

        if wins:
            lines.append("✅ Best wins:")
            top_wins = sorted(wins, key=lambda e: e.pnl, reverse=True)[:3]
            for e in top_wins:
                lines.append(f"  {e.symbol} P{e.pillar} {e.direction}: ${e.pnl:+,.2f}")

        insights = self.get_pattern_insights()
        if "top_bad_trade_reasons" in insights:
            lines.append("\n⚠️ Recurring mistakes:")
            for r in insights["top_bad_trade_reasons"]:
                lines.append(f"  {r}")

        return "\n".join(lines)
