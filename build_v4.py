#!/usr/bin/env python3
"""Build COMPLETE_SOURCE_V4.py — concatenation of ALL Esther source files."""
from pathlib import Path
from datetime import datetime

ROOT = Path(".")
OUTPUT = ROOT / "COMPLETE_SOURCE_V4.py"

FILE_ORDER = [
    "config.yaml",
    "config-tradier.yaml",
    "esther/core/config.py",
    "esther/core/engine.py",
    "esther/data/tradier.py",
    "esther/data/alpaca.py",
    "esther/signals/bias_engine.py",
    "esther/signals/black_swan.py",
    "esther/signals/flow.py",
    "esther/signals/quality_filter.py",
    "esther/signals/inversion_engine.py",
    "esther/signals/levels.py",
    "esther/signals/regime.py",
    "esther/signals/calendar.py",
    "esther/signals/ifvg.py",
    "esther/signals/premarket.py",
    "esther/signals/reentry.py",
    "esther/signals/watchlist.py",
    "esther/signals/sage.py",
    "esther/ai/debate.py",
    "esther/ai/sizing.py",
    "esther/execution/pillars.py",
    "esther/execution/position_manager.py",
    "esther/execution/swing.py",
    "esther/execution/leap.py",
    "esther/risk/risk_manager.py",
    "esther/risk/journal.py",
    "scripts/run_live.py",
    "tests/test_bias_engine.py",
    "tests/test_pillars.py",
    "tests/test_quality_filter.py",
    "tests/test_risk_manager.py",
    "tests/test_premarket.py",
    "docs/strategy-reference.md",
]

header = '''"""
+======================================================================+
|                      ESTHER TRADING BOT V4                            |
|                Complete Source — %s                    |
|                                                                        |
|  Autonomous SPX/SPY/QQQ Options Trading System                        |
|  Based on @SuperLuckeee (Esther & Michael) strategies                 |
|  Built by Mercury for Shawn Katyal                                    |
|                                                                        |
|  5 PILLARS:                                                            |
|    P1 - Iron Condors (neutral, VIX > 25 sweet spot)                   |
|    P2 - Bear Call Spreads (bearish)                                    |
|    P3 - Bull Put Spreads (bullish)                                     |
|    P4 - 0DTE Directional Scalps (high conviction)                     |
|    P5 - Butterfly Spreads (small accounts, moderate conviction)       |
|                                                                        |
|  AI DEBATE: Riki (Bull) + Abi (Bear) + Kage (Final) + Kimi Research  |
|  BROKERS: Alpaca (ETF/stock) + Tradier (SPX index options)           |
|  DATA: Unusual Whales (flow) + FRED (macro) + Tradier/Alpaca         |
+======================================================================+
"""

''' % datetime.now().strftime("%Y-%m-%d %H:%M")

with open(OUTPUT, "w") as out:
    out.write(header)
    total_lines = 0
    
    for filepath in FILE_ORDER:
        full = ROOT / filepath
        if not full.exists():
            out.write(f"\n# !! MISSING: {filepath}\n\n")
            continue
        content = full.read_text()
        lines = content.count("\n") + 1
        total_lines += lines
        sep = "=" * 70
        out.write(f"\n# {sep}\n")
        out.write(f"# FILE: {filepath} ({lines} lines)\n")
        out.write(f"# {sep}\n\n")
        if filepath.endswith((".yaml", ".md")):
            out.write('"""\n' + content + '\n"""\n')
        else:
            out.write(content)
            out.write("\n")

    out.write(f"\n# Total: {total_lines} lines across {len(FILE_ORDER)} files\n")

print(f"Built COMPLETE_SOURCE_V4.py - {total_lines} lines")
