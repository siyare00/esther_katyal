# Esther Trading

Autonomous options trading bot powered by AI debate, multi-pillar execution, and adaptive risk management.

## Architecture

```
Tradier Market Data → Black Swan Detector → Bias Engine → Quality Filter
→ Inversion Engine → AI Debate (Kimi/Riki/Abi/Kage) → AI Sizing + Capital Recycler
→ Execute via 5 Pillars → Position Management → Risk Manager → Feedback Loop
```

## The 5 Pillars

| Pillar | Strategy | Trigger |
|--------|----------|---------|
| P1 | Iron Condors | Neutral bias (-20 to +20) |
| P2 | Bear Call Spreads | Strong bearish (< -60) |
| P3 | Bull Put Spreads | Strong bullish (> +60) |
| P4 | 0DTE Directional Scalps | High conviction (±40+) |
| P5 | Butterfly Spreads | Moderate conviction (small accounts) |

## Ticker Tiers

- **Tier 1** (0DTE): SPX, SPY, QQQ, IWM — all 5 pillars
- **Tier 2** (Weekly): GLD, SLV, USO, TLT — pillars 2-5
- **Tier 3** (Weekly): NVDA, TSLA, AAPL, AMZN — pillars 2-5

## Setup

```bash
# Clone and install
cd esther-trading
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your API keys

# Edit config.yaml for your risk parameters

# Run live
python scripts/run_live.py

# Run backtest
python scripts/run_backtest.py
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TRADIER_API_KEY` | Tradier API key |
| `TRADIER_ACCOUNT_ID` | Tradier account ID |
| `ANTHROPIC_API_KEY` | Anthropic API key for AI debate/sizing |
| `TRADIER_SANDBOX` | Set to `true` for paper trading |

## AI Debate System

Esther's multi-backend AI debate system utilizes five specialized agents for comprehensive trade analysis and decision-making:

- **Kimi** 💡 — The Researcher/Challenger. Provides initial research, identifies potential flaws, and challenges Riki and Abi's positions.
- **Riki** 🐂 — The eternal bull. Always finds reasons to go long.
- **Abi** 🐻 — The permanent bear. Always finds reasons to go short.
- **Neo** 🤖 — The trading agent. Monitors and executes trades based on Kage's final decision, ensuring self-healing and continuous operation.
- **Kage** ⚖️ — The judge. Weighs all arguments from Kimi, Riki, and Abi, makes the final call.

**Sage** (Intel Officer) also feeds premarket/intraday/EOD scans and intel to all debate agents.

## Risk Management

- Per-tier position limits (5/3/3)
- Daily loss cap: **2%** of account value
- Cooldown after consecutive losses
- Black Swan detector (VIX, SPX moves, volume anomalies)
- Automatic position force-close on RED status
