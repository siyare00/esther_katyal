# Esther Trading

Autonomous options trading bot powered by AI debate, multi-pillar execution, and adaptive risk management.

## Architecture

```
Tradier Market Data → Black Swan Detector → Bias Engine → Quality Filter
→ Inversion Engine → AI Debate (Riki/Abi/Kage) → AI Sizing + Capital Recycler
→ Execute via 4 Pillars → Position Management → Risk Manager → Feedback Loop
```

## The 4 Pillars

| Pillar | Strategy | Trigger |
|--------|----------|---------|
| P1 | Iron Condors | Neutral bias (-20 to +20) |
| P2 | Bear Call Spreads | Strong bearish (< -60) |
| P3 | Bull Put Spreads | Strong bullish (> +60) |
| P4 | 0DTE Directional Scalps | High conviction (±40+) |

## Ticker Tiers

- **Tier 1** (0DTE): SPX, SPY, QQQ, IWM — all 4 pillars
- **Tier 2** (Weekly): GLD, SLV, USO, TLT — pillars 2-4
- **Tier 3** (Weekly): NVDA, TSLA, AAPL, AMZN — pillars 2-4

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

Three Claude-powered personalities debate every trade:
- **Riki** 🐂 — The eternal bull. Always finds reasons to go long.
- **Abi** 🐻 — The permanent bear. Always finds reasons to go short.
- **Kage** ⚖️ — The judge. Weighs both cases, makes the final call.

## Risk Management

- Per-tier position limits (5/3/3)
- Daily loss cap: 5% of account value
- Cooldown after consecutive losses
- Black Swan detector (VIX, SPX moves, volume anomalies)
- Automatic position force-close on RED status
