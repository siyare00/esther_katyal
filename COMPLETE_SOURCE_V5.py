#!/usr/bin/env python3
"""
================================================================================
ESTHER TRADING BOT — COMPLETE SOURCE V5
Generated: 2026-04-01 01:11:46
================================================================================

AUTONOMOUS OPTIONS TRADING BOT
  - 12 Tickers: SPY, QQQ, IWM, SPX, GLD, SLV, USO, TLT, NVDA, TSLA, AAPL, AMZN
  - 5 Pillars: Iron Condors, Bear Calls, Bull Puts, Scalps, Butterflies
  - 5 AI Agents: Sage (Intel), Kimi (Research), Riki (Bull), Abi (Bear), Kage (Judge)
  - Debate Flow: Sage → Kimi → Riki → Abi → Kimi → Kage (consensus required)
  - Inversion Engine: Flips direction on EVERY loss (consecutive_loss_trigger: 1)
  - Broker: Tradier (SPX/XSP index options + all ETFs/stocks)
  - Multi-backend AI: Ollama (local), Groq (remote), Anthropic (Claude)

CHANGES FROM V4:
  v5: Full audit + critical bug fixes
    - 5-agent debate (debate_with_kimi) instead of legacy 3-agent
    - Trade journal fix (position.contracts -> position.quantity)
    - Black swan force-close feeds inversion engine
    - Per-ticker position limit enforced
    - Sage wired into engine (premarket/intraday/eod)
    - Journal get_lessons() feeds debate agents
    - VIX fallback chain: Alpaca -> Tradier -> default 20.0
  v6: Multi-backend AI (Ollama/Groq/Anthropic) + Groq provider
  v7: Tradier full 12-ticker config + morning cron launcher
================================================================================
"""


#===============================================================================
# SECTION: CONFIGURATION
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: config.yaml (194 lines)
#-------------------------------------------------------------------------------

# Esther Trading Configuration
# All configurable parameters — nothing hardcoded in the code

tickers:
  tier1:
    symbols: ["SPY", "QQQ", "IWM"]  # SPX/XSP removed — Alpaca doesn't support index options
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]
  tier2:
    symbols: ["GLD", "SLV", "USO", "TLT"]
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]
  tier3:
    symbols: ["NVDA", "TSLA", "AAPL", "AMZN"]
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]

# Bias Engine
bias:
  weights:
    vwap: 0.15
    ema_cross: 0.10
    rsi: 0.10
    vix: 0.10
    price_action: 0.10
    flow: 0.25
    regime: 0.10
    levels: 0.10
  pillar_ranges:
    p1_low: -35        # ICs work in wider range — VIX 30 = IC sweet spot
    p1_high: 35
    p2_threshold: -25   # Bear calls activate earlier — bearish is bearish
    p3_threshold: 25    # Bull puts activate earlier
    p4_threshold: 25    # Scalps fire more often — paper mode, let it rip
    p5_threshold: 30    # Butterflies at moderate conviction
  ema:
    fast: 9
    slow: 21
  rsi:
    period: 14
    overbought: 70
    oversold: 30

# Black Swan Detector
black_swan:
  vix_red: 35
  vix_yellow: 25
  spx_drop_red: -3.0      # percent
  spx_drop_yellow: -1.5   # percent
  volume_std_threshold: 3.0

# Quality Filter
quality:
  max_spread_pct: 0.20    # reject if bid-ask spread > 20% of mid
  min_volume:
    tier1: 500
    tier2: 100
    tier3: 200
  iv_rank:
    spread_min: 20
    spread_max: 80
    iron_condor_min: 25         # lowered — VIX 30 IS high IV, let ICs trade

# Pillar Execution
pillars:
  p1:  # Iron Condors — take profit FAST
    short_delta: 0.12           # further OTM = higher win rate (was 0.16)
    wing_width: 10
    profit_target_pct: 0.75     # take 75% profit
    stop_loss_multiplier: 1.5   # cut at 1.5x credit (was 2.0x)
    expire_worthless: true
  p2:  # Bear Call Spreads
    short_delta: 0.20           # slightly further OTM (was 0.25)
    wing_width: 10
    profit_target_pct: 0.75     # take 75% profit
    stop_loss_multiplier: 1.5
  p3:  # Bull Put Spreads
    short_delta: 0.20
    wing_width: 10
    profit_target_pct: 0.75
    stop_loss_multiplier: 1.5
  p4:  # 0DTE Directional Scalps — TIGHT STOPS
    delta_range: [0.40, 0.55]
    initial_trail_pct: 0.10       # 10% trail from entry (was 20% — too loose)
    tight_trail_pct: 0.05         # 5% after profit (was 10%)
    tighten_after_gain_pct: 0.25  # tighten sooner — after 25% gain (was 50%)
    tiered_stops: true
    stop_tiers: 3
    stop_loss_multiplier: 1.5     # max loss = 1.5x entry (was 2.0x)
  p5:  # Butterfly Spreads (small accounts)
    wing_width_etf: 5       # SPY/QQQ wing width
    wing_width_index: 10    # SPX wing width
    profit_target_pct: 1.00  # 100% — double your money
    stop_loss_pct: 0.50      # cut at 50% loss of debit

# Risk Management — PAPER MODE: NO LIMITS
risk:
  max_positions:
    tier1: 20
    tier2: 10
    tier3: 10
  daily_loss_cap_pct: 1.00    # 100% — no kill-switch on paper
  max_risk_per_ticker_pct: 0.50  # 50% per ticker on paper
  cooldown_consecutive_losses: 0
  cooldown_minutes: 0

# Inversion Engine — INVERT ON EVERY LOSS
inversion:
  consecutive_loss_trigger: 1   # flip on first loss
  state_file: "data/inversion_state.json"

# Position Management
positions:
  eod_close_minutes_before: 15   # close P1-P3 at 3:45 PM ET

# AI Debate
ai:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024
  temperature: 0.7
  ai_backend: "ollama"
  ollama_model: "qwen2.5-coder:7b"
  ollama_base_url: "http://127.0.0.1:11434/v1"

# Sizing — Configured for $10K/day target with compounding
sizing:
  kelly_fraction: 0.50         # half-Kelly — aggressive on paper
  min_contracts: 50            # minimum 50 contracts per trade
  max_contracts: 200           # scale to 200 contracts
  win_streak_multiplier: 1.30  # 30% compound after each win
  loss_streak_divisor: 1.0     # don't reduce on loss — just invert

# Engine Schedule
engine:
  scan_interval_seconds: 60    # scan every 1 min — faster entries
  market_open: "09:30"
  market_close: "16:00"
  pre_market_scan: "09:15"
  eod_cleanup: "15:50"
  first_trade_after: "09:35"   # trade from open — catch opening moves
  max_trades_per_day: 50       # no limit on paper

# Daily Target & Compounding Strategy
# Phase 1: $80K → $200K (4-6 weeks) — 60 spreads, ~$5-7K/day
# Phase 2: $200K → $500K (6-8 weeks) — 100 spreads, $10K+/day
# Phase 3: $500K+ — 200 spreads, $15-25K/day, start taking profits
daily_targets:
  target_daily_pnl: 10000      # $10K/day goal
  compound_mode: true           # reinvest ALL profits until Phase 2
  take_profit_threshold: 200000 # after $200K, take 30% of daily profits out
  take_profit_pct: 0.30         # withdraw 30% of daily profits above threshold
  max_daily_trades: 50          # no limit on paper
  preferred_strategy: "ic_ladder"  # IC Ladder = highest win rate for daily bread

# Logging
logging:
  level: "INFO"
  file: "logs/esther.log"

# --- NEW SECTIONS ---

# Key Levels Tracking
levels:
  track_pm_low: true
  track_prev_close: true
  track_nwog: true
  fibonacci_levels: [0.382, 0.500, 0.618]

# Market Regime Detection (SMA crossover)
regime:
  sma_fast: 20
  sma_slow: 50
  lookback_days: 100

# Order Flow Integration
flow:
  provider: "tradier"
  min_premium: 100000        # $100K minimum flow to track

# Economic Calendar
calendar:
  track_fomc: true
  track_cpi: true
  track_ppi: true
  track_nfp: true
  track_opex: true
  reduce_size_on_event: 0.5  # 50% size on event days

# Ladder (multiple ICs at different strikes)
ladder:
  enabled: true
  max_rungs: 3             # up to 3 ICs at different strikes
  size_by_direction: true   # size more on the side matching bias

#-------------------------------------------------------------------------------
# FILE: config-tradier.yaml (89 lines)
#-------------------------------------------------------------------------------

# Esther Trading Configuration — Tradier (All 12 Tickers)
# Full ticker coverage: ETFs + Commodities/Bonds + Mega-cap + Index

tickers:
  tier1:
    symbols: ["SPY", "QQQ", "IWM", "SPX"]
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]
  tier2:
    symbols: ["GLD", "SLV", "USO", "TLT"]
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]
  tier3:
    symbols: ["NVDA", "TSLA", "AAPL", "AMZN"]
    expiry: "0dte"
    pillars: [1, 2, 3, 4, 5]

# Inherit everything else from defaults — only override what differs
# Bias, quality, pillars, risk, AI, sizing all use same defaults

# Risk Management — separate budget for index trades
risk:
  max_positions:
    tier1: 20
    tier2: 10
    tier3: 10
  daily_loss_cap_pct: 1.00      # no kill-switch on paper
  max_risk_per_ticker_pct: 0.75  # 75% — go big on paper
  cooldown_consecutive_losses: 0
  cooldown_minutes: 0

# Inversion Engine — INVERT ON FIRST LOSS
inversion:
  consecutive_loss_trigger: 1
  state_file: "data/inversion_state_tradier.json"

# Engine Schedule
engine:
  scan_interval_seconds: 180    # 3 min scans — reduces Claude API calls
  market_open: "09:30"
  market_close: "16:00"
  pre_market_scan: "09:15"
  eod_cleanup: "15:50"
  first_trade_after: "09:35"    # trade from open
  max_trades_per_day: 50        # no limit

# Quality — match Alpaca aggressive settings
quality:
  max_spread_pct: 0.25
  min_volume:
    tier1: 100
    tier2: 50
    tier3: 50
  iv_rank:
    spread_min: 20
    spread_max: 80
    iron_condor_min: 25

# Pillar ranges — wider for more trades
bias:
  pillar_ranges:
    p1_low: -35
    p1_high: 35
    p2_threshold: -25
    p3_threshold: 25
    p4_threshold: 25

# AI Debate
ai:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024
  temperature: 0.7
  ai_backend: "ollama"
  ollama_model: "qwen2.5-coder:7b"
  ollama_base_url: "http://127.0.0.1:11434/v1"

# Sizing — AGGRESSIVE for paper
sizing:
  kelly_fraction: 0.50
  min_contracts: 10
  max_contracts: 50
  win_streak_multiplier: 1.30
  loss_streak_divisor: 1.0

# Logging — separate log file
logging:
  level: "INFO"
  file: "logs/esther-tradier.log"

#-------------------------------------------------------------------------------
# FILE: pyproject.toml (40 lines)
#-------------------------------------------------------------------------------

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "esther-trading"
version = "0.1.0"
description = "Autonomous options trading bot with AI debate and multi-pillar execution"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pandas>=2.2",
    "numpy>=1.26",
    "anthropic>=0.40",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "pyyaml>=6.0",
    "structlog>=24.1",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "ruff>=0.3",
]

[tool.setuptools.packages.find]
include = ["esther*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100


#===============================================================================
# SECTION: CORE - Config Models
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/core/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: esther/core/config.py (297 lines)
#-------------------------------------------------------------------------------

"""Configuration loader for Esther Trading.

Loads config.yaml and merges with environment variables.
All configurable parameters live in config.yaml — code reads from here, never hardcodes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Load .env file from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class EnvSettings(BaseSettings):
    """Environment variables — secrets that don't go in config.yaml."""

    tradier_api_key: str = Field(default="")
    tradier_account_id: str = Field(default="")
    tradier_sandbox: bool = Field(default=True)
    tradier_live_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    unusual_whales_api_key: str = Field(default="")

    # Alpaca Paper Account 1
    alpaca_paper1_key: str = Field(default="")
    alpaca_paper1_secret: str = Field(default="")
    alpaca_paper1_url: str = Field(default="https://paper-api.alpaca.markets/v2")

    # Alpaca Paper Account 2
    alpaca_paper2_key: str = Field(default="")
    alpaca_paper2_secret: str = Field(default="")
    alpaca_paper2_url: str = Field(default="https://paper-api.alpaca.markets/v2")

    # Which Alpaca account to use: "paper1" or "paper2"
    alpaca_broker: str = Field(default="paper2")

    model_config = {"env_prefix": "", "case_sensitive": False}


class TierConfig(BaseModel):
    symbols: list[str]
    expiry: str
    pillars: list[int]


class BiasWeights(BaseModel):
    vwap: float = 0.15
    ema_cross: float = 0.10
    rsi: float = 0.10
    vix: float = 0.10
    price_action: float = 0.10
    flow: float = 0.25
    regime: float = 0.10
    levels: float = 0.10


class BiasConfig(BaseModel):
    weights: BiasWeights = BiasWeights()
    pillar_ranges: dict[str, float] = {
        "p1_low": -20, "p1_high": 20,
        "p2_threshold": -60, "p3_threshold": 60,
        "p4_threshold": 40,
    }
    ema: dict[str, int] = {"fast": 9, "slow": 21}
    rsi: dict[str, int] = {"period": 14, "overbought": 70, "oversold": 30}


class BlackSwanConfig(BaseModel):
    vix_red: float = 35.0
    vix_yellow: float = 25.0
    spx_drop_red: float = -3.0
    spx_drop_yellow: float = -1.5
    volume_std_threshold: float = 3.0


class QualityConfig(BaseModel):
    max_spread_pct: float = 0.20
    min_volume: dict[str, int] = {"tier1": 500, "tier2": 100, "tier3": 200}
    iv_rank: dict[str, float] = {"spread_min": 30, "spread_max": 70, "iron_condor_min": 50}


class PillarP1Config(BaseModel):
    short_delta: float = 0.16
    wing_width: int = 10
    profit_target_pct: float = 0.75
    stop_loss_multiplier: float = 2.0
    expire_worthless: bool = True


class PillarP2Config(BaseModel):
    short_delta: float = 0.25
    wing_width: int = 10
    profit_target_pct: float = 0.75
    stop_loss_multiplier: float = 2.0


class PillarP3Config(BaseModel):
    short_delta: float = 0.25
    wing_width: int = 10
    profit_target_pct: float = 0.75
    stop_loss_multiplier: float = 2.0


class PillarP4Config(BaseModel):
    delta_range: list[float] = [0.40, 0.55]
    initial_trail_pct: float = 0.20
    tight_trail_pct: float = 0.10
    tighten_after_gain_pct: float = 0.50
    tiered_stops: bool = True
    stop_tiers: int = 3


class PillarP5Config(BaseModel):
    wing_width_etf: int = 5
    wing_width_index: int = 10
    profit_target_pct: float = 1.00
    stop_loss_pct: float = 0.50


class PillarsConfig(BaseModel):
    p1: PillarP1Config = PillarP1Config()
    p2: PillarP2Config = PillarP2Config()
    p3: PillarP3Config = PillarP3Config()
    p4: PillarP4Config = PillarP4Config()
    p5: PillarP5Config = PillarP5Config()


class RiskConfig(BaseModel):
    max_positions: dict[str, int] = {"tier1": 5, "tier2": 3, "tier3": 3}
    daily_loss_cap_pct: float = 0.02  # 2% kill-switch (per SuperLuckeee sovereign instruction set)
    max_risk_per_ticker_pct: float = 0.03  # 3% default, higher for index-only configs
    cooldown_consecutive_losses: int = 2
    cooldown_minutes: int = 30
    max_positions_per_ticker: int = 2  # Prevent stacking 5+ positions in the same ticker


class InversionConfig(BaseModel):
    consecutive_loss_trigger: int = 3
    state_file: str = "data/inversion_state.json"


class AIConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 1024
    temperature: float = 0.7
    ai_backend: str = "ollama"  # "ollama", "groq", or "anthropic"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_key: str = ""  # Set via GROQ_API_KEY env or config


class SizingConfig(BaseModel):
    kelly_fraction: float = 0.25
    min_contracts: int = 1
    max_contracts: int = 50
    win_streak_multiplier: float = 1.15
    loss_streak_divisor: float = 1.25


class EngineConfig(BaseModel):
    scan_interval_seconds: int = 120
    market_open: str = "09:30"
    market_close: str = "16:00"
    pre_market_scan: str = "09:15"
    eod_cleanup: str = "15:45"


# --- NEW CONFIG MODELS ---


class LevelsConfig(BaseModel):
    """Configuration for key price level tracking."""

    track_pm_low: bool = True
    track_prev_close: bool = True
    track_nwog: bool = True
    fibonacci_levels: list[float] = [0.382, 0.500, 0.618]


class RegimeConfig(BaseModel):
    """Configuration for market regime detection (SMA crossover)."""

    sma_fast: int = 20
    sma_slow: int = 50
    lookback_days: int = 100


class FlowConfig(BaseModel):
    """Configuration for order flow data integration."""

    provider: str = "tradier"
    min_premium: int = 100000


class CalendarConfig(BaseModel):
    """Configuration for economic calendar awareness."""

    track_fomc: bool = True
    track_cpi: bool = True
    track_ppi: bool = True
    track_nfp: bool = True
    track_opex: bool = True
    reduce_size_on_event: float = 0.5


class LadderConfig(BaseModel):
    """Configuration for laddered IC entries at different strikes."""

    enabled: bool = True
    max_rungs: int = 3
    size_by_direction: bool = True


class EstherConfig(BaseModel):
    """Top-level configuration model."""

    tickers: dict[str, TierConfig] = {}
    bias: BiasConfig = BiasConfig()
    black_swan: BlackSwanConfig = BlackSwanConfig()
    quality: QualityConfig = QualityConfig()
    pillars: PillarsConfig = PillarsConfig()
    risk: RiskConfig = RiskConfig()
    inversion: InversionConfig = InversionConfig()
    ai: AIConfig = AIConfig()
    sizing: SizingConfig = SizingConfig()
    engine: EngineConfig = EngineConfig()
    levels: LevelsConfig = LevelsConfig()
    regime: RegimeConfig = RegimeConfig()
    flow: FlowConfig = FlowConfig()
    calendar: CalendarConfig = CalendarConfig()
    ladder: LadderConfig = LadderConfig()


def load_config(config_path: str | Path | None = None) -> EstherConfig:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config.yaml. Defaults to project root.

    Returns:
        Fully validated EstherConfig instance.
    """
    if config_path is None:
        config_path = _PROJECT_ROOT / "config.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    return EstherConfig.model_validate(raw)


def get_env() -> EnvSettings:
    """Load environment settings (API keys, etc.)."""
    return EnvSettings()


# Convenience: module-level singletons (lazy-loaded)
_config: EstherConfig | None = None
_env: EnvSettings | None = None


def set_config(cfg: EstherConfig) -> None:
    """Override the global config singleton (used for multi-broker setups)."""
    global _config
    _config = cfg


def config() -> EstherConfig:
    """Get or create the global config singleton."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def env() -> EnvSettings:
    """Get or create the global env singleton."""
    global _env
    if _env is None:
        _env = get_env()
    return _env


#===============================================================================
# SECTION: CORE - Engine
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/core/engine.py (1260 lines)
#-------------------------------------------------------------------------------

"""Main Orchestrator Engine — The Brain of Esther.

Chains all modules together in the main trading loop:
    1. Black Swan check → gate all activity
    2. Bias Engine → directional scoring
    3. Inversion Engine → self-correction
    4. Quality Filter → option quality gate
    5. AI Debate → Riki/Abi/Kage argue the trade
    6. AI Sizing → Kelly + capital recycler
    7. Pillar Executor → build and submit orders
    8. Position Manager → track and manage open positions
    9. Risk Manager → enforce limits throughout

Runs every scan_interval_seconds during market hours (9:30 AM - 4:00 PM ET).
Pre-market scan at 9:15 AM. EOD cleanup at 3:45 PM.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import structlog
from zoneinfo import ZoneInfo

from esther.core.config import EstherConfig, TierConfig, load_config, get_env, set_config
from esther.data.tradier import TradierClient, Quote, OptionQuote
from esther.data.alpaca import AlpacaClient
from esther.signals.black_swan import BlackSwanDetector, BlackSwanStatus, ThreatLevel
from esther.signals.bias_engine import BiasEngine, BiasScore
from esther.signals.quality_filter import QualityFilter, QualityCheck
from esther.signals.inversion_engine import InversionEngine, TradeResult
from esther.ai.debate import AIDebate, DebateInput, DebateVerdict
from esther.ai.sizing import AISizer, SizingInput, SizingResult
from esther.execution.pillars import PillarExecutor, SpreadOrder
from esther.execution.position_manager import PositionManager, Position, PositionStatus
from esther.risk.risk_manager import RiskManager
from esther.risk.journal import TradeJournal, TradeEntry
from esther.signals.reentry import ReentryGuard
from esther.signals.sage import Sage

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class TickerJob:
    """Encapsulates processing state for a single ticker within a scan cycle."""

    def __init__(self, symbol: str, tier_name: str, tier_cfg: TierConfig):
        self.symbol = symbol
        self.tier_name = tier_name  # "tier1", "tier2", "tier3"
        self.tier_cfg = tier_cfg
        self.quote: Quote | None = None
        self.chain: list[OptionQuote] = []
        self.bars: list = []
        self.bias: BiasScore | None = None
        self.expiration: str = ""
        self.vix_level: float = 0.0


class EstherEngine:
    """Main orchestrator that runs the full trading pipeline.

    Lifecycle:
        engine = EstherEngine(config_path="config.yaml", sandbox=True)
        await engine.start()   # blocks until market close or shutdown
        await engine.stop()

    The engine manages its own broker client (AlpacaClient or TradierClient) and all sub-components.
    """

    def __init__(
        self,
        config_path: str | None = None,
        sandbox: bool | None = None,
        broker: str = "alpaca",
    ):
        self._cfg = load_config(config_path)
        set_config(self._cfg)  # Override singleton so all components use this config
        self._env = get_env()
        self._sandbox = sandbox if sandbox is not None else self._env.tradier_sandbox
        self._broker = broker  # "alpaca" or "tradier"

        # Components — initialized in start()
        self._client: TradierClient | AlpacaClient | None = None
        self._black_swan: BlackSwanDetector | None = None
        self._bias_engine: BiasEngine | None = None
        self._quality_filter: QualityFilter | None = None
        self._inversion: InversionEngine | None = None
        self._debate: AIDebate | None = None
        self._sizer: AISizer | None = None
        self._executor: PillarExecutor | None = None
        self._position_mgr: PositionManager | None = None
        self._risk_mgr: RiskManager | None = None
        self._journal = TradeJournal()
        self._reentry = ReentryGuard(required_candles=2)
        self._sage = Sage()
        self._last_sage_scan: float = 0.0  # timestamp of last intraday sage scan

        # State
        self._running = False
        self._current_swan_status: BlackSwanStatus | None = None
        self._account_balance: float = 0.0
        self._scan_count: int = 0
        self._pre_market_done: bool = False
        self._eod_done: bool = False

        # Win/loss streak tracking per symbol for the capital recycler
        self._streaks: dict[str, int] = {}  # symbol → current streak (+ wins, - losses)
        self._recent_results: dict[str, tuple[int, int]] = {}  # symbol → (wins, losses)

        # Duplicate debate prevention: tracks "symbol:pillar" debated this scan cycle
        self._debated_this_cycle: set[str] = set()

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the engine. Blocks until market close or explicit stop."""
        logger.info("engine_starting", broker=self._broker, sandbox=self._sandbox)

        if self._broker == "alpaca":
            ctx = AlpacaClient()
        else:
            ctx = TradierClient(sandbox=self._sandbox)

        async with ctx as client:
            self._client = client
            self._init_components()

            # Fetch initial account balance
            try:
                balance_data = await client.get_account_balance()
                self._account_balance = float(balance_data.get("total_equity", 100_000))
                logger.info("account_balance", balance=self._account_balance)
            except Exception as e:
                logger.warning("balance_fetch_failed", error=str(e))
                self._account_balance = 100_000  # fallback for sandbox

            self._risk_mgr = RiskManager(self._position_mgr, self._account_balance, risk_cfg=self._cfg.risk)
            self._running = True

            logger.info("engine_started", broker=self._broker, balance=self._account_balance)

            try:
                await self._main_loop()
            except asyncio.CancelledError:
                logger.info("engine_cancelled")
            except Exception as e:
                logger.error("engine_fatal_error", error=str(e), exc_info=True)
            finally:
                await self._shutdown()

    async def stop(self) -> None:
        """Signal the engine to stop gracefully."""
        logger.info("engine_stop_requested")
        self._running = False

    def _init_components(self) -> None:
        """Initialize all trading components."""
        self._black_swan = BlackSwanDetector(self._client)
        self._bias_engine = BiasEngine()
        self._quality_filter = QualityFilter()
        self._inversion = InversionEngine()
        self._debate = AIDebate()
        self._sizer = AISizer()
        self._executor = PillarExecutor(self._client)
        self._position_mgr = PositionManager(self._client)

    # ── Main Loop ────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        """Core loop: wait for market hours, scan tickers, manage positions."""
        while self._running:
            now_et = datetime.now(ET)
            current_time = now_et.time()

            market_open = time(9, 30)
            market_close = time(16, 0)
            pre_market = time(9, 15)
            eod_time = time(15, 45)

            # ── Before Market ────────────────────────────────────
            if current_time < pre_market:
                wait_seconds = self._seconds_until(pre_market, now_et)
                logger.info("waiting_for_pre_market", wait_min=round(wait_seconds / 60, 1))
                await self._interruptible_sleep(min(wait_seconds, 60))
                continue

            # ── Pre-Market Scan (9:15 AM) ────────────────────────
            if pre_market <= current_time < market_open and not self._pre_market_done:
                await self._pre_market_scan()
                self._pre_market_done = True
                continue

            # ── EOD Cleanup (3:45 PM) ────────────────────────────
            if current_time >= eod_time and not self._eod_done:
                await self._eod_cleanup()
                self._eod_done = True
                continue

            # ── After Market Close ───────────────────────────────
            if current_time >= market_close:
                if not self._eod_done:
                    await self._eod_cleanup()
                    self._eod_done = True
                logger.info("market_closed")
                self._running = False
                break

            # ── During Market Hours ──────────────────────────────
            if market_open <= current_time < market_close:
                await self._scan_cycle()

            # Sleep until next scan
            await self._interruptible_sleep(self._cfg.engine.scan_interval_seconds)

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that can be interrupted by stop()."""
        try:
            end = asyncio.get_event_loop().time() + seconds
            while self._running and asyncio.get_event_loop().time() < end:
                await asyncio.sleep(min(1.0, max(0, end - asyncio.get_event_loop().time())))
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _seconds_until(target: time, now: datetime) -> float:
        """Seconds from now until target time today."""
        target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
        if target_dt <= now:
            return 0
        return (target_dt - now).total_seconds()

    # ── Pre-Market ───────────────────────────────────────────────

    async def _pre_market_scan(self) -> None:
        """Pre-market scan at 9:15 AM ET.

        - Reset daily state
        - Check Black Swan
        - Fetch today's expirations for all tickers
        - Log summary
        """
        logger.info("pre_market_scan_start")

        # Reset daily state
        self._black_swan.reset_volume_history()
        if self._risk_mgr:
            self._risk_mgr.reset_daily(self._account_balance)
        self._scan_count = 0
        self._eod_done = False

        # Pre-market Black Swan check
        try:
            status = await self._black_swan.check()
            self._current_swan_status = status
            if status.should_close_all:
                logger.error("pre_market_red_alert", triggers=status.triggers)
            else:
                logger.info("pre_market_swan_check", level=status.level.value, vix=status.vix)
        except Exception as e:
            logger.error("pre_market_swan_failed", error=str(e))

        # Sage pre-market intelligence scan
        try:
            sage_intel = await self._sage.premarket_scan()
            logger.info("sage_premarket_complete", brief_len=len(sage_intel.intel_brief))
        except Exception as e:
            logger.warning("sage_premarket_failed", error=str(e))

        # Log ticker universe
        total_symbols = sum(
            len(tc.symbols) for tc in self._cfg.tickers.values()
        )
        logger.info("pre_market_scan_complete", total_symbols=total_symbols)

    # ── EOD Cleanup ──────────────────────────────────────────────

    async def _eod_cleanup(self) -> None:
        """End-of-day cleanup at 3:45 PM ET.

        - Close all remaining positions
        - Generate daily risk report
        - Log summary
        """
        logger.info("eod_cleanup_start")

        # Force close all open positions
        if self._position_mgr and self._position_mgr.open_positions:
            closed = await self._position_mgr.force_close_all("EOD_CLEANUP")
            for pos in closed:
                if self._risk_mgr:
                    self._risk_mgr.record_trade_result(pos)
                self._record_streak(pos)
                # Feed result to inversion engine (same as mid-session close)
                if self._inversion:
                    self._inversion.record_result(TradeResult(
                        symbol=pos.symbol,
                        direction="bull" if pos.direction == "BULL" else "bear",
                        pnl=pos.unrealized_pnl,
                        won=pos.unrealized_pnl > 0,
                    ))
            logger.info("eod_positions_closed", count=len(closed))

        # Sage EOD scan
        try:
            sage_eod = await self._sage.eod_scan()
            logger.info("sage_eod_complete", brief_len=len(sage_eod.intel_brief))
        except Exception as e:
            logger.warning("sage_eod_failed", error=str(e))

        # Place GTC overnight orders for tomorrow
        try:
            await self._place_gtc_overnight_orders()
        except Exception as e:
            logger.error("gtc_overnight_failed", error=str(e))

        # Generate daily report
        if self._risk_mgr:
            report = self._risk_mgr.generate_daily_report()
            logger.info(
                "daily_report",
                trades=report.total_trades,
                win_rate=f"{report.win_rate:.0%}",
                pnl=report.total_pnl,
                max_drawdown=report.max_drawdown,
                events=report.risk_events,
            )

        # Journal daily summary + pattern insights
        try:
            journal_summary = self._journal.daily_summary()
            insights = self._journal.get_pattern_insights()
            logger.info("journal_daily_summary", summary=journal_summary)
            logger.info("journal_insights", insights=insights)
        except Exception as e:
            logger.warning("journal_summary_failed", error=str(e))

        logger.info("eod_cleanup_complete")

    # ── Scan Cycle ───────────────────────────────────────────────

    async def _scan_cycle(self) -> None:
        """One full scan cycle: check swan, scan tickers by tier, manage positions."""
        self._scan_count += 1
        self._debated_this_cycle.clear()
        logger.info("scan_cycle_start", cycle=self._scan_count)

        # Step 0: Sage intraday scan (throttled — every 5 minutes)
        import time as _time_mod
        now_ts = _time_mod.time()
        if now_ts - self._last_sage_scan >= 300:  # 5 minutes
            try:
                await self._sage.intraday_scan()
                self._last_sage_scan = now_ts
                logger.info("sage_intraday_scan_complete")
            except Exception as e:
                logger.warning("sage_intraday_scan_failed", error=str(e))

        # Step 1: Black Swan check
        try:
            self._current_swan_status = await self._black_swan.check()
        except Exception as e:
            logger.error("swan_check_failed", error=str(e))
            # Assume YELLOW if we can't check
            self._current_swan_status = None

        # If RED → force close everything and shut down
        if self._current_swan_status and self._current_swan_status.should_close_all:
            logger.error(
                "black_swan_red",
                triggers=self._current_swan_status.triggers,
            )
            if self._risk_mgr:
                self._risk_mgr.trigger_force_close("BLACK_SWAN_RED")
            if self._position_mgr:
                closed = await self._position_mgr.force_close_all("BLACK_SWAN_RED")
                for pos in closed:
                    if self._risk_mgr:
                        self._risk_mgr.record_trade_result(pos)
                    self._record_streak(pos)
                    # Feed result to inversion engine
                    if self._inversion:
                        self._inversion.record_result(TradeResult(
                            symbol=pos.symbol,
                            direction="bull" if pos.direction == "BULL" else "bear",
                            pnl=pos.unrealized_pnl,
                            won=pos.unrealized_pnl > 0,
                        ))
            return  # Skip rest of cycle

        # Step 2: Update existing positions (check profit targets, stops, trails)
        if self._position_mgr:
            try:
                closed_positions = await self._position_mgr.update_positions()
                for pos in closed_positions:
                    if self._risk_mgr:
                        self._risk_mgr.record_trade_result(pos)
                    self._record_streak(pos)
                    # Feed result to inversion engine
                    if self._inversion:
                        self._inversion.record_result(TradeResult(
                            symbol=pos.symbol,
                            direction="bull" if pos.direction == "BULL" else "bear",
                            pnl=pos.unrealized_pnl,
                            won=pos.unrealized_pnl > 0,
                        ))
            except Exception as e:
                logger.error("position_update_failed", error=str(e))

        # Step 3: If risk manager shut us down, skip new entries
        if self._risk_mgr and self._risk_mgr.is_shutdown:
            logger.warning("risk_shutdown_active", reason="daily limit hit")
            return

        # Step 4: Process tickers by tier (Tier 1 first, then 2, then 3)
        tier_order = ["tier1", "tier2", "tier3"]
        for tier_name in tier_order:
            tier_cfg = self._cfg.tickers.get(tier_name)
            if not tier_cfg:
                continue

            for symbol in tier_cfg.symbols:
                if not self._running:
                    return

                try:
                    await self._process_ticker(symbol, tier_name, tier_cfg)
                except Exception as e:
                    logger.error(
                        "ticker_processing_failed",
                        symbol=symbol,
                        tier=tier_name,
                        error=str(e),
                        exc_info=True,
                    )
                    # One ticker failing doesn't stop the loop
                    continue

        logger.info(
            "scan_cycle_complete",
            cycle=self._scan_count,
            open_positions=self._position_mgr.get_position_count() if self._position_mgr else 0,
            daily_pnl=self._position_mgr.get_daily_pnl() if self._position_mgr else 0,
        )

    # ── Per-Ticker Pipeline ──────────────────────────────────────

    async def _process_ticker(
        self, symbol: str, tier_name: str, tier_cfg: TierConfig
    ) -> None:
        """Full pipeline for a single ticker.

        Steps:
        1. Fetch quote and bars
        2. Compute bias score
        3. Apply inversion engine
        4. Determine eligible pillars (intersection of bias + tier config)
        5. Fetch option chain
        6. Quality filter
        7. AI debate
        8. AI sizing
        9. Build and submit order
        10. Register position
        """
        log = logger.bind(symbol=symbol, tier=tier_name)

        # ── 1. Fetch Market Data ─────────────────────────────────
        try:
            quotes = await self._client.get_quotes([symbol])
            if not quotes:
                log.warning("no_quote_data")
                return
            quote = quotes[0]
        except Exception as e:
            log.error("quote_fetch_failed", error=str(e))
            return

        # Fetch recent bars for bias calculation (need at least 25)
        # SPX/XSP don't have bars on some brokers — use SPY as proxy
        bars_symbol = "SPY" if symbol in ("SPX", "XSP", "SPXW") else symbol
        try:
            today = date.today()
            start_date = today - timedelta(days=60)
            bars = await self._client.get_bars(
                bars_symbol, interval="daily", start=start_date, end=today
            )
            if bars_symbol != symbol:
                log.info("bars_proxy_used", proxy=bars_symbol, original=symbol, count=len(bars))
        except Exception as e:
            log.error("bars_fetch_failed", error=str(e))
            return

        if len(bars) < 25:
            log.warning("insufficient_bars", count=len(bars))
            return

        # Get VIX level from swan status or fetch fresh
        vix_level = 20.0  # default
        if self._current_swan_status:
            vix_level = self._current_swan_status.vix
        if not vix_level or vix_level == 0:
            vix_level = await self._fetch_vix()
        log.info("vix_for_ticker", vix=vix_level, source="swan" if self._current_swan_status else "fetch/default")

        # ── 1b. Fetch Order Flow (Unusual Whales + Tradier fallback) ──
        flow_entries = None
        try:
            flow_entries = await self._bias_engine.flow_analyzer.get_flow(symbol)
            if flow_entries:
                log.info("flow_data_loaded", symbol=symbol, entries=len(flow_entries))
        except Exception as e:
            log.warning("flow_fetch_failed", symbol=symbol, error=str(e))

        # ── 2. Compute Bias Score ────────────────────────────────
        raw_bias = self._bias_engine.compute_bias(
            symbol=symbol,
            bars=bars,
            vix_level=vix_level,
            current_price=quote.last,
            flow_entries=flow_entries,
        )

        # ── 3. Apply Inversion Engine ────────────────────────────
        adjusted_score = self._inversion.get_adjusted_bias(symbol, raw_bias.score)
        # Rebuild active pillars with adjusted score
        active_pillars_from_bias = self._bias_engine._determine_pillars(adjusted_score)

        # ── 4. Determine Eligible Pillars ────────────────────────
        # Intersection of bias-activated pillars and tier-allowed pillars
        tier_allowed = set(tier_cfg.pillars)
        eligible_pillars = [p for p in active_pillars_from_bias if p in tier_allowed]

        if not eligible_pillars:
            log.debug("no_eligible_pillars", bias=adjusted_score, tier_allowed=list(tier_allowed))
            return

        # Determine direction from adjusted bias
        if adjusted_score > 20:
            direction = "BULL"
        elif adjusted_score < -20:
            direction = "BEAR"
        else:
            direction = "NEUTRAL"

        log.info(
            "ticker_eligible",
            bias_raw=raw_bias.score,
            bias_adjusted=adjusted_score,
            direction=direction,
            pillars=eligible_pillars,
        )

        # ── 5. Fetch Option Chain ────────────────────────────────
        expiration = await self._get_expiration(symbol, tier_cfg.expiry)
        if not expiration:
            log.warning("no_expiration_found", expiry_type=tier_cfg.expiry)
            return

        try:
            if isinstance(self._client, AlpacaClient):
                chain = await self._client.get_option_chain_compat(symbol, expiration)
            else:
                chain = await self._client.get_option_chain(symbol, expiration)
        except Exception as e:
            log.error("chain_fetch_failed", error=str(e))
            return

        if not chain:
            log.warning("empty_chain", expiration=expiration)
            return

        # ── 6-10. Execute for each eligible pillar ───────────────
        for pillar in eligible_pillars:
            if not self._running:
                return

            try:
                await self._execute_pillar(
                    symbol=symbol,
                    tier_name=tier_name,
                    pillar=pillar,
                    direction=direction,
                    chain=chain,
                    expiration=expiration,
                    quote=quote,
                    bias_score=adjusted_score,
                    vix_level=vix_level,
                    bars=bars,
                    flow_entries=flow_entries,
                )
            except Exception as e:
                log.error(
                    "pillar_execution_failed",
                    pillar=pillar,
                    error=str(e),
                    exc_info=True,
                )
                continue

    async def _execute_pillar(
        self,
        symbol: str,
        tier_name: str,
        pillar: int,
        direction: str,
        chain: list[OptionQuote],
        expiration: str,
        quote: Quote,
        bias_score: float,
        vix_level: float,
        bars: list,
        flow_entries=None,
    ) -> None:
        """Execute a single pillar strategy for a ticker.

        Steps 5b-10 of the pipeline: re-entry check → quality → debate → size → execute → register.
        """
        log = logger.bind(symbol=symbol, pillar=pillar)

        # ── 5b. Re-entry Guard (candle confirmation after loss) ──
        if not self._reentry.can_reenter(symbol, direction):
            # Try to confirm with recent 5m bars
            try:
                bars_symbol = "SPY" if symbol in ("SPX", "XSP", "SPXW") else symbol
                today = date.today()
                start_date = today - timedelta(days=1)
                recent_bars = await self._client.get_bars(bars_symbol, interval="5min", start=start_date, end=today)
                if recent_bars and self._reentry.check_candles(symbol, recent_bars):
                    log.info("reentry_candle_confirmed", symbol=symbol, direction=direction)
                else:
                    log.info("reentry_blocked_no_confirmation", symbol=symbol, direction=direction)
                    return
            except Exception:
                log.info("reentry_blocked_bars_failed", symbol=symbol, direction=direction)
                return

        # ── 6. Quality Filter ────────────────────────────────────
        # Sample a representative option from the chain to check quality
        sample_option = self._pick_sample_option(chain, pillar, direction)
        if not sample_option:
            log.debug("no_sample_option_for_quality")
            return

        # Estimate IV rank (use mid_iv from greeks if available)
        iv_rank = self._estimate_iv_rank(chain)

        quality = self._quality_filter.check(
            option=sample_option,
            tier=tier_name,
            pillar=pillar,
            iv_rank=iv_rank,
        )

        if not quality.passed:
            log.info("quality_rejected", reasons=quality.reasons, score=quality.quality_score)
            return

        # ── 6b. Duplicate Debate Prevention ──────────────────────
        debate_key = f"{symbol}:{pillar}"
        if debate_key in self._debated_this_cycle:
            log.debug("debate_skipped_duplicate", key=debate_key)
            return
        self._debated_this_cycle.add(debate_key)

        # ── 7. AI Debate ─────────────────────────────────────────
        # Build debate input from available data
        closes = [b.close for b in bars]
        rsi_val = self._bias_engine._compute_rsi(
            __import__("numpy").array(closes), 14
        )

        # Build flow context for debate
        flow_bias = 0.0
        flow_summary = ""
        if flow_entries:
            flow_bias = self._bias_engine.flow_analyzer.get_flow_bias_sync(flow_entries)
            try:
                summary = self._bias_engine.flow_analyzer.analyze_flow(flow_entries)
                biggest_premium = max((e.premium for e in flow_entries), default=0)
                flow_summary = (
                    f"Put/Call Premium Ratio: {summary.put_call_ratio:.2f}, "
                    f"Net Premium: ${summary.net_premium:+,.0f}, "
                    f"Call Vol: {summary.call_volume:,}, Put Vol: {summary.put_volume:,}, "
                    f"Biggest Single Trade: ${biggest_premium:,.0f}, "
                    f"Unusual Trades: {len(summary.unusual_trades)}"
                )
            except Exception:
                flow_summary = f"Flow entries: {len(flow_entries)}, bias: {flow_bias:+.1f}"

        # Get Sage's broader market intelligence for the debate
        sage_intel = self._sage.get_intel_for_debate() or None

        # Get journal lessons so agents learn from recent mistakes
        journal_lessons = self._journal.get_lessons()

        debate_input = DebateInput(
            symbol=symbol,
            current_price=quote.last,
            bias_score=bias_score,
            vix_level=vix_level,
            rsi=rsi_val,
            daily_change_pct=quote.change_pct,
            volume=quote.volume,
            flow_bias=flow_bias,
            flow_summary=flow_summary,
            sage_intel=sage_intel if sage_intel else None,
            journal_lessons=journal_lessons,
        )

        try:
            verdict = await self._debate.debate_with_kimi(debate_input)
        except Exception as e:
            log.error("debate_failed", error=str(e))
            return

        # Check debate verdict aligns with pillar direction
        if not self._verdict_aligns(verdict, pillar, direction):
            log.info(
                "debate_misaligned",
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                pillar=pillar,
                direction=direction,
            )
            return

        # Minimum confidence threshold — SuperLuckeee: "only take high-conviction trades"
        if verdict.confidence < 70:
            log.info("debate_low_confidence", confidence=verdict.confidence, min_required=70)
            return

        # ── 8. Risk Check (pre-trade) ────────────────────────────
        # Build the order first to know the max_risk
        order = await self._build_pillar_order(
            symbol=symbol,
            pillar=pillar,
            direction=direction,
            chain=chain,
            expiration=expiration,
            quantity=1,  # placeholder, sizing comes next
        )

        if not order:
            log.info("order_build_failed")
            return

        # ── 9. AI Sizing ─────────────────────────────────────────
        streak = self._streaks.get(symbol, 0)
        wins, losses = self._recent_results.get(symbol, (0, 0))

        daily_pnl = self._position_mgr.get_daily_pnl() if self._position_mgr else 0.0
        daily_loss_cap = self._risk_mgr.daily_loss_cap if self._risk_mgr else self._account_balance * 0.02

        sizing_input = SizingInput(
            symbol=symbol,
            account_balance=self._account_balance,
            max_risk_per_trade=self._account_balance * 0.02,  # 2% default
            confidence=verdict.confidence,
            recent_wins=wins,
            recent_losses=losses,
            current_streak=streak,
            vix_level=vix_level,
            pillar=pillar,
            credit_or_debit=order.net_price,
            max_loss_per_contract=order.max_loss if order.max_loss > 0 else order.net_price * 100,
            daily_pnl=daily_pnl,
            daily_loss_cap=daily_loss_cap,
        )

        try:
            sizing = await self._sizer.calculate_size(sizing_input)
        except Exception as e:
            log.error("sizing_failed", error=str(e))
            sizing = SizingResult(
                contracts=1, max_risk=order.max_loss,
                kelly_raw=0, kelly_adjusted=0,
                recycler_multiplier=1.0, reasoning="Sizing failed, defaulting to 1 contract",
            )

        # ── Hard per-ticker limit (belt + suspenders) ────────────
        existing_for_ticker = len(self._position_mgr.get_positions_for_symbol(symbol))
        max_per_ticker = self._cfg.risk.max_positions_per_ticker
        if existing_for_ticker >= max_per_ticker:
            log.info("engine_ticker_limit_blocked", symbol=symbol, existing=existing_for_ticker, max=max_per_ticker)
            return

        # ── Risk check with actual size ──────────────────────────
        total_max_risk = sizing.contracts * (order.max_loss if order.max_loss > 0 else order.net_price * 100)

        risk_check = self._risk_mgr.can_open_position(
            symbol=symbol,
            tier=tier_name,
            max_risk=total_max_risk,
        )

        if not risk_check.approved:
            log.info("risk_rejected", reason=risk_check.reason)
            return

        # ── 10. Rebuild order with correct quantity and submit ───
        final_order = await self._build_pillar_order(
            symbol=symbol,
            pillar=pillar,
            direction=direction,
            chain=chain,
            expiration=expiration,
            quantity=sizing.contracts,
        )

        if not final_order:
            log.warning("final_order_build_failed")
            return

        # Submit order
        try:
            result = await self._executor.submit_order(final_order)
            order_id = str(result.get("order", {}).get("id", ""))
        except Exception as e:
            log.error("order_submit_failed", error=str(e))
            return

        # Register position with position manager
        position = self._position_mgr.open_position(
            order=final_order,
            order_id=order_id,
            direction=direction,
            tier=tier_name,
            confidence=verdict.confidence,
        )

        log.info(
            "trade_executed",
            position_id=position.id,
            contracts=sizing.contracts,
            max_risk=total_max_risk,
            confidence=verdict.confidence,
            verdict=verdict.verdict,
            sizing_reasoning=sizing.reasoning,
        )

    # ── Helper Methods ───────────────────────────────────────────

    async def _fetch_vix(self) -> float:
        """Fetch VIX level with fallback chain: Alpaca → Tradier live → default.

        Returns a non-zero VIX value. Falls back to 20.0 if all sources fail.
        """
        # Try Alpaca first
        try:
            vix_quotes = await self._client.get_quotes(["VIX"])
            if vix_quotes and vix_quotes[0].last > 0:
                return vix_quotes[0].last
        except Exception as e:
            logger.debug("vix_alpaca_failed", error=str(e))

        # Try Tradier live API as backup
        try:
            tradier_client = TradierClient(sandbox=False)
            async with tradier_client as tc:
                vix_quotes = await tc.get_quotes(["VIX"])
                if vix_quotes and vix_quotes[0].last > 0:
                    logger.info("vix_tradier_fallback", vix=vix_quotes[0].last)
                    return vix_quotes[0].last
        except Exception as e:
            logger.debug("vix_tradier_failed", error=str(e))

        # Last resort: safe default
        logger.warning("vix_all_sources_failed", fallback=20.0)
        return 20.0

    async def _get_expiration(self, symbol: str, expiry_type: str) -> str | None:
        """Get the appropriate expiration date for a ticker.

        Args:
            symbol: Ticker symbol.
            expiry_type: "0dte" or "weekly".

        Returns:
            Expiration date string (YYYY-MM-DD) or None.
        """
        try:
            expirations = await self._client.get_option_expirations(symbol)
        except Exception as e:
            logger.error("expiration_fetch_failed", symbol=symbol, error=str(e))
            return None

        if not expirations:
            return None

        today = date.today().isoformat()

        if expiry_type == "0dte":
            # Use today's expiration if available, otherwise nearest
            if today in expirations:
                return today
            # Find the nearest future expiration
            future = [e for e in expirations if e >= today]
            return future[0] if future else None

        elif expiry_type == "weekly":
            # Find the nearest Friday expiration (or nearest weekly)
            future = [e for e in expirations if e >= today]
            if not future:
                return None
            # Skip today, get the next available weekly
            for exp in future:
                exp_date = date.fromisoformat(exp)
                # Prefer expirations 2-7 days out for weeklies
                days_out = (exp_date - date.today()).days
                if 1 <= days_out <= 7:
                    return exp
            # Fallback to nearest future
            return future[0] if future else None

        return expirations[0] if expirations else None

    async def _build_pillar_order(
        self,
        symbol: str,
        pillar: int,
        direction: str,
        chain: list[OptionQuote],
        expiration: str,
        quantity: int,
    ) -> SpreadOrder | None:
        """Build the appropriate order for a pillar.

        Args:
            symbol: Underlying symbol.
            pillar: Pillar number (1-4).
            direction: "BULL", "BEAR", or "NEUTRAL".
            chain: Option chain.
            expiration: Expiration date.
            quantity: Number of contracts.

        Returns:
            SpreadOrder or None if construction fails.
        """
        if pillar == 1:
            return await self._executor.build_iron_condor(
                symbol=symbol, chain=chain,
                quantity=quantity, expiration=expiration,
            )
        elif pillar == 2:
            return await self._executor.build_bear_call(
                symbol=symbol, chain=chain,
                quantity=quantity, expiration=expiration,
            )
        elif pillar == 3:
            return await self._executor.build_bull_put(
                symbol=symbol, chain=chain,
                quantity=quantity, expiration=expiration,
            )
        elif pillar == 4:
            return await self._executor.build_directional_scalp(
                symbol=symbol, chain=chain,
                direction=direction, quantity=quantity,
                expiration=expiration,
            )
        elif pillar == 5:
            # Get current price for ATM determination
            current_price = 0.0
            if chain:
                strikes = sorted(set(o.strike for o in chain))
                current_price = strikes[len(strikes) // 2] if strikes else 0.0
            return await self._executor.build_butterfly(
                symbol=symbol, chain=chain,
                direction=direction, current_price=current_price,
                quantity=quantity, expiration=expiration,
            )
        return None

    def _verdict_aligns(
        self, verdict: DebateVerdict, pillar: int, direction: str
    ) -> bool:
        """Check if the AI debate verdict aligns with the pillar strategy.

        P1 (Iron Condor): NEUTRAL verdict is ideal, BULL/BEAR also OK
        P2 (Bear Call): needs BEAR or NEUTRAL verdict
        P3 (Bull Put): needs BULL or NEUTRAL verdict
        P4 (Directional): BULL verdict for bull scalp, BEAR for bear scalp
        """
        v = verdict.verdict

        if pillar == 1:
            # Iron condors work in any non-extreme condition
            return True

        if pillar == 2:
            # Bear call spread — want bearish or neutral
            return v in ("BEAR", "NEUTRAL")

        if pillar == 3:
            # Bull put spread — want bullish or neutral
            return v in ("BULL", "NEUTRAL")

        if pillar == 4:
            # Directional scalp — must match direction
            if direction == "BULL":
                return v == "BULL"
            elif direction == "BEAR":
                return v == "BEAR"

        if pillar == 5:
            # Butterfly — needs directional conviction
            if direction == "BULL":
                return v in ("BULL", "NEUTRAL")
            elif direction == "BEAR":
                return v in ("BEAR", "NEUTRAL")
            return True

        return False

    def _pick_sample_option(
        self,
        chain: list[OptionQuote],
        pillar: int,
        direction: str,
    ) -> OptionQuote | None:
        """Pick a representative option from the chain for quality checks.

        Uses the same logic as the pillar strike selection to get a realistic
        sample of what we'd actually trade.
        """
        from esther.data.tradier import OptionType
        from esther.execution.pillars import find_closest_delta

        if pillar == 1:
            # Iron condor — check the short put as representative
            return find_closest_delta(chain, 0.16, OptionType.PUT)
        elif pillar == 2:
            # Bear call — check the short call
            return find_closest_delta(chain, 0.25, OptionType.CALL)
        elif pillar == 3:
            # Bull put — check the short put
            return find_closest_delta(chain, 0.25, OptionType.PUT)
        elif pillar == 4:
            # Directional — check the target option
            opt_type = OptionType.CALL if direction == "BULL" else OptionType.PUT
            return find_closest_delta(chain, 0.47, opt_type)
        elif pillar == 5:
            # Butterfly — check ATM option
            opt_type = OptionType.CALL if direction == "BULL" else OptionType.PUT
            return find_closest_delta(chain, 0.50, opt_type)

        return None

    def _estimate_iv_rank(self, chain: list[OptionQuote]) -> float | None:
        """Estimate IV rank from the option chain's implied volatilities.

        This is a rough estimate — uses the average mid_iv from ATM options
        as a proxy. A proper implementation would compare to historical IV.

        Returns:
            Estimated IV rank (0-100) or None.
        """
        ivs = []
        for opt in chain:
            if opt.greeks and opt.greeks.mid_iv > 0:
                ivs.append(opt.greeks.mid_iv)

        if not ivs:
            return None

        # Use average IV as a rough percentile estimate
        avg_iv = sum(ivs) / len(ivs)
        # Map typical IV range (0.10 - 0.80) to 0-100 rank
        # This is approximate — proper IV rank needs historical data
        rank = min(100, max(0, (avg_iv - 0.10) / 0.70 * 100))
        return round(rank, 1)

    def _record_streak(self, position: Position) -> None:
        """Update win/loss streak, journal, and re-entry guard after a position closes."""
        symbol = position.symbol
        won = position.unrealized_pnl > 0
        pnl = position.unrealized_pnl
        direction = getattr(position, "direction", "UNKNOWN")

        # Update streak
        current = self._streaks.get(symbol, 0)
        if won:
            self._streaks[symbol] = max(1, current + 1) if current > 0 else 1
        else:
            self._streaks[symbol] = min(-1, current - 1) if current < 0 else -1

        # Update recent results
        wins, losses = self._recent_results.get(symbol, (0, 0))
        if won:
            self._recent_results[symbol] = (wins + 1, losses)
        else:
            self._recent_results[symbol] = (wins, losses + 1)

        # Re-entry guard: block same direction after loss, allow inversion
        if not won:
            self._reentry.record_loss(symbol, direction, pnl)

        # Journal: record the trade with full context
        try:
            # Pull Sage intel snapshot for flow context at close time
            sage_data = self._sage.get_intel_for_debate() if self._sage.latest else {}
            flow_bias_at_close = sage_data.get("flow_bias", 0.0) if sage_data else 0.0

            # Extract debate context from position metadata if available
            ai_confidence = getattr(position, "debate_confidence", 0)
            ai_verdict = getattr(position, "ai_verdict", "")
            bias_score_val = getattr(position, "bias_score", 0.0)

            entry = TradeEntry(
                id=position.id,
                date=date.today().isoformat(),
                timestamp=datetime.now().isoformat(),
                symbol=symbol,
                pillar=position.pillar,
                direction=direction,
                entry_price=position.entry_price,
                contracts=position.quantity,
                exit_price=position.current_value,
                pnl=pnl,
                pnl_pct=(pnl / (position.entry_price * position.quantity * 100) * 100) if position.entry_price > 0 and position.quantity > 0 else 0,
                exit_reason=position.status.value if hasattr(position.status, "value") else str(position.status),
                won=won,
                vix_level=self._current_swan_status.vix if self._current_swan_status else 0,
                ai_confidence=ai_confidence,
                ai_verdict=ai_verdict,
                bias_score=bias_score_val,
                flow_bias=flow_bias_at_close,
            )
            self._journal.record(entry)
        except Exception as e:
            logger.warning("journal_record_failed", error=str(e))

    # ── GTC Overnight Orders ────────────────────────────────────────

    async def _place_gtc_overnight_orders(self) -> None:
        """Place GTC (Good Till Cancel) orders before market close.

        Runs during EOD cleanup. Analyzes tomorrow's setup and places
        limit orders at favorable prices that persist overnight.
        GTC orders fill at open if price is right.

        Stores pending GTC orders in data/gtc_orders.json for tracking.
        """
        logger.info("gtc_overnight_start")

        if not self._executor or not self._client:
            logger.warning("gtc_no_executor_or_client")
            return

        gtc_orders: list[dict[str, Any]] = []
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        gtc_file = data_dir / "gtc_orders.json"

        # Process tier1 symbols for GTC orders (most liquid)
        tier_cfg = self._cfg.tickers.get("tier1")
        if not tier_cfg:
            return

        for symbol in tier_cfg.symbols:
            try:
                # Get current quote for price reference
                quotes = await self._client.get_quotes([symbol])
                if not quotes or quotes[0].last <= 0:
                    continue
                current_price = quotes[0].last

                # Get tomorrow's expiration
                expirations = await self._client.get_option_expirations(symbol)
                if not expirations:
                    continue

                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                # Find nearest future expiration
                future_exps = [e for e in expirations if e >= tomorrow]
                if not future_exps:
                    continue
                target_exp = future_exps[0]

                # Fetch option chain for target expiration
                if isinstance(self._client, AlpacaClient):
                    chain = await self._client.get_option_chain_compat(symbol, target_exp)
                else:
                    chain = await self._client.get_option_chain(symbol, target_exp)

                if not chain:
                    continue

                # Build an iron condor at better-than-market prices (10% improvement)
                order = await self._executor.build_iron_condor(
                    symbol=symbol, chain=chain, quantity=1, expiration=target_exp,
                )

                if order and order.net_price > 0:
                    # Set GTC and improve the limit price by 10%
                    improved_price = round(order.net_price * 1.10, 2)
                    order.net_price = improved_price
                    order.time_in_force = "gtc"

                    try:
                        result = await self._executor.submit_order(order)
                        order_id = str(result.get("order", {}).get("id", ""))

                        gtc_entry = {
                            "symbol": symbol,
                            "pillar": 1,
                            "order_id": order_id,
                            "expiration": target_exp,
                            "net_price": improved_price,
                            "time_in_force": "gtc",
                            "placed_at": datetime.now().isoformat(),
                            "status": "pending",
                        }
                        gtc_orders.append(gtc_entry)

                        logger.info(
                            "gtc_order_placed",
                            symbol=symbol,
                            order_id=order_id,
                            price=improved_price,
                            expiration=target_exp,
                        )
                    except Exception as e:
                        logger.error("gtc_order_submit_failed", symbol=symbol, error=str(e))

            except Exception as e:
                logger.error("gtc_symbol_failed", symbol=symbol, error=str(e))
                continue

        # Save GTC orders to state file
        existing_orders = []
        if gtc_file.exists():
            try:
                existing_orders = json.loads(gtc_file.read_text())
            except (json.JSONDecodeError, Exception):
                existing_orders = []

        # Merge: keep recent pending orders, add new ones
        all_orders = existing_orders + gtc_orders
        # Only keep orders from last 7 days
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        all_orders = [o for o in all_orders if o.get("placed_at", "") >= cutoff]

        gtc_file.write_text(json.dumps(all_orders, indent=2))
        logger.info("gtc_overnight_complete", new_orders=len(gtc_orders), total_stored=len(all_orders))

    async def _shutdown(self) -> None:
        """Clean shutdown: close positions, generate report."""
        logger.info("engine_shutting_down")

        if not self._eod_done and self._position_mgr:
            await self._eod_cleanup()

        self._running = False
        logger.info("engine_shutdown_complete")


#===============================================================================
# SECTION: DATA - Alpaca Client
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/data/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: esther/data/alpaca.py (944 lines)
#-------------------------------------------------------------------------------

"""Alpaca Broker Client.

Async client for the Alpaca Trading API — account info, quotes, option chains,
order execution (single-leg and multi-leg), positions, and portfolio history.
Drop-in alternative to TradierClient for order execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from esther.core.config import env
from esther.data.tradier import Quote, OptionQuote, OptionGreeks, OptionType, Bar

logger = structlog.get_logger(__name__)


# ── Pydantic Models ─────────────────────────────────────────────


class AlpacaAccount(BaseModel):
    """Alpaca account summary."""

    id: str = ""
    status: str = ""
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    portfolio_value: float = 0.0
    long_market_value: float = 0.0
    short_market_value: float = 0.0
    daytrade_count: int = 0
    daytrading_buying_power: float = 0.0


class AlpacaQuote(BaseModel):
    """Quote for a stock or option."""

    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0


class AlpacaOptionContract(BaseModel):
    """Option contract from the chain."""

    id: str = ""
    symbol: str = ""
    name: str = ""
    underlying_symbol: str = ""
    type: str = ""  # call or put
    strike_price: float = 0.0
    expiration_date: str = ""
    status: str = ""
    tradable: bool = False
    close_price: float | None = None
    open_interest: int | None = None


class AlpacaPosition(BaseModel):
    """Open position."""

    asset_id: str = ""
    symbol: str = ""
    qty: float = 0.0
    side: str = ""
    market_value: float = 0.0
    cost_basis: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_plpc: float = 0.0
    current_price: float = 0.0
    avg_entry_price: float = 0.0
    asset_class: str = ""


class AlpacaOrder(BaseModel):
    """Order details."""

    id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: str = ""
    type: str = ""
    status: str = ""
    filled_qty: str = ""
    filled_avg_price: str | None = None
    limit_price: str | None = None
    order_class: str = ""
    created_at: str = ""
    legs: list[dict[str, Any]] = Field(default_factory=list)


class AlpacaPortfolioHistory(BaseModel):
    """Portfolio P&L history."""

    timestamp: list[int] = Field(default_factory=list)
    equity: list[float] = Field(default_factory=list)
    profit_loss: list[float] = Field(default_factory=list)
    profit_loss_pct: list[float] = Field(default_factory=list)
    base_value: float = 0.0
    timeframe: str = ""


# ── Client ───────────────────────────────────────────────────────


class AlpacaClient:
    """Async Alpaca Trading API client with rate limiting and retries.

    Usage:
        async with AlpacaClient() as client:
            acct = await client.get_account()
            positions = await client.get_positions()

    Or without context manager (creates/closes client per call):
        client = AlpacaClient()
        acct = await client.get_account()
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    RATE_LIMIT_DELAY = 0.1

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        broker: str | None = None,
    ):
        _env = env()
        _broker = broker or _env.alpaca_broker  # "paper1" or "paper2"

        if api_key and api_secret:
            self.api_key = api_key
            self.api_secret = api_secret
        elif _broker == "paper1":
            self.api_key = _env.alpaca_paper1_key
            self.api_secret = _env.alpaca_paper1_secret
            self.base_url = base_url or _env.alpaca_paper1_url
        else:  # paper2 default
            self.api_key = _env.alpaca_paper2_key
            self.api_secret = _env.alpaca_paper2_secret
            self.base_url = base_url or _env.alpaca_paper2_url

        if base_url:
            self.base_url = base_url
        elif not hasattr(self, "base_url"):
            self.base_url = "https://paper-api.alpaca.markets/v2"

        # Data API base URL for market data (snapshots, options)
        self.data_url = "https://data.alpaca.markets/v2"
        self.options_data_url = "https://data.alpaca.markets/v1beta1"

        self._client: httpx.AsyncClient | None = None
        self._owns_client = False
        self._last_request_time: float = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def __aenter__(self) -> AlpacaClient:
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(30.0),
        )
        self._owns_client = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(30.0),
            )
            self._owns_client = True
        return self._client

    async def _rate_limit(self) -> None:
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < self.RATE_LIMIT_DELAY:
                await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Make an API request with retries and rate limiting."""
        client = await self._ensure_client()
        delay = self.RETRY_DELAY

        for attempt in range(self.MAX_RETRIES):
            await self._rate_limit()

            try:
                if method == "GET":
                    resp = await client.get(url, params=params)
                elif method == "POST":
                    resp = await client.post(url, json=json_data)
                elif method == "DELETE":
                    resp = await client.delete(url, params=params)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    logger.warning("alpaca_rate_limited", retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(retry_after)
                    delay *= 2
                    continue

                if resp.status_code >= 500:
                    logger.warning("alpaca_server_error", status=resp.status_code, attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                resp.raise_for_status()

                # DELETE /positions can return 204 with no body
                if resp.status_code == 204:
                    return {}

                return resp.json()

            except httpx.TimeoutException:
                logger.warning("alpaca_timeout", attempt=attempt)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

        raise RuntimeError(f"Max retries ({self.MAX_RETRIES}) exceeded for {url}")

    # ── Account ──────────────────────────────────────────────────

    async def get_account(self) -> AlpacaAccount:
        """Get account balance, buying power, equity.

        GET /v2/account
        """
        data = await self._request("GET", f"{self.base_url}/account")
        assert isinstance(data, dict)

        return AlpacaAccount(
            id=data.get("id", ""),
            status=data.get("status", ""),
            equity=float(data.get("equity", 0)),
            cash=float(data.get("cash", 0)),
            buying_power=float(data.get("buying_power", 0)),
            portfolio_value=float(data.get("portfolio_value", 0)),
            long_market_value=float(data.get("long_market_value", 0)),
            short_market_value=float(data.get("short_market_value", 0)),
            daytrade_count=int(data.get("daytrade_count", 0)),
            daytrading_buying_power=float(data.get("daytrading_buying_power", 0)),
        )

    # ── Quotes ───────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> AlpacaQuote:
        """Get last price, bid, ask for a stock.

        Uses Alpaca Data API: GET /v2/stocks/{symbol}/snapshot
        For options, uses /v1beta1/options/snapshots/{symbol}
        """
        # Detect if this is an option symbol (OCC format like SPY260327C00640000)
        if len(symbol) > 10 and any(c in symbol for c in ("C", "P")):
            return await self._get_option_quote(symbol)

        data = await self._request(
            "GET", f"{self.data_url}/stocks/{symbol}/snapshot", params={"feed": "iex"}
        )
        assert isinstance(data, dict)

        latest_trade = data.get("latestTrade", {})
        latest_quote = data.get("latestQuote", {})
        daily_bar = data.get("dailyBar", {})

        return AlpacaQuote(
            symbol=symbol,
            last=float(latest_trade.get("p", 0)),
            bid=float(latest_quote.get("bp", 0)),
            ask=float(latest_quote.get("ap", 0)),
            high=float(daily_bar.get("h", 0)),
            low=float(daily_bar.get("l", 0)),
            volume=int(daily_bar.get("v", 0)),
        )

    async def _get_option_quote(self, symbol: str) -> AlpacaQuote:
        """Get quote for an option symbol via snapshots endpoint."""
        data = await self._request(
            "GET",
            f"{self.options_data_url}/options/snapshots/{symbol}",
        )
        assert isinstance(data, dict)

        latest_trade = data.get("latestTrade", {})
        latest_quote = data.get("latestQuote", {})

        return AlpacaQuote(
            symbol=symbol,
            last=float(latest_trade.get("p", 0)),
            bid=float(latest_quote.get("bp", 0)),
            ask=float(latest_quote.get("ap", 0)),
        )

    # ── Option Chain ─────────────────────────────────────────────

    async def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        option_type: str | None = None,
        limit: int = 1000,
    ) -> list[AlpacaOptionContract]:
        """Fetch option contracts for a symbol and expiration.

        GET /v2/options/contracts?underlying_symbol={symbol}&expiration_date={expiration}
        """
        params: dict[str, Any] = {
            "underlying_symbols": symbol,
            "expiration_date": expiration,
            "limit": limit,
        }
        if option_type:
            params["type"] = option_type

        data = await self._request(
            "GET", f"{self.base_url}/options/contracts", params=params
        )
        assert isinstance(data, dict)

        contracts_data = data.get("option_contracts", [])
        if not contracts_data:
            contracts_data = data.get("options", [])

        results = []
        for c in contracts_data:
            results.append(AlpacaOptionContract(
                id=c.get("id", ""),
                symbol=c.get("symbol", ""),
                name=c.get("name", ""),
                underlying_symbol=c.get("underlying_symbol", ""),
                type=c.get("type", ""),
                strike_price=float(c.get("strike_price", 0)),
                expiration_date=c.get("expiration_date", ""),
                status=c.get("status", ""),
                tradable=c.get("tradable", False),
                close_price=float(c["close_price"]) if c.get("close_price") else None,
                open_interest=int(c["open_interest"]) if c.get("open_interest") else None,
            ))

        logger.info(
            "alpaca_option_chain_fetched",
            symbol=symbol,
            expiration=expiration,
            contracts=len(results),
        )
        return results

    # ── Order Execution ──────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str = "buy",
        qty: int = 1,
        order_type: str = "market",
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Place a single-leg order (equity or option).

        POST /v2/orders

        Args:
            symbol: Ticker or OCC option symbol (e.g., "SPY" or "SPY260327C00640000").
            side: "buy" or "sell".
            qty: Number of shares/contracts.
            order_type: "market", "limit", "stop", "stop_limit".
            limit_price: Required for limit orders.
            time_in_force: "day", "gtc", "ioc", "fok".

        Returns:
            AlpacaOrder with order ID and status.
        """
        order_data: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "type": order_type,
            "time_in_force": time_in_force,
        }

        if limit_price is not None:
            order_data["limit_price"] = str(limit_price)

        data = await self._request("POST", f"{self.base_url}/orders", json_data=order_data)
        assert isinstance(data, dict)

        order = self._parse_order(data)
        logger.info(
            "alpaca_order_placed",
            symbol=symbol,
            side=side,
            qty=qty,
            order_id=order.id,
            status=order.status,
        )
        return order

    async def place_multileg_order(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        order_type: str = "limit",
        net_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Place a multi-leg option order (spreads, iron condors).

        POST /v2/orders with order_class="mleg"

        Args:
            symbol: Underlying symbol (e.g., "SPY").
            legs: List of leg dicts:
                - symbol: OCC option symbol
                - side: "buy" or "sell"
                - qty: number of contracts
                - ratio_qty: (optional) for ratio spreads
            order_type: "market", "limit", "debit", "credit".
            net_price: Net credit/debit price (positive = debit, negative = credit).
                For limit orders, the net price of the spread.
            time_in_force: "day", "gtc".

        Returns:
            AlpacaOrder with order ID and status.
        """
        order_legs = []
        for leg in legs:
            order_leg: dict[str, Any] = {
                "symbol": leg["symbol"],
                "side": leg["side"],
                "qty": str(leg.get("qty", leg.get("quantity", 1))),
            }
            if "ratio_qty" in leg:
                order_leg["ratio_qty"] = str(leg["ratio_qty"])
            order_legs.append(order_leg)

        order_data: dict[str, Any] = {
            "symbol": symbol,
            "order_class": "mleg",
            "type": order_type,
            "time_in_force": time_in_force,
            "legs": order_legs,
        }

        if net_price is not None:
            order_data["limit_price"] = str(abs(net_price))

        data = await self._request("POST", f"{self.base_url}/orders", json_data=order_data)
        assert isinstance(data, dict)

        order = self._parse_order(data)
        logger.info(
            "alpaca_multileg_order_placed",
            symbol=symbol,
            legs=len(legs),
            order_id=order.id,
            status=order.status,
        )
        return order

    async def submit_multi_leg_order(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        order_type: str = "limit",
        net_price: float | None = None,
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Submit a multi-leg order with fallback to individual legs.

        Tries the native mleg endpoint first. If it returns a 422 (common on
        paper accounts that don't support multi-leg), falls back to submitting
        each leg as a separate order.

        Args:
            symbol: Underlying symbol (e.g., "SPY").
            legs: List of leg dicts with keys: symbol, side, qty.
            order_type: "market", "limit", "debit", "credit".
            net_price: Net credit/debit price for limit orders.
            time_in_force: "day" or "gtc".

        Returns:
            AlpacaOrder from the multi-leg attempt, or the last individual
            leg order if fallback was used.
        """
        try:
            return await self.place_multileg_order(
                symbol=symbol,
                legs=legs,
                order_type=order_type,
                net_price=net_price,
                time_in_force=time_in_force,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                logger.warning(
                    "mleg_not_supported_falling_back",
                    symbol=symbol,
                    legs=len(legs),
                    error=str(e),
                )
                return await self._submit_legs_individually(
                    legs=legs,
                    time_in_force=time_in_force,
                )
            raise
        except Exception as e:
            # Any other error on mleg — try individual legs as last resort
            logger.warning(
                "mleg_failed_falling_back",
                symbol=symbol,
                error=str(e),
            )
            return await self._submit_legs_individually(
                legs=legs,
                time_in_force=time_in_force,
            )

    async def _submit_legs_individually(
        self,
        legs: list[dict[str, Any]],
        time_in_force: str = "day",
    ) -> AlpacaOrder:
        """Fallback: submit each leg of a spread as a separate market order.

        NOTE: This is a workaround for paper accounts that don't support
        order_class=mleg. Individual legs may not fill at the same time,
        creating temporary naked risk.
        """
        last_order: AlpacaOrder | None = None
        for leg in legs:
            order = await self.place_order(
                symbol=leg["symbol"],
                side=leg["side"],
                qty=int(leg.get("qty", leg.get("quantity", 1))),
                order_type="market",
                time_in_force=time_in_force,
            )
            last_order = order
            logger.info(
                "individual_leg_submitted",
                symbol=leg["symbol"],
                side=leg["side"],
                order_id=order.id,
                note="WORKAROUND: mleg not supported, legs submitted individually",
            )
        assert last_order is not None
        return last_order

    # ── Positions ────────────────────────────────────────────────

    async def get_positions(self) -> list[AlpacaPosition]:
        """Get all open positions.

        GET /v2/positions
        """
        data = await self._request("GET", f"{self.base_url}/positions")
        assert isinstance(data, list)

        results = []
        for p in data:
            results.append(AlpacaPosition(
                asset_id=p.get("asset_id", ""),
                symbol=p.get("symbol", ""),
                qty=float(p.get("qty", 0)),
                side=p.get("side", ""),
                market_value=float(p.get("market_value", 0)),
                cost_basis=float(p.get("cost_basis", 0)),
                unrealized_pl=float(p.get("unrealized_pl", 0)),
                unrealized_plpc=float(p.get("unrealized_plpc", 0)),
                current_price=float(p.get("current_price", 0)),
                avg_entry_price=float(p.get("avg_entry_price", 0)),
                asset_class=p.get("asset_class", ""),
            ))

        logger.info("alpaca_positions_fetched", count=len(results))
        return results

    # ── Orders ───────────────────────────────────────────────────

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
    ) -> list[AlpacaOrder]:
        """Get order history.

        GET /v2/orders
        """
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
        }

        data = await self._request("GET", f"{self.base_url}/orders", params=params)
        assert isinstance(data, list)

        results = [self._parse_order(o) for o in data]
        logger.info("alpaca_orders_fetched", count=len(results), status=status)
        return results

    # ── Close Position ───────────────────────────────────────────

    async def close_position(self, symbol_or_id: str) -> dict[str, Any]:
        """Close a position by symbol or asset ID.

        DELETE /v2/positions/{symbol_or_asset_id}
        """
        data = await self._request(
            "DELETE", f"{self.base_url}/positions/{symbol_or_id}"
        )
        assert isinstance(data, dict)
        logger.info("alpaca_position_closed", symbol_or_id=symbol_or_id)
        return data

    # ── Portfolio History ────────────────────────────────────────

    async def get_portfolio_history(
        self,
        period: str = "1M",
        timeframe: str = "1D",
    ) -> AlpacaPortfolioHistory:
        """Get portfolio P&L history.

        GET /v2/account/portfolio/history

        Args:
            period: "1D", "1W", "1M", "3M", "1A", "all", or intraday like "1D".
            timeframe: "1Min", "5Min", "15Min", "1H", "1D".
        """
        params: dict[str, Any] = {
            "period": period,
            "timeframe": timeframe,
        }

        data = await self._request(
            "GET", f"{self.base_url}/account/portfolio/history", params=params
        )
        assert isinstance(data, dict)

        return AlpacaPortfolioHistory(
            timestamp=data.get("timestamp", []),
            equity=data.get("equity", []),
            profit_loss=data.get("profit_loss", []),
            profit_loss_pct=data.get("profit_loss_pct", []),
            base_value=float(data.get("base_value", 0)),
            timeframe=data.get("timeframe", ""),
        )

    # ── Tradier-Compatible Interface ────────────────────────────
    # These methods return the same data models as TradierClient so the
    # engine, black_swan, pillars, and position_manager work unchanged.

    # Symbols that are indices (not stocks) — Alpaca can't quote these directly.
    # We use UVXY as a VIX proxy (VIX ETN), or return a stub.
    _INDEX_SYMBOLS = {"VIX", "$VIX", "VIX.X", "SPX", "$SPX"}
    _VIX_PROXY = "VIXY"  # ProShares VIX Short-Term Futures ETF (quotable on Alpaca)

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch quotes for multiple symbols, returning tradier-compatible Quote objects.
        
        Handles index symbols (VIX, SPX) that aren't available on Alpaca's stock API
        by using ETF proxies or returning safe stubs.
        """
        results: list[Quote] = []
        for symbol in symbols:
            if symbol.upper() in self._INDEX_SYMBOLS:
                # VIX / SPX are indices — not quotable as stocks on Alpaca
                # Return a stub with reasonable defaults so the engine doesn't crash
                logger.debug("index_symbol_stub", symbol=symbol, note="indices not available on Alpaca stock API")
                results.append(Quote(
                    symbol=symbol,
                    last=0.0,
                    bid=0.0,
                    ask=0.0,
                    high=0.0,
                    low=0.0,
                    volume=0,
                    change=0.0,
                    change_percentage=0.0,
                ))
                continue
            try:
                aq = await self.get_quote(symbol)
                results.append(Quote(
                    symbol=aq.symbol,
                    last=aq.last,
                    bid=aq.bid,
                    ask=aq.ask,
                    high=aq.high,
                    low=aq.low,
                    volume=aq.volume,
                    change=0.0,
                    change_percentage=0.0,
                ))
            except Exception as e:
                logger.warning("quote_fetch_failed", symbol=symbol, error=str(e))
                results.append(Quote(
                    symbol=symbol,
                    last=0.0, bid=0.0, ask=0.0, high=0.0, low=0.0,
                    volume=0, change=0.0, change_percentage=0.0,
                ))
        return results

    async def get_bars(
        self,
        symbol: str,
        interval: str = "daily",
        start: "date | None" = None,
        end: "date | None" = None,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars via Alpaca Data API, returning tradier-compatible Bar objects."""
        from datetime import date as _date, datetime as _dt

        # Index symbols can't be fetched from stock bars endpoint
        # Use ETF proxies: SPX→SPY, VIX→VIXY
        _bar_proxy = {"SPX": "SPY", "$SPX": "SPY", "VIX": "VIXY", "$VIX": "VIXY", "VIX.X": "VIXY"}
        fetch_symbol = _bar_proxy.get(symbol.upper(), symbol)

        # Map tradier interval names to Alpaca timeframes
        tf_map = {
            "daily": "1Day",
            "weekly": "1Week",
            "monthly": "1Month",
            "5min": "5Min",
            "15min": "15Min",
        }
        timeframe = tf_map.get(interval, "1Day")

        params: dict[str, Any] = {
            "timeframe": timeframe,
            "limit": 1000,
            "adjustment": "raw",
            "feed": "iex",
        }
        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()

        try:
            data = await self._request(
                "GET", f"{self.data_url}/stocks/{fetch_symbol}/bars", params=params
            )
        except Exception as e:
            logger.warning("bars_fetch_failed", symbol=symbol, fetch_symbol=fetch_symbol, error=str(e))
            return []
        assert isinstance(data, dict)

        bars_data = data.get("bars", [])
        results: list[Bar] = []
        for b in bars_data:
            ts = b.get("t", "")
            try:
                timestamp = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                timestamp = _dt.now()
            results.append(Bar(
                timestamp=timestamp,
                open=float(b.get("o", 0)),
                high=float(b.get("h", 0)),
                low=float(b.get("l", 0)),
                close=float(b.get("c", 0)),
                volume=int(b.get("v", 0)),
            ))

        logger.info("alpaca_bars_fetched", symbol=symbol, interval=interval, count=len(results))
        return results

    async def get_option_expirations(self, symbol: str) -> list[str]:
        """Get available option expiration dates for a symbol.

        Uses the Alpaca options contracts endpoint to discover expirations.
        """
        params: dict[str, Any] = {
            "underlying_symbols": symbol,
            "limit": 1000,
        }
        data = await self._request(
            "GET", f"{self.base_url}/options/contracts", params=params
        )
        assert isinstance(data, dict)

        contracts = data.get("option_contracts", [])
        expirations = sorted({c.get("expiration_date", "") for c in contracts if c.get("expiration_date")})
        return expirations

    async def get_option_chain_compat(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True,
    ) -> list[OptionQuote]:
        """Fetch option chain returning tradier-compatible OptionQuote objects.

        This fetches contracts from the trading API and snapshots from the data API
        to build full OptionQuote objects with greeks.
        """
        # Step 1: Get contracts
        contracts = await self.get_option_chain(symbol, expiration)
        if not contracts:
            return []

        # Step 2: Get snapshots for pricing data
        occ_symbols = [c.symbol for c in contracts if c.symbol]
        snapshots = await self._get_option_snapshots(occ_symbols)

        results: list[OptionQuote] = []
        for c in contracts:
            snap = snapshots.get(c.symbol, {})
            latest_quote = snap.get("latestQuote", {})
            greeks_data = snap.get("greeks", {})

            bid = float(latest_quote.get("bp", 0))
            ask = float(latest_quote.get("ap", 0))

            greeks_obj = None
            if greeks and greeks_data:
                greeks_obj = OptionGreeks(
                    delta=float(greeks_data.get("delta", 0)),
                    gamma=float(greeks_data.get("gamma", 0)),
                    theta=float(greeks_data.get("theta", 0)),
                    vega=float(greeks_data.get("vega", 0)),
                    rho=float(greeks_data.get("rho", 0)),
                    smv_vol=float(greeks_data.get("mid_iv", 0)),
                )

            opt_type = OptionType.CALL if c.type == "call" else OptionType.PUT

            results.append(OptionQuote(
                symbol=c.symbol,
                option_type=opt_type,
                strike=c.strike_price,
                expiration=c.expiration_date,
                bid=bid,
                ask=ask,
                mid=round((bid + ask) / 2, 2) if (bid + ask) > 0 else 0.0,
                last=float(snap.get("latestTrade", {}).get("p", 0)),
                volume=int(latest_quote.get("s", 0)),
                open_interest=c.open_interest or 0,
                greeks=greeks_obj,
            ))

        logger.info("alpaca_option_chain_compat", symbol=symbol, expiration=expiration, contracts=len(results))
        return results

    async def _get_option_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch option snapshots for a batch of OCC symbols."""
        if not symbols:
            return {}

        # Alpaca allows batching snapshots — fetch in chunks of 100
        all_snapshots: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            try:
                data = await self._request(
                    "GET",
                    f"{self.options_data_url}/options/snapshots",
                    params={"symbols": ",".join(chunk), "feed": "indicative"},
                )
                assert isinstance(data, dict)
                snapshots = data.get("snapshots", {})
                all_snapshots.update(snapshots)
            except Exception as e:
                logger.warning("option_snapshots_failed", error=str(e), chunk_size=len(chunk))

        return all_snapshots

    async def get_account_balance(self) -> dict[str, Any]:
        """Get account balance in tradier-compatible dict format."""
        acct = await self.get_account()
        return {
            "total_equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
            "portfolio_value": acct.portfolio_value,
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _parse_order(self, data: dict[str, Any]) -> AlpacaOrder:
        """Parse an order response dict into AlpacaOrder."""
        return AlpacaOrder(
            id=data.get("id", ""),
            client_order_id=data.get("client_order_id", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            qty=data.get("qty", ""),
            type=data.get("type", ""),
            status=data.get("status", ""),
            filled_qty=data.get("filled_qty", ""),
            filled_avg_price=data.get("filled_avg_price"),
            limit_price=data.get("limit_price"),
            order_class=data.get("order_class", ""),
            created_at=data.get("created_at", ""),
            legs=data.get("legs") or [],
        )


#===============================================================================
# SECTION: DATA - Tradier Client
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/data/tradier.py (564 lines)
#-------------------------------------------------------------------------------

"""Tradier Market Data Client.

Async client for the Tradier API — quotes, option chains, historical bars, and streaming.
Supports both sandbox and production endpoints with automatic rate limiting and retries.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from enum import Enum
from typing import Any, AsyncIterator

import httpx
import structlog
from pydantic import BaseModel, Field

from esther.core.config import env

logger = structlog.get_logger(__name__)

# Tradier endpoints
PROD_BASE = "https://api.tradier.com/v1"
SANDBOX_BASE = "https://sandbox.tradier.com/v1"
STREAM_BASE = "https://stream.tradier.com/v1"
SANDBOX_STREAM_BASE = "https://sandbox.tradier.com/v1"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class Quote(BaseModel):
    """Market quote for a symbol."""

    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float | None = None
    close: float | None = None
    volume: int = 0
    change: float = 0.0
    change_pct: float = Field(default=0.0, alias="change_percentage")

    model_config = {"populate_by_name": True}


class OptionQuote(BaseModel):
    """Single option contract quote."""

    symbol: str
    option_type: OptionType
    strike: float
    expiration: str
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    greeks: OptionGreeks | None = None


class OptionGreeks(BaseModel):
    """Greeks for an option contract."""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    mid_iv: float = Field(default=0.0, alias="smv_vol")

    model_config = {"populate_by_name": True}


class Bar(BaseModel):
    """OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class TradierClient:
    """Async Tradier API client with rate limiting and retries.

    Usage:
        async with TradierClient() as client:
            quotes = await client.get_quotes(["SPY", "QQQ"])
            chain = await client.get_option_chain("SPY", "2024-03-28")
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds, doubles on each retry
    RATE_LIMIT_DELAY = 0.1  # minimum seconds between requests

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        sandbox: bool | None = None,
    ):
        _env = env()
        self.api_key = api_key or _env.tradier_api_key
        self.account_id = account_id or _env.tradier_account_id
        self.sandbox = sandbox if sandbox is not None else _env.tradier_sandbox

        self.base_url = SANDBOX_BASE if self.sandbox else PROD_BASE
        self.stream_url = SANDBOX_STREAM_BASE if self.sandbox else STREAM_BASE

        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def __aenter__(self) -> TradierClient:
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < self.RATE_LIMIT_DELAY:
                await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make an API request with retries and rate limiting.

        Retries on 429 (rate limit) and 5xx errors with exponential backoff.
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use 'async with TradierClient() as client:'")

        url = f"{self.base_url}{path}"
        delay = self.RETRY_DELAY

        for attempt in range(self.MAX_RETRIES):
            await self._rate_limit()

            try:
                if method == "GET":
                    resp = await self._client.get(url, params=params)
                else:
                    resp = await self._client.post(url, data=data)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    logger.warning("rate_limited", retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(retry_after)
                    delay *= 2
                    continue

                if resp.status_code >= 500:
                    logger.warning("server_error", status=resp.status_code, attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                resp.raise_for_status()
                return resp.json()

            except httpx.TimeoutException:
                logger.warning("request_timeout", attempt=attempt)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

        raise RuntimeError(f"Max retries ({self.MAX_RETRIES}) exceeded for {path}")

    # ── Market Data ──────────────────────────────────────────────

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch current quotes for a list of symbols.

        Args:
            symbols: List of ticker symbols (e.g., ["SPY", "QQQ"]).

        Returns:
            List of Quote objects with current market data.
        """
        data = await self._request("GET", "/markets/quotes", params={
            "symbols": ",".join(symbols),
            "greeks": "false",
        })

        quotes_data = (data.get("quotes") or {}).get("quote") or []
        if isinstance(quotes_data, dict):
            quotes_data = [quotes_data]

        results = []
        for q in quotes_data:
            results.append(Quote(
                symbol=q.get("symbol", ""),
                last=float(q.get("last") or 0),
                bid=float(q.get("bid") or 0),
                ask=float(q.get("ask") or 0),
                high=float(q.get("high") or 0),
                low=float(q.get("low") or 0),
                open=float(q.get("open") or 0) if q.get("open") else None,
                close=float(q.get("close") or 0) if q.get("close") else None,
                volume=int(q.get("volume") or 0),
                change=float(q.get("change") or 0),
                change_percentage=float(q.get("change_percentage") or 0),
            ))

        logger.info("quotes_fetched", count=len(results), symbols=symbols)
        return results

    async def get_option_chain(
        self, symbol: str, expiration: str, greeks: bool = True
    ) -> list[OptionQuote]:
        """Fetch the full option chain for a symbol and expiration.

        Args:
            symbol: Underlying ticker (e.g., "SPY").
            expiration: Expiration date as YYYY-MM-DD.
            greeks: Whether to include Greeks in the response.

        Returns:
            List of OptionQuote objects for all strikes and types.
        """
        data = await self._request("GET", "/markets/options/chains", params={
            "symbol": symbol,
            "expiration": expiration,
            "greeks": str(greeks).lower(),
        })

        options_data = data.get("options", {}).get("option", [])
        if isinstance(options_data, dict):
            options_data = [options_data]

        results = []
        for opt in options_data:
            greeks_data = opt.get("greeks")
            greeks_obj = None
            if greeks_data and isinstance(greeks_data, dict):
                greeks_obj = OptionGreeks(
                    delta=float(greeks_data.get("delta", 0)),
                    gamma=float(greeks_data.get("gamma", 0)),
                    theta=float(greeks_data.get("theta", 0)),
                    vega=float(greeks_data.get("vega", 0)),
                    rho=float(greeks_data.get("rho", 0)),
                    smv_vol=float(greeks_data.get("smv_vol", 0)),
                )

            bid = float(opt.get("bid", 0))
            ask = float(opt.get("ask", 0))

            results.append(OptionQuote(
                symbol=opt.get("symbol", ""),
                option_type=OptionType(opt.get("option_type", "call")),
                strike=float(opt.get("strike", 0)),
                expiration=opt.get("expiration_date", expiration),
                bid=bid,
                ask=ask,
                mid=round((bid + ask) / 2, 2) if (bid + ask) > 0 else 0.0,
                last=float(opt.get("last") or 0),
                volume=int(opt.get("volume") or 0),
                open_interest=int(opt.get("open_interest") or 0),
                greeks=greeks_obj,
            ))

        logger.info("option_chain_fetched", symbol=symbol, expiration=expiration, contracts=len(results))
        return results

    async def get_option_expirations(self, symbol: str) -> list[str]:
        """Get available expiration dates for a symbol.

        Returns:
            List of expiration date strings (YYYY-MM-DD).
        """
        data = await self._request("GET", "/markets/options/expirations", params={
            "symbol": symbol,
            "includeAllRoots": "true",
        })

        expirations = data.get("expirations", {}).get("date", [])
        if isinstance(expirations, str):
            expirations = [expirations]

        return expirations

    async def get_bars(
        self,
        symbol: str,
        interval: str = "daily",
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars.

        Args:
            symbol: Ticker symbol.
            interval: Bar interval — "daily", "weekly", "monthly", or intraday like "5min", "15min".
            start: Start date.
            end: End date.

        Returns:
            List of Bar objects.
        """
        is_intraday = interval.endswith("min")

        if is_intraday:
            path = "/markets/timesales"
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval.replace("min", ""),
                "session_filter": "open",
            }
            if start:
                params["start"] = start.isoformat()
            if end:
                params["end"] = end.isoformat()
        else:
            path = "/markets/history"
            params = {
                "symbol": symbol,
                "interval": interval,
            }
            if start:
                params["start"] = start.isoformat()
            if end:
                params["end"] = end.isoformat()

        data = await self._request("GET", path, params=params)

        bars = []
        if is_intraday:
            series = (data.get("series") or {}).get("data") or []
            if isinstance(series, dict):
                series = [series]
            for point in series:
                bars.append(Bar(
                    timestamp=datetime.fromisoformat(point["time"]),
                    open=float(point.get("open", 0)),
                    high=float(point.get("high", 0)),
                    low=float(point.get("low", 0)),
                    close=float(point.get("close", point.get("price", 0))),
                    volume=int(point.get("volume", 0)),
                ))
        else:
            history_data = data.get("history") or {}
            history = history_data.get("day") or []
            if isinstance(history, dict):
                history = [history]
            for day in history:
                bars.append(Bar(
                    timestamp=datetime.fromisoformat(day["date"]),
                    open=float(day.get("open", 0)),
                    high=float(day.get("high", 0)),
                    low=float(day.get("low", 0)),
                    close=float(day.get("close", 0)),
                    volume=int(day.get("volume", 0)),
                ))

        logger.info("bars_fetched", symbol=symbol, interval=interval, count=len(bars))
        return bars

    # ── Streaming ────────────────────────────────────────────────

    async def _create_streaming_session(self) -> str:
        """Create a streaming session and return the session ID."""
        data = await self._request("POST", "/markets/events/session")
        return data["stream"]["sessionid"]

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Stream real-time quotes for symbols.

        Yields Quote objects as prices update. Automatically creates a streaming
        session and reconnects on failure.

        Args:
            symbols: List of ticker symbols to stream.

        Yields:
            Quote objects with updated prices.
        """
        session_id = await self._create_streaming_session()
        stream_url = f"{self.stream_url}/markets/events"

        logger.info("streaming_started", symbols=symbols)

        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as stream_client:
            async with stream_client.stream(
                "POST",
                stream_url,
                data={
                    "sessionid": session_id,
                    "symbols": ",".join(symbols),
                    "filter": "quote",
                    "linebreak": "true",
                },
                headers=self._headers,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json
                        event = json.loads(line)
                        if event.get("type") == "quote":
                            yield Quote(
                                symbol=event.get("symbol", ""),
                                last=float(event.get("last", 0)),
                                bid=float(event.get("bid", 0)),
                                ask=float(event.get("ask", 0)),
                                high=float(event.get("high", 0)),
                                low=float(event.get("low", 0)),
                                volume=int(event.get("cvol", 0)),
                                change=float(event.get("change", 0)),
                                change_percentage=float(event.get("change_percentage", 0)),
                            )
                    except Exception as e:
                        logger.warning("stream_parse_error", error=str(e), line=line[:100])
                        continue

    # ── Order Execution ──────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        option_symbol: str | None = None,
        side: str = "buy_to_open",
        quantity: int = 1,
        order_type: str = "market",
        price: float | None = None,
        duration: str = "day",
    ) -> dict[str, Any]:
        """Place a single-leg order.

        Args:
            symbol: Underlying symbol.
            option_symbol: Option contract symbol (OCC format). None for equity orders.
            side: Order side — buy_to_open, sell_to_open, buy_to_close, sell_to_close.
            quantity: Number of contracts/shares.
            order_type: market, limit, stop, stop_limit.
            price: Limit price (required for limit/stop_limit).
            duration: day or gtc.

        Returns:
            Order response dict with order ID and status.
        """
        order_data: dict[str, Any] = {
            "class": "option" if option_symbol else "equity",
            "symbol": symbol,
            "side": side,
            "quantity": str(quantity),
            "type": order_type,
            "duration": duration,
        }

        if option_symbol:
            order_data["option_symbol"] = option_symbol

        if price is not None:
            order_data["price"] = str(price)

        result = await self._request(
            "POST",
            f"/accounts/{self.account_id}/orders",
            data=order_data,
        )

        logger.info("order_placed", symbol=symbol, side=side, quantity=quantity, result=result)
        return result

    async def place_multileg_order(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        order_type: str = "credit",
        price: float | None = None,
        duration: str = "day",
    ) -> dict[str, Any]:
        """Place a multi-leg option order (spreads, iron condors, etc.).

        Args:
            symbol: Underlying symbol.
            legs: List of leg dicts, each with:
                - option_symbol: OCC option symbol
                - side: buy_to_open, sell_to_open, etc.
                - quantity: number of contracts
            order_type: credit, debit, even, market.
            price: Net credit/debit price.
            duration: day or gtc.

        Returns:
            Order response dict.
        """
        order_data: dict[str, Any] = {
            "class": "multileg",
            "symbol": symbol,
            "type": order_type,
            "duration": duration,
        }

        if price is not None:
            order_data["price"] = str(price)

        for i, leg in enumerate(legs):
            order_data[f"option_symbol[{i}]"] = leg["option_symbol"]
            order_data[f"side[{i}]"] = leg["side"]
            order_data[f"quantity[{i}]"] = str(leg["quantity"])

        result = await self._request(
            "POST",
            f"/accounts/{self.account_id}/orders",
            data=order_data,
        )

        logger.info("multileg_order_placed", symbol=symbol, legs=len(legs), result=result)
        return result

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order."""
        result = await self._request(
            "DELETE" if hasattr(self._client, "delete") else "POST",
            f"/accounts/{self.account_id}/orders/{order_id}",
        )
        logger.info("order_cancelled", order_id=order_id)
        return result

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions in the account."""
        data = await self._request("GET", f"/accounts/{self.account_id}/positions")
        positions = data.get("positions", {}).get("position", [])
        if isinstance(positions, dict):
            positions = [positions]
        return positions

    async def get_account_balance(self) -> dict[str, Any]:
        """Get account balance and buying power."""
        data = await self._request("GET", f"/accounts/{self.account_id}/balances")
        return data.get("balances", {})


#===============================================================================
# SECTION: AI - Debate System (Riki, Abi, Kage, Kimi)
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/ai/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: esther/ai/debate.py (919 lines)
#-------------------------------------------------------------------------------

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


#===============================================================================
# SECTION: AI - Position Sizing
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/ai/sizing.py (329 lines)
#-------------------------------------------------------------------------------

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


#===============================================================================
# SECTION: SIGNALS - Bias Engine
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/__init__.py (27 lines)
#-------------------------------------------------------------------------------

"""Esther Signals — Technical analysis, bias detection, and trade signal generation."""

from esther.signals.bias_engine import BiasEngine, BiasScore
from esther.signals.calendar import CalendarModule, EconomicEvent
from esther.signals.flow import FlowAnalyzer, FlowEntry, FlowSummary
from esther.signals.ifvg import IFVGDetector, IFVGEntry, FVG
from esther.signals.levels import LevelTracker, KeyLevels
from esther.signals.regime import RegimeDetector, RegimeResult, RegimeState

__all__ = [
    "BiasEngine",
    "BiasScore",
    "CalendarModule",
    "EconomicEvent",
    "FlowAnalyzer",
    "FlowEntry",
    "FlowSummary",
    "IFVGDetector",
    "IFVGEntry",
    "FVG",
    "LevelTracker",
    "KeyLevels",
    "RegimeDetector",
    "RegimeResult",
    "RegimeState",
]

#-------------------------------------------------------------------------------
# FILE: esther/signals/bias_engine.py (750 lines)
#-------------------------------------------------------------------------------

"""Bias Engine — Directional Bias Scoring.

Computes a directional bias score from -100 (extreme bear) to +100 (extreme bull)
for each ticker by combining multiple technical indicators and signal sources:

    Core technical:
    - VWAP position (price vs. VWAP)               weight: 0.15
    - EMA crossovers (9/21 EMA)                     weight: 0.10
    - RSI(14) mean reversion/momentum               weight: 0.10
    - VIX level (fear/greed)                         weight: 0.10
    - Price action patterns (candle structure)        weight: 0.10

    New integrated signals:
    - Order Flow (institutional positioning)          weight: 0.25
    - Market Regime (20/50 SMA cross)                weight: 0.10
    - Key Levels (support/resistance position)        weight: 0.10

The bias score determines which trading Pillar(s) are active:
    -20 to +20  → P1 (Iron Condors) — neutral range
    Below -60   → P2 (Bear Call Spreads) — strong bearish
    Above +60   → P3 (Bull Put Spreads) — strong bullish
    ±40+        → P4 (Directional Scalps) — high conviction directional

Multi-timeframe support: bias is calculated on 5m, 15m, 1hr, daily bars
separately, then combined with weights (5m: 0.30, 15m: 0.25, 1hr: 0.25, daily: 0.20).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import Bar
from esther.signals.calendar import CalendarModule
from esther.signals.flow import FlowAnalyzer, FlowEntry
from esther.signals.levels import LevelTracker
from esther.signals.regime import RegimeDetector

logger = structlog.get_logger(__name__)

# Multi-timeframe weights
TIMEFRAME_WEIGHTS = {
    "5m": 0.30,
    "15m": 0.25,
    "1hr": 0.25,
    "daily": 0.20,
}


class Pillar(int, Enum):
    P1_IRON_CONDOR = 1
    P2_BEAR_CALL = 2
    P3_BULL_PUT = 3
    P4_DIRECTIONAL = 4
    P5_BUTTERFLY = 5


class BiasScore(BaseModel):
    """Directional bias result for a single ticker."""

    symbol: str
    score: float  # -100 to +100
    active_pillars: list[int]
    components: dict[str, float]  # individual indicator scores
    confidence: float = 1.0  # 0-1, reduced on event days
    regime_state: str = ""  # current market regime
    timeframe_scores: dict[str, float] = {}  # per-timeframe scores

    @property
    def direction(self) -> str:
        if self.score > 20:
            return "BULL"
        elif self.score < -20:
            return "BEAR"
        return "NEUTRAL"


class BiasEngine:
    """Computes directional bias from multiple technical indicators and signal sources.

    Each indicator produces a sub-score from -100 to +100,
    then they're weighted and combined into the final bias.

    Integrates:
    - RegimeDetector: macro market regime (death/golden cross)
    - FlowAnalyzer: institutional order flow direction
    - CalendarModule: event day confidence reduction
    - LevelTracker: support/resistance position scoring
    """

    def __init__(self):
        self._cfg = config().bias
        self._regime = RegimeDetector()
        self._flow = FlowAnalyzer()
        self._calendar = CalendarModule()
        self._levels = LevelTracker()

    @property
    def regime_detector(self) -> RegimeDetector:
        """Access the regime detector for external use."""
        return self._regime

    @property
    def flow_analyzer(self) -> FlowAnalyzer:
        """Access the flow analyzer for external use."""
        return self._flow

    @property
    def calendar_module(self) -> CalendarModule:
        """Access the calendar module for external use."""
        return self._calendar

    @property
    def level_tracker(self) -> LevelTracker:
        """Access the level tracker for external use."""
        return self._levels

    def compute_bias(
        self,
        symbol: str,
        bars: list[Bar],
        vix_level: float,
        current_price: float | None = None,
        daily_bars: list[Bar] | None = None,
        flow_entries: list[FlowEntry] | None = None,
    ) -> BiasScore:
        """Compute the full bias score for a ticker.

        Args:
            symbol: Ticker symbol.
            bars: Recent OHLCV bars (at least 25 for EMA/RSI calculations).
                  These are the primary timeframe bars (e.g., 5m).
            vix_level: Current VIX reading.
            current_price: Current price (uses last bar close if not provided).
            daily_bars: Daily bars for regime detection (at least 50).
                        If None, regime adjustment is skipped.
            flow_entries: Pre-fetched flow entries for the symbol.
                          If None, flow component returns 0.

        Returns:
            BiasScore with the combined score, active pillars, and metadata.
        """
        if len(bars) < 25:
            logger.warning("insufficient_bars", symbol=symbol, count=len(bars))
            return BiasScore(
                symbol=symbol, score=0.0, active_pillars=[1], components={}
            )

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars])
        price = current_price or closes[-1]

        # --- Core technical components ---
        vwap_score = self._vwap_score(closes, highs, lows, volumes, price)
        ema_score = self._ema_cross_score(closes)
        rsi_score = self._rsi_score(closes)
        vix_score = self._vix_score(vix_level)
        pa_score = self._price_action_score(closes, highs, lows)

        # --- New integrated components ---

        # Flow bias (most important signal — weight 0.25)
        flow_score = 0.0
        if flow_entries:
            flow_score = self._flow.get_flow_bias_sync(flow_entries)

        # Regime adjustment (applied as additive bonus/penalty)
        regime_adjustment = 0.0
        regime_state = ""
        if daily_bars and len(daily_bars) >= 50:
            regime_result = self._regime.detect_regime(daily_bars)
            regime_adjustment = regime_result.bias_adjustment
            regime_state = regime_result.state.value

        # Levels bias (price position relative to key S/R)
        levels_score = self._levels.get_levels_bias(symbol, price)

        # Macro data bias (from FRED economic data)
        macro_bias = self._calendar.get_macro_bias()

        components = {
            "vwap": round(vwap_score, 2),
            "ema_cross": round(ema_score, 2),
            "rsi": round(rsi_score, 2),
            "vix": round(vix_score, 2),
            "price_action": round(pa_score, 2),
            "flow": round(flow_score, 2),
            "regime": round(regime_adjustment, 2),
            "levels": round(levels_score, 2),
            "macro": round(macro_bias, 2),
        }

        # Weighted combination using new weights
        w = self._cfg.weights
        raw_score = (
            vwap_score * w.vwap
            + ema_score * w.ema_cross
            + rsi_score * w.rsi
            + vix_score * w.vix
            + pa_score * w.price_action
            + flow_score * w.flow
            + regime_adjustment * w.regime
            + levels_score * w.levels
            + macro_bias  # Additive: -50 to +50 direct contribution
        )

        # Clamp to [-100, 100]
        score = float(np.clip(raw_score, -100, 100))

        # Calendar confidence adjustment
        confidence = self._calendar.get_confidence_adjustment()
        if confidence < 1.0:
            # Scale the score toward neutral on event days
            score = score * confidence
            logger.info(
                "bias_confidence_reduced",
                symbol=symbol,
                confidence=confidence,
                adjusted_score=round(score, 2),
            )

        # Determine active pillars
        active = self._determine_pillars(score)

        result = BiasScore(
            symbol=symbol,
            score=round(score, 2),
            active_pillars=active,
            components=components,
            confidence=round(confidence, 2),
            regime_state=regime_state,
        )

        logger.info(
            "bias_computed",
            symbol=symbol,
            score=result.score,
            direction=result.direction,
            pillars=active,
            confidence=confidence,
            regime=regime_state,
        )
        return result

    def compute_multi_timeframe_bias(
        self,
        symbol: str,
        bars_5m: list[Bar],
        bars_15m: list[Bar],
        bars_1hr: list[Bar],
        bars_daily: list[Bar],
        vix_level: float,
        current_price: float | None = None,
        flow_entries: list[FlowEntry] | None = None,
    ) -> BiasScore:
        """Compute bias using multi-timeframe analysis.

        Calculates bias on each timeframe separately, then combines
        using the timeframe weights:
            5m:  0.30 (most responsive to current action)
            15m: 0.25 (medium-term structure)
            1hr: 0.25 (trend direction)
            daily: 0.20 (macro context)

        Regime and calendar adjustments are applied once to the final score,
        not per-timeframe. Flow is also applied once (it's not timeframe-specific).

        Args:
            symbol: Ticker symbol.
            bars_5m: 5-minute bars (at least 25).
            bars_15m: 15-minute bars (at least 25).
            bars_1hr: 1-hour bars (at least 25).
            bars_daily: Daily bars (at least 50 for regime).
            vix_level: Current VIX level.
            current_price: Override current price.
            flow_entries: Pre-fetched flow entries.

        Returns:
            BiasScore with combined multi-timeframe score.
        """
        price = current_price

        # Calculate per-timeframe technical scores
        tf_scores: dict[str, float] = {}

        for tf_name, tf_bars, tf_weight in [
            ("5m", bars_5m, TIMEFRAME_WEIGHTS["5m"]),
            ("15m", bars_15m, TIMEFRAME_WEIGHTS["15m"]),
            ("1hr", bars_1hr, TIMEFRAME_WEIGHTS["1hr"]),
            ("daily", bars_daily, TIMEFRAME_WEIGHTS["daily"]),
        ]:
            if len(tf_bars) < 25:
                tf_scores[tf_name] = 0.0
                continue

            closes = np.array([b.close for b in tf_bars])
            highs = np.array([b.high for b in tf_bars])
            lows = np.array([b.low for b in tf_bars])
            volumes = np.array([b.volume for b in tf_bars])
            p = price or closes[-1]

            # Core technicals only for per-timeframe scoring
            vwap = self._vwap_score(closes, highs, lows, volumes, p)
            ema = self._ema_cross_score(closes)
            rsi = self._rsi_score(closes)
            pa = self._price_action_score(closes, highs, lows)

            # Equal weight within each timeframe for the technical core
            tf_score = (vwap + ema + rsi + pa) / 4.0
            tf_scores[tf_name] = round(tf_score, 2)

        # Weighted timeframe combination (technical component)
        tech_score = sum(
            tf_scores.get(tf, 0.0) * w
            for tf, w in TIMEFRAME_WEIGHTS.items()
        )

        # Now layer on the non-timeframe signals
        vix_score = self._vix_score(vix_level)
        flow_score = self._flow.get_flow_bias_sync(flow_entries) if flow_entries else 0.0
        regime_adjustment = 0.0
        regime_state = ""
        if bars_daily and len(bars_daily) >= 50:
            regime_result = self._regime.detect_regime(bars_daily)
            regime_adjustment = regime_result.bias_adjustment
            regime_state = regime_result.state.value

        levels_score = self._levels.get_levels_bias(
            symbol, price or bars_5m[-1].close if bars_5m else 0.0
        )

        # Final combination with all weights
        w = self._cfg.weights
        # Technical core gets the combined weights of vwap+ema+rsi+price_action
        tech_weight = w.vwap + w.ema_cross + w.rsi + w.price_action
        raw_score = (
            tech_score * tech_weight
            + vix_score * w.vix
            + flow_score * w.flow
            + regime_adjustment * w.regime
            + levels_score * w.levels
        )

        score = float(np.clip(raw_score, -100, 100))

        # Calendar adjustment
        confidence = self._calendar.get_confidence_adjustment()
        if confidence < 1.0:
            score *= confidence

        active = self._determine_pillars(score)

        components = {
            "tech_5m": tf_scores.get("5m", 0.0),
            "tech_15m": tf_scores.get("15m", 0.0),
            "tech_1hr": tf_scores.get("1hr", 0.0),
            "tech_daily": tf_scores.get("daily", 0.0),
            "vix": round(vix_score, 2),
            "flow": round(flow_score, 2),
            "regime": round(regime_adjustment, 2),
            "levels": round(levels_score, 2),
        }

        result = BiasScore(
            symbol=symbol,
            score=round(score, 2),
            active_pillars=active,
            components=components,
            confidence=round(confidence, 2),
            regime_state=regime_state,
            timeframe_scores=tf_scores,
        )

        logger.info(
            "multi_tf_bias_computed",
            symbol=symbol,
            score=result.score,
            direction=result.direction,
            pillars=active,
            tf_scores=tf_scores,
            regime=regime_state,
        )
        return result

    def _vwap_score(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
        current_price: float,
    ) -> float:
        """Score based on price position relative to VWAP.

        VWAP = cumulative(typical_price * volume) / cumulative(volume)
        Price above VWAP → bullish; below → bearish.
        Score scaled by distance from VWAP as % of price.
        """
        typical_prices = (highs + lows + closes) / 3
        cum_tp_vol = np.cumsum(typical_prices * volumes)
        cum_vol = np.cumsum(volumes)

        # Avoid division by zero
        mask = cum_vol > 0
        if not mask.any():
            return 0.0

        vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else closes[-1]

        # Distance from VWAP as percentage
        distance_pct = ((current_price - vwap) / vwap) * 100

        # Scale: ±2% from VWAP = ±100 score
        return float(np.clip(distance_pct * 50, -100, 100))

    def _ema_cross_score(self, closes: np.ndarray) -> float:
        """Score based on EMA(fast) vs EMA(slow) crossover.

        Fast EMA above slow → bullish; below → bearish.
        Score based on distance between EMAs as % of price.
        Also checks for recent crossover (stronger signal).
        """
        fast_period = self._cfg.ema["fast"]
        slow_period = self._cfg.ema["slow"]

        fast_ema = self._compute_ema(closes, fast_period)
        slow_ema = self._compute_ema(closes, slow_period)

        # Current spread as percentage
        spread_pct = ((fast_ema[-1] - slow_ema[-1]) / slow_ema[-1]) * 100

        # Check for recent crossover (within last 3 bars) — amplify signal
        crossover_bonus = 0.0
        if len(fast_ema) >= 3 and len(slow_ema) >= 3:
            for i in range(-3, 0):
                prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
                curr_diff = fast_ema[i] - slow_ema[i]
                if prev_diff <= 0 < curr_diff:  # bullish crossover
                    crossover_bonus = 30.0
                elif prev_diff >= 0 > curr_diff:  # bearish crossover
                    crossover_bonus = -30.0

        # Scale: ±1% spread = ±70, plus crossover bonus
        score = spread_pct * 70 + crossover_bonus
        return float(np.clip(score, -100, 100))

    def _rsi_score(self, closes: np.ndarray) -> float:
        """Score based on RSI(14).

        RSI mapping to bias score:
            RSI > 70 (overbought) → bearish (mean reversion expected)
            RSI < 30 (oversold)   → bullish (mean reversion expected)
            RSI 45-55             → neutral
            RSI 55-70             → mildly bullish (momentum)
            RSI 30-45             → mildly bearish (momentum)

        This captures both momentum and mean reversion — RSI between 30-70
        is treated as momentum, extreme RSI as mean reversion.
        """
        rsi = self._compute_rsi(closes, self._cfg.rsi["period"])

        if rsi is None:
            return 0.0

        overbought = self._cfg.rsi["overbought"]
        oversold = self._cfg.rsi["oversold"]

        if rsi >= overbought:
            # Overbought → expect pullback → bearish
            return float(np.clip(-(rsi - overbought) * 3.3, -100, 0))
        elif rsi <= oversold:
            # Oversold → expect bounce → bullish
            return float(np.clip((oversold - rsi) * 3.3, 0, 100))
        else:
            # Middle zone: momentum reading
            # RSI 50 = neutral, RSI 60 = mildly bullish, RSI 40 = mildly bearish
            return float((rsi - 50) * 2.5)

    def _vix_score(self, vix_level: float) -> float:
        """Score based on VIX level.

        Low VIX (< 15) → complacency → slightly bearish (correction risk)
        Normal VIX (15-20) → neutral
        Elevated VIX (20-30) → fear → contrarian bullish (bounce expected)
        High VIX (> 30) → NOT automatically bearish. SuperLuckeee explicitly
            says "VIX at 30 = BEST time for iron condors" because premium is fat.
            Mildly bearish for direction but BULLISH for IC profitability.
        Extreme VIX (> 35) → capitulation zone, historical bottom (April 2025 pattern)
        """
        if vix_level > 35:
            return -60.0  # Capitulation — bearish but watch for reversal
        elif vix_level > 30:
            # VIX 30+ = elevated regime. NOT panic shutdown.
            # SuperLuckeee: "used when IV is high (VIX at 30)" = IC sweet spot.
            # Mildly bearish for direction, but P1 (IC) should be PRIORITIZED.
            return -30.0
        elif vix_level > 25:
            return -40.0  # High fear
        elif vix_level > 20:
            return 20.0  # Elevated but contrarian bullish
        elif vix_level > 15:
            return 0.0  # Normal
        else:
            return -15.0  # Complacent — slight correction risk

    def _price_action_score(
        self,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
    ) -> float:
        """Score based on recent price action patterns.

        Analyzes the last 5 bars for:
        - Higher highs / lower lows trend
        - Body size relative to range (conviction)
        - Closing position within range (buying/selling pressure)
        """
        if len(closes) < 5:
            return 0.0

        # Last 5 bars
        recent_closes = closes[-5:]
        recent_highs = highs[-5:]
        recent_lows = lows[-5:]

        score = 0.0

        # Higher highs count vs lower lows count
        hh_count = sum(
            1 for i in range(1, len(recent_highs))
            if recent_highs[i] > recent_highs[i - 1]
        )
        ll_count = sum(
            1 for i in range(1, len(recent_lows))
            if recent_lows[i] < recent_lows[i - 1]
        )

        # More higher highs → bullish, more lower lows → bearish
        score += (hh_count - ll_count) * 15

        # Closing position within range for the last bar
        # Close near high = bullish pressure, close near low = bearish
        last_range = recent_highs[-1] - recent_lows[-1]
        if last_range > 0:
            close_position = (recent_closes[-1] - recent_lows[-1]) / last_range
            # 0.0 = closed at low, 1.0 = closed at high
            score += (close_position - 0.5) * 40

        # Net movement over 5 bars
        net_change = (recent_closes[-1] - recent_closes[0]) / recent_closes[0] * 100
        score += net_change * 20

        return float(np.clip(score, -100, 100))

    def is_choppy(
        self,
        bars: list[Bar],
        bias_score: float,
        vix_level: float,
    ) -> dict[str, Any]:
        """Detect range-bound/choppy market conditions.

        From @SuperLuckeee's Lever 1: "Skip chop."
        Chop KILLS directional traders but is GREAT for iron condors.

        When choppy:
        - P1 (IC) = ALLOWED (chop = free money for premium sellers)
        - P2/P3/P4 = BLOCKED (no directional edge in chop)

        Conditions checked:
        1. Price within tight range (< 0.3% from session midpoint)
        2. Bias score is super neutral (-15 to +15)
        3. ATR of last 10 bars < 50% of 20-bar ATR average
        4. VIX is flat/low (no fear = no direction)

        Args:
            bars: Recent OHLCV bars (at least 20).
            bias_score: Current bias engine score.
            vix_level: Current VIX reading.

        Returns:
            Dict with is_choppy, chop_score (0-100), reasons, and allowed_pillars.
        """
        reasons: list[str] = []
        chop_signals = 0
        total_signals = 4

        if len(bars) < 20:
            return {"is_choppy": False, "chop_score": 0.0, "reasons": ["insufficient_bars"], "allowed_pillars": [1, 2, 3, 4]}

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])

        # Signal 1: Tight price range — session midpoint test
        recent_high = float(highs[-10:].max())
        recent_low = float(lows[-10:].min())
        recent_mid = (recent_high + recent_low) / 2
        current_price = float(closes[-1])

        if recent_mid > 0:
            distance_from_mid_pct = abs(current_price - recent_mid) / recent_mid
            if distance_from_mid_pct < 0.003:  # < 0.3% from midpoint
                chop_signals += 1
                reasons.append(f"TIGHT_RANGE: price {distance_from_mid_pct:.2%} from session midpoint")

        # Signal 2: Super neutral bias
        if -15 <= bias_score <= 15:
            chop_signals += 1
            reasons.append(f"NEUTRAL_BIAS: score {bias_score:.1f} in dead zone [-15, +15]")

        # Signal 3: Declining ATR (volatility contracting)
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(float(tr))

        if len(trs) >= 20:
            atr_10 = sum(trs[-10:]) / 10
            atr_20 = sum(trs[-20:]) / 20
            if atr_20 > 0 and atr_10 / atr_20 < 0.50:
                chop_signals += 1
                reasons.append(f"ATR_CONTRACTING: 10-bar ATR is {atr_10/atr_20:.0%} of 20-bar (< 50%)")

        # Signal 4: Low/flat VIX
        if vix_level < 18:
            chop_signals += 1
            reasons.append(f"LOW_VIX: {vix_level:.1f} = complacent market, no fear = no direction")

        chop_score = (chop_signals / total_signals) * 100
        is_choppy = chop_signals >= 3  # Need 3 of 4 signals

        # When choppy: ICs are great, directional is death
        allowed_pillars = [1, 2, 3, 4] if not is_choppy else [1]

        if is_choppy:
            logger.warning(
                "chop_detected",
                chop_score=chop_score,
                signals=f"{chop_signals}/{total_signals}",
                reasons=reasons,
                allowed_pillars=allowed_pillars,
            )

        return {
            "is_choppy": is_choppy,
            "chop_score": round(chop_score, 1),
            "reasons": reasons,
            "allowed_pillars": allowed_pillars,
        }

    def is_ic_favorable_vix(self, vix_level: float) -> bool:
        """Check if VIX is in the iron condor sweet spot (25-35).

        SuperLuckeee: "The fastest way to grow a small account (iron condor strategy)
        — used when IV is high (volatility like the VIX is at 30)"

        When VIX is elevated, IC premium is fat and the strategy excels.
        This flag tells the engine to PRIORITIZE P1 (IC) even if directional
        signals suggest P2/P3/P4.

        Args:
            vix_level: Current VIX reading.

        Returns:
            True if ICs should be prioritized.
        """
        return 25.0 <= vix_level <= 35.0

    def _determine_pillars(self, score: float) -> list[int]:
        """Map bias score to active trading pillars.

        Pillars can overlap — e.g., score of +65 activates both P3 and P4.
        """
        ranges = self._cfg.pillar_ranges
        active: list[int] = []

        # P1: Iron Condors — neutral zone
        if ranges["p1_low"] <= score <= ranges["p1_high"]:
            active.append(1)

        # P2: Bear Call Spreads — strong bearish
        if score <= ranges["p2_threshold"]:
            active.append(2)

        # P3: Bull Put Spreads — strong bullish
        if score >= ranges["p3_threshold"]:
            active.append(3)

        # P4: Directional Scalps — high conviction either way
        if abs(score) >= ranges["p4_threshold"]:
            active.append(4)

        # P5: Butterfly Spreads — moderate conviction
        if "p5_threshold" in ranges and abs(score) >= ranges["p5_threshold"]:
            active.append(5)

        # Default to P1 if nothing activated (shouldn't happen, but safety net)
        if not active:
            active.append(1)

        return sorted(active)

    @staticmethod
    def _compute_ema(data: np.ndarray, period: int) -> np.ndarray:
        """Compute Exponential Moving Average."""
        multiplier = 2.0 / (period + 1)
        ema = np.zeros_like(data, dtype=float)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]
        return ema

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int = 14) -> float | None:
        """Compute RSI using Wilder's smoothing method."""
        if len(closes) < period + 1:
            return None

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Initial average gain/loss
        avg_gain = gains[:period].mean()
        avg_loss = losses[:period].mean()

        # Wilder's smoothing for remaining periods
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


#===============================================================================
# SECTION: SIGNALS - Black Swan Detector
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/black_swan.py (210 lines)
#-------------------------------------------------------------------------------

"""Black Swan Detector.

Monitors VIX level, SPX intraday % move, and put/call volume spikes to detect
market crash conditions. Returns a threat level that gates all trading activity.

Threat Levels:
    GREEN  — Normal market conditions, all systems go.
    YELLOW — Elevated volatility, reduce position sizing.
    RED    — Crash detected. Force-close ALL positions, no new entries until GREEN.

RED Triggers (any one is sufficient):
    - VIX > 35
    - SPX down > 3% intraday
    - Put/call volume anomaly > 3 standard deviations
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import TradierClient, Quote
from esther.data.alpaca import AlpacaClient

logger = structlog.get_logger(__name__)


class ThreatLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class BlackSwanStatus(BaseModel):
    """Current black swan detection status."""

    level: ThreatLevel
    vix: float = 0.0
    spx_change_pct: float = 0.0
    volume_z_score: float = 0.0
    triggers: list[str] = []
    timestamp: datetime = datetime.now()

    @property
    def is_safe(self) -> bool:
        return self.level == ThreatLevel.GREEN

    @property
    def should_close_all(self) -> bool:
        return self.level == ThreatLevel.RED


class BlackSwanDetector:
    """Monitors market conditions for crash signals.

    Checks three independent signals:
    1. VIX absolute level — fear gauge
    2. SPX intraday % change — actual damage
    3. Put/call volume spike — institutional hedging

    Any single RED trigger activates emergency protocol.
    """

    def __init__(self, client: TradierClient | AlpacaClient):
        self.client = client
        self._cfg = config().black_swan
        self._volume_history: list[float] = []
        self._max_volume_history = 100  # rolling window for z-score

    async def check(self) -> BlackSwanStatus:
        """Run all black swan checks and return current threat level.

        This is the main entry point — called every scan cycle.
        Fetches VIX and SPX data, computes all three signals,
        and returns the highest threat level triggered.
        """
        triggers: list[str] = []
        level = ThreatLevel.GREEN

        # Fetch VIX and SPX quotes in parallel
        try:
            quotes = await self.client.get_quotes(["VIX", "SPX"])
        except Exception as e:
            logger.error("black_swan_data_fetch_failed", error=str(e))
            # If we can't get data, assume elevated risk
            return BlackSwanStatus(
                level=ThreatLevel.YELLOW,
                triggers=["DATA_UNAVAILABLE: Could not fetch VIX/SPX quotes"],
            )

        vix_quote = next((q for q in quotes if q.symbol == "VIX"), None)
        spx_quote = next((q for q in quotes if q.symbol == "SPX"), None)

        vix_level = vix_quote.last if vix_quote else 0.0
        spx_change_pct = spx_quote.change_pct if spx_quote else 0.0

        # ── Signal 1: VIX Level ──────────────────────────────────
        if vix_level >= self._cfg.vix_red:
            triggers.append(f"VIX_RED: VIX at {vix_level:.1f} (threshold: {self._cfg.vix_red})")
            level = ThreatLevel.RED
        elif vix_level >= 30:
            # VIX 30+ = elevated regime but NOT shutdown — this is where reversals form.
            # Historical rhyme: April 2025 VIX spiked to 35, SPY bottomed $490 → +200 pts.
            # SuperLuckeee: "This set-up is forming next week" when VIX closed at 30.
            # Action: YELLOW alert, reduce sizing, but watch for VIX 35 = capitulation bottom.
            triggers.append(
                f"VIX_ELEVATED: VIX at {vix_level:.1f} — reversal regime forming. "
                f"Watch for VIX >35 = capitulation bottom (April 2025 pattern)."
            )
            if level != ThreatLevel.RED:
                level = ThreatLevel.YELLOW
        elif vix_level >= self._cfg.vix_yellow:
            triggers.append(f"VIX_YELLOW: VIX at {vix_level:.1f} (threshold: {self._cfg.vix_yellow})")
            if level != ThreatLevel.RED:
                level = ThreatLevel.YELLOW

        # ── Signal 2: SPX Intraday Move ──────────────────────────
        if spx_change_pct <= self._cfg.spx_drop_red:
            triggers.append(
                f"SPX_DROP_RED: SPX down {spx_change_pct:.2f}% "
                f"(threshold: {self._cfg.spx_drop_red}%)"
            )
            level = ThreatLevel.RED
        elif spx_change_pct <= self._cfg.spx_drop_yellow:
            triggers.append(
                f"SPX_DROP_YELLOW: SPX down {spx_change_pct:.2f}% "
                f"(threshold: {self._cfg.spx_drop_yellow}%)"
            )
            if level != ThreatLevel.RED:
                level = ThreatLevel.YELLOW

        # ── Signal 3: Volume Anomaly ─────────────────────────────
        volume_z = await self._check_volume_anomaly(spx_quote)
        if volume_z >= self._cfg.volume_std_threshold:
            triggers.append(
                f"VOLUME_SPIKE_RED: Put volume z-score {volume_z:.2f} "
                f"(threshold: {self._cfg.volume_std_threshold})"
            )
            level = ThreatLevel.RED

        status = BlackSwanStatus(
            level=level,
            vix=vix_level,
            spx_change_pct=spx_change_pct,
            volume_z_score=volume_z,
            triggers=triggers,
            timestamp=datetime.now(),
        )

        if level != ThreatLevel.GREEN:
            logger.warning(
                "black_swan_alert",
                level=level.value,
                vix=vix_level,
                spx_change=spx_change_pct,
                volume_z=volume_z,
                triggers=triggers,
            )
        else:
            logger.debug("black_swan_clear", vix=vix_level, spx_change=spx_change_pct)

        return status

    async def _check_volume_anomaly(self, spx_quote: Quote | None) -> float:
        """Check if current volume is anomalous relative to recent history.

        Uses a rolling z-score of SPX volume. A z-score > 3 means volume is
        3+ standard deviations above the mean — likely institutional panic hedging.

        Args:
            spx_quote: Current SPX quote with volume data.

        Returns:
            Z-score of current volume relative to rolling history.
        """
        if spx_quote is None or spx_quote.volume == 0:
            return 0.0

        current_volume = float(spx_quote.volume)
        self._volume_history.append(current_volume)

        # Keep rolling window bounded
        if len(self._volume_history) > self._max_volume_history:
            self._volume_history = self._volume_history[-self._max_volume_history:]

        # Need at least 10 data points for meaningful statistics
        if len(self._volume_history) < 10:
            return 0.0

        arr = np.array(self._volume_history)
        mean = arr.mean()
        std = arr.std()

        if std == 0:
            return 0.0

        z_score = (current_volume - mean) / std
        return round(z_score, 2)

    def reset_volume_history(self) -> None:
        """Clear volume history (e.g., at start of new trading day)."""
        self._volume_history.clear()
        logger.info("volume_history_reset")


#===============================================================================
# SECTION: SIGNALS - Inversion Engine
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/inversion_engine.py (243 lines)
#-------------------------------------------------------------------------------

"""Inversion Engine — Self-Correcting Bias Flipper.

Tracks win/loss records per ticker per direction. If a ticker accumulates
3+ consecutive losses in one direction, the engine flips the bias for that ticker.

This is the system's self-correction mechanism:
    - If we keep losing on bullish SPY trades → inversion engine says "go bearish on SPY"
    - No limit on inversions — it can flip back and forth as needed
    - Fully data-driven, no ego about being "right"

State persists to JSON so it survives restarts.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from esther.core.config import config

logger = structlog.get_logger(__name__)


class TradeResult(BaseModel):
    """Result of a completed trade for inversion tracking."""

    symbol: str
    direction: str  # "bull" or "bear"
    pnl: float
    won: bool
    timestamp: datetime = datetime.now()


class TickerState(BaseModel):
    """Tracking state for a single ticker + direction."""

    consecutive_losses: int = 0
    total_wins: int = 0
    total_losses: int = 0
    inverted: bool = False
    last_inversion: datetime | None = None


class InversionState(BaseModel):
    """Full inversion engine state — persisted to JSON."""

    # Key format: "SYMBOL:direction" e.g. "SPY:bull"
    trackers: dict[str, TickerState] = {}
    total_inversions: int = 0
    last_updated: datetime = datetime.now()


class InversionEngine:
    """Self-correcting bias flipper.

    How it works:
    1. After each trade completes, record_result() is called.
    2. Engine tracks consecutive losses per ticker per direction.
    3. If 3+ consecutive losses in one direction → flip the bias.
    4. get_adjusted_bias() returns the bias with inversions applied.

    The beauty: no hardcoded limits on inversions. If flipping was wrong,
    it'll flip back after 3 more losses. Fully adaptive.
    """

    def __init__(self, state_file: str | Path | None = None):
        self._cfg = config().inversion
        self._state_file = Path(state_file or self._cfg.state_file)
        self._state = self._load_state()

    def _load_state(self) -> InversionState:
        """Load persisted state from JSON file."""
        if self._state_file.exists():
            try:
                with open(self._state_file) as f:
                    data = json.load(f)
                state = InversionState.model_validate(data)
                logger.info(
                    "inversion_state_loaded",
                    trackers=len(state.trackers),
                    total_inversions=state.total_inversions,
                )
                return state
            except Exception as e:
                logger.warning("inversion_state_load_failed", error=str(e))

        return InversionState()

    def _save_state(self) -> None:
        """Persist state to JSON file."""
        self._state.last_updated = datetime.now()
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self._state_file, "w") as f:
            json.dump(self._state.model_dump(mode="json"), f, indent=2, default=str)

        logger.debug("inversion_state_saved")

    def record_result(self, result: TradeResult) -> bool:
        """Record a completed trade result and check for inversion trigger.

        Args:
            result: The completed trade result.

        Returns:
            True if this result triggered a new inversion.
        """
        key = f"{result.symbol}:{result.direction}"

        if key not in self._state.trackers:
            self._state.trackers[key] = TickerState()

        tracker = self._state.trackers[key]
        triggered_inversion = False

        if result.won:
            # Win resets consecutive loss counter
            tracker.consecutive_losses = 0
            tracker.total_wins += 1

            # If we were inverted and winning, the inversion is working
            if tracker.inverted:
                logger.info(
                    "inversion_validated",
                    symbol=result.symbol,
                    direction=result.direction,
                )
        else:
            # Loss increments the counter
            tracker.consecutive_losses += 1
            tracker.total_losses += 1

            # Check if we hit the inversion trigger
            if tracker.consecutive_losses >= self._cfg.consecutive_loss_trigger:
                tracker.inverted = not tracker.inverted  # Toggle
                tracker.consecutive_losses = 0  # Reset counter
                tracker.last_inversion = datetime.now()
                self._state.total_inversions += 1
                triggered_inversion = True

                logger.warning(
                    "inversion_triggered",
                    symbol=result.symbol,
                    direction=result.direction,
                    now_inverted=tracker.inverted,
                    total_inversions=self._state.total_inversions,
                )

        self._save_state()
        return triggered_inversion

    def get_adjusted_bias(self, symbol: str, raw_bias: float) -> float:
        """Apply inversions to a raw bias score.

        If the inversion engine has flagged this ticker's direction,
        flip the bias sign.

        Args:
            symbol: Ticker symbol.
            raw_bias: Original bias score from BiasEngine (-100 to +100).

        Returns:
            Adjusted bias score (may be flipped).
        """
        direction = "bull" if raw_bias > 0 else "bear"
        key = f"{symbol}:{direction}"

        tracker = self._state.trackers.get(key)

        if tracker and tracker.inverted:
            adjusted = -raw_bias
            logger.info(
                "bias_inverted",
                symbol=symbol,
                original=raw_bias,
                adjusted=adjusted,
                direction=direction,
            )
            return adjusted

        return raw_bias

    def is_inverted(self, symbol: str, direction: str) -> bool:
        """Check if a specific ticker+direction is currently inverted."""
        key = f"{symbol}:{direction}"
        tracker = self._state.trackers.get(key)
        return tracker.inverted if tracker else False

    def get_stats(self, symbol: str | None = None) -> dict[str, Any]:
        """Get inversion stats, optionally filtered by symbol.

        Returns:
            Dict with inversion statistics.
        """
        if symbol:
            relevant = {
                k: v for k, v in self._state.trackers.items()
                if k.startswith(f"{symbol}:")
            }
        else:
            relevant = self._state.trackers

        return {
            "trackers": {
                k: {
                    "consecutive_losses": v.consecutive_losses,
                    "win_rate": (
                        v.total_wins / (v.total_wins + v.total_losses)
                        if (v.total_wins + v.total_losses) > 0
                        else 0.0
                    ),
                    "inverted": v.inverted,
                    "last_inversion": str(v.last_inversion) if v.last_inversion else None,
                }
                for k, v in relevant.items()
            },
            "total_inversions": self._state.total_inversions,
        }

    def reset(self, symbol: str | None = None) -> None:
        """Reset inversion state.

        Args:
            symbol: If provided, only reset this symbol. Otherwise reset everything.
        """
        if symbol:
            keys_to_remove = [
                k for k in self._state.trackers if k.startswith(f"{symbol}:")
            ]
            for k in keys_to_remove:
                del self._state.trackers[k]
            logger.info("inversion_reset", symbol=symbol)
        else:
            self._state = InversionState()
            logger.info("inversion_full_reset")

        self._save_state()


#===============================================================================
# SECTION: SIGNALS - Sage (Intel Officer)
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/sage.py (615 lines)
#-------------------------------------------------------------------------------

"""SAGE 🔭 — The Intel Officer. Always-on market intelligence gathering.

Sage is Esther's eyes and ears. While the other agents debate and judge
individual trades, Sage is constantly scanning the battlefield:

    - Sunday 8 PM: Weekly flow review + set weekly plan
    - Mon-Fri 6 AM: Overnight flow + dark pool changes
    - Mon-Fri 8:30 AM: Economic data releases (CPI/PPI/NFP)
    - Mon-Fri continuous: Real-time flow monitoring during market hours
    - Mon-Fri 4 PM: EOD summary + overnight positioning

Sage does NOT make trade decisions. He gathers, organizes, and delivers
intelligence to the team:
    - Kimi uses Sage's intel for risk analysis
    - Riki/Abi use Sage's intel to build their arguments
    - Kage uses Sage's intel for the final verdict

Sage is PURE DATA — no Claude API calls needed. Fast, cheap, always running.
He pulls from Unusual Whales, Tradier, economic calendars, and the LEAP watchlist.

The 5-agent structure:
    SAGE 🔭 (Intel) → KIMI 🔬 (Research/Risk) → RIKI 🐂 (Bull) + ABI 🐻 (Bear) → KIMI 🔬 (Challenge) → KAGE ⚖️ (Final)
"""

from __future__ import annotations

import json
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config, env
from esther.signals.flow import FlowAnalyzer, FlowSummary, UnusualWhalesClient
from esther.signals.levels import LevelTracker, KeyLevels
from esther.signals.regime import RegimeDetector, RegimeResult
from esther.signals.calendar import CalendarModule
from esther.signals.watchlist import WatchlistMonitor, WatchlistAlert
from esther.signals.premarket import PreMarketResearcher, PreMarketReport

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")

# Persistence
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INTEL_DIR = _PROJECT_ROOT / "data" / "intel"


class FlowIntel(BaseModel):
    """Flow intelligence snapshot."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(ET))
    spy_call_premium: float = 0.0
    spy_put_premium: float = 0.0
    spy_put_call_ratio: float = 0.0
    spy_bullish_premium: float = 0.0
    spy_bearish_premium: float = 0.0
    flow_bias_score: float = 0.0
    flow_direction: str = ""  # BULLISH, BEARISH, NEUTRAL
    max_pain_spy: float = 0.0
    net_delta: float = 0.0
    net_delta_direction: str = ""  # BULLISH or BEARISH
    top_alerts: list[dict] = []
    dark_pool_blocks: list[dict] = []
    sweep_count: int = 0
    floor_trade_count: int = 0


class MarketIntel(BaseModel):
    """Complete market intelligence package — Sage's output."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(ET))
    scan_type: str = ""  # "sunday_review", "overnight", "premarket", "intraday", "eod"

    # Prices
    spy_price: float = 0.0
    spx_price: float = 0.0
    vix_level: float = 0.0
    qqq_price: float = 0.0
    iwm_price: float = 0.0

    # Flow
    flow: FlowIntel = Field(default_factory=FlowIntel)

    # Levels
    key_levels: dict[str, Any] = {}  # {symbol: {pm_high, pm_low, etc}}

    # Regime
    regime_state: str = ""
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0

    # Calendar
    is_event_day: bool = False
    event_name: str = ""
    events_this_week: list[dict] = []

    # LEAP watchlist
    watchlist_alerts: list[str] = []
    watchlist_approaching: list[str] = []

    # Expected moves (from options pricing)
    spy_expected_move: float = 0.0
    spy_range_low: float = 0.0
    spy_range_high: float = 0.0
    spx_expected_move: float = 0.0
    spx_range_low: float = 0.0
    spx_range_high: float = 0.0

    # Sage's summary (plain text intelligence brief)
    intel_brief: str = ""

    # Risk flags
    risk_flags: list[str] = []


class Sage:
    """The Intel Officer — always-on market intelligence gathering.

    Sage runs continuously, pulling data from all sources and packaging
    it into MarketIntel objects that the rest of the team consumes.

    Usage:
        sage = Sage()
        intel = await sage.scan_now()  # Run appropriate scan for current time
        intel = await sage.sunday_review()  # Force Sunday review
        intel = await sage.overnight_scan()  # Force overnight scan
        brief = sage.get_latest_brief()  # Get last intel brief as text
    """

    def __init__(self):
        self._env = env()
        self._cfg = config()
        self._uw = UnusualWhalesClient(api_key=self._env.unusual_whales_api_key)
        self._flow = FlowAnalyzer()
        self._levels = LevelTracker()
        self._regime = RegimeDetector()
        self._calendar = CalendarModule()
        self._watchlist = WatchlistMonitor()
        self._premarket = PreMarketResearcher()

        self._latest_intel: MarketIntel | None = None
        self._intel_history: list[MarketIntel] = []

        # Ensure intel directory exists
        INTEL_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def latest(self) -> MarketIntel | None:
        """Get the most recent intelligence report."""
        return self._latest_intel

    # ── Auto-detect which scan to run ────────────────────────────

    async def scan_now(self) -> MarketIntel:
        """Run the appropriate scan based on current time.

        Sunday 6-10 PM → sunday_review
        Mon-Fri 4-9 AM → overnight_scan
        Mon-Fri 9-9:30 AM → premarket_scan
        Mon-Fri 9:30-4 PM → intraday_scan
        Mon-Fri 4-5 PM → eod_scan
        """
        now = datetime.now(ET)
        weekday = now.weekday()  # 0=Mon, 6=Sun
        hour = now.hour

        if weekday == 6 and hour >= 18:  # Sunday evening
            return await self.sunday_review()
        elif hour < 9:
            return await self.overnight_scan()
        elif hour == 9 and now.minute < 30:
            return await self.premarket_scan()
        elif 9 <= hour < 16:
            return await self.intraday_scan()
        elif hour >= 16:
            return await self.eod_scan()
        else:
            return await self.overnight_scan()

    # ── Sunday Night Review ──────────────────────────────────────

    async def sunday_review(self) -> MarketIntel:
        """Full weekly intelligence review — Sunday evening.

        Like @SuperLuckeee: "Every Sunday I plan the whole week
        based on orderflow and place bubbles where the flow is."
        """
        logger.info("sage_sunday_review_started")
        intel = MarketIntel(scan_type="sunday_review")

        # Pull Friday's flow data
        await self._populate_flow(intel)

        # Get prices from last close
        await self._populate_prices(intel)

        # Check economic calendar for the week
        await self._populate_calendar(intel)

        # Scan LEAP watchlist
        await self._populate_watchlist(intel)

        # Calculate expected moves
        self._calculate_expected_moves(intel)

        # Build the intel brief
        intel.intel_brief = self._build_sunday_brief(intel)

        # Risk flags
        self._assess_risk_flags(intel)

        # Store
        self._store_intel(intel)

        logger.info("sage_sunday_review_complete", brief_len=len(intel.intel_brief))
        return intel

    # ── Overnight Scan ───────────────────────────────────────────

    async def overnight_scan(self) -> MarketIntel:
        """Early morning scan — what happened overnight?"""
        logger.info("sage_overnight_scan_started")
        intel = MarketIntel(scan_type="overnight")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_calendar(intel)
        await self._populate_watchlist(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_overnight_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_overnight_scan_complete")
        return intel

    # ── Pre-Market Scan ──────────────────────────────────────────

    async def premarket_scan(self) -> MarketIntel:
        """9:00-9:30 AM scan — final intel before market open."""
        logger.info("sage_premarket_scan_started")
        intel = MarketIntel(scan_type="premarket")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_calendar(intel)
        await self._populate_watchlist(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_premarket_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_premarket_scan_complete")
        return intel

    # ── Intraday Scan ────────────────────────────────────────────

    async def intraday_scan(self) -> MarketIntel:
        """During market hours — quick flow check."""
        logger.info("sage_intraday_scan_started")
        intel = MarketIntel(scan_type="intraday")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        self._calculate_expected_moves(intel)

        intel.intel_brief = self._build_intraday_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        return intel

    # ── EOD Scan ─────────────────────────────────────────────────

    async def eod_scan(self) -> MarketIntel:
        """End of day — what happened today, what's the overnight setup?"""
        logger.info("sage_eod_scan_started")
        intel = MarketIntel(scan_type="eod")

        await self._populate_flow(intel)
        await self._populate_prices(intel)
        await self._populate_watchlist(intel)

        intel.intel_brief = self._build_eod_brief(intel)
        self._assess_risk_flags(intel)
        self._store_intel(intel)

        logger.info("sage_eod_scan_complete")
        return intel

    # ── Data Population Methods ──────────────────────────────────

    async def _populate_flow(self, intel: MarketIntel) -> None:
        """Pull flow data from Unusual Whales."""
        try:
            # SPY options volume
            vol_data = await self._uw.get_options_volume("SPY")
            if vol_data:
                v = vol_data[0] if isinstance(vol_data[0], dict) else {}
                intel.flow.spy_call_premium = float(v.get("call_premium", 0))
                intel.flow.spy_put_premium = float(v.get("put_premium", 0))
                intel.flow.spy_bullish_premium = float(v.get("bullish_premium", 0))
                intel.flow.spy_bearish_premium = float(v.get("bearish_premium", 0))

                if intel.flow.spy_call_premium > 0:
                    intel.flow.spy_put_call_ratio = intel.flow.spy_put_premium / intel.flow.spy_call_premium

            # Flow bias
            flow_entries = await self._flow.get_flow("SPY")
            if flow_entries:
                summary = self._flow.analyze_flow(flow_entries)
                intel.flow.flow_bias_score = summary.flow_bias_score

            # Direction
            if intel.flow.flow_bias_score > 20:
                intel.flow.flow_direction = "BULLISH"
            elif intel.flow.flow_bias_score < -20:
                intel.flow.flow_direction = "BEARISH"
            else:
                intel.flow.flow_direction = "NEUTRAL"

            # Max pain
            mp = await self._uw.get_max_pain("SPY")
            if mp:
                m = mp[0] if isinstance(mp, list) else mp
                if isinstance(m, dict):
                    intel.flow.max_pain_spy = float(m.get("max_pain", 0))

            # Greek exposure
            ge = await self._uw.get_greek_exposure("SPY")
            if ge:
                g = ge[0] if isinstance(ge, list) else ge
                if isinstance(g, dict):
                    cd = float(g.get("call_delta", 0))
                    pd = float(g.get("put_delta", 0))
                    intel.flow.net_delta = cd + pd
                    intel.flow.net_delta_direction = "BULLISH" if intel.flow.net_delta > 0 else "BEARISH"

            # Flow alerts
            alerts = await self._uw.get_flow_alerts(ticker="SPY", limit=5)
            intel.flow.top_alerts = [a if isinstance(a, dict) else {} for a in alerts[:5]]

            # Dark pool
            dp = await self._uw.get_dark_pool(limit=7)
            intel.flow.dark_pool_blocks = [d if isinstance(d, dict) else {} for d in dp[:7]]

            # Count sweeps and floor trades
            for a in alerts:
                if isinstance(a, dict):
                    if a.get("has_sweep"):
                        intel.flow.sweep_count += 1
                    if a.get("has_floor"):
                        intel.flow.floor_trade_count += 1

        except Exception as e:
            logger.error("sage_flow_error", error=str(e))

    async def _populate_prices(self, intel: MarketIntel) -> None:
        """Pull current/last close prices from Tradier."""
        try:
            live_key = self._env.tradier_live_api_key if hasattr(self._env, 'tradier_live_api_key') else self._env.tradier_api_key
            headers = {"Authorization": f"Bearer {live_key}", "Accept": "application/json"}

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.tradier.com/v1/markets/quotes",
                    headers=headers,
                    params={"symbols": "SPY,SPX,VIX,QQQ,IWM"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    quotes = data.get("quotes", {}).get("quote", [])
                    for q in quotes:
                        sym = q.get("symbol", "")
                        last = q.get("last", 0) or q.get("close", 0) or 0
                        if sym == "SPY":
                            intel.spy_price = float(last)
                        elif sym == "SPX":
                            intel.spx_price = float(last)
                        elif sym == "VIX":
                            intel.vix_level = float(last)
                        elif sym == "QQQ":
                            intel.qqq_price = float(last)
                        elif sym == "IWM":
                            intel.iwm_price = float(last)
        except Exception as e:
            logger.error("sage_price_error", error=str(e))

    async def _populate_calendar(self, intel: MarketIntel) -> None:
        """Check economic calendar."""
        try:
            event = self._calendar.get_todays_events()
            if event:
                intel.is_event_day = True
                intel.event_name = str(event)

            week_events = self._calendar.get_week_events()
            if week_events:
                intel.events_this_week = [
                    {"date": str(e.get("date", "")), "name": str(e.get("name", ""))}
                    for e in week_events
                ] if isinstance(week_events, list) else []
        except Exception as e:
            logger.warning("sage_calendar_error", error=str(e))

    async def _populate_watchlist(self, intel: MarketIntel) -> None:
        """Scan LEAP watchlist for buy zone alerts."""
        try:
            # Get prices for watchlist stocks
            live_key = self._env.tradier_live_api_key if hasattr(self._env, 'tradier_live_api_key') else self._env.tradier_api_key
            headers = {"Authorization": f"Bearer {live_key}", "Accept": "application/json"}

            symbols = [e.symbol for e in self._watchlist.watchlist if e.buy_zone_high > 0]
            if symbols:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://api.tradier.com/v1/markets/quotes",
                        headers=headers,
                        params={"symbols": ",".join(symbols)},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        quotes = data.get("quotes", {}).get("quote", [])
                        if isinstance(quotes, dict):
                            quotes = [quotes]
                        prices = {q["symbol"]: float(q.get("last", 0) or 0) for q in quotes}
                        self._watchlist.update_prices(prices)

                alerts = self._watchlist.scan()
                intel.watchlist_alerts = [a.symbol for a in alerts if a.status.value == "IN_ZONE"]
                intel.watchlist_approaching = [a.symbol for a in alerts if a.status.value == "ENTERING_ZONE"]

                # Also check the stored summary
                summary = self._watchlist.get_summary()
                if summary.get("in_zone_symbols"):
                    intel.watchlist_alerts = summary["in_zone_symbols"]
                if summary.get("approaching_symbols"):
                    intel.watchlist_approaching = summary["approaching_symbols"]
        except Exception as e:
            logger.warning("sage_watchlist_error", error=str(e))

    def _calculate_expected_moves(self, intel: MarketIntel) -> None:
        """Calculate expected moves from VIX."""
        if intel.vix_level > 0 and intel.spy_price > 0:
            # Expected daily move = SPY × VIX / sqrt(252) / 100
            import math
            daily_move = intel.spy_price * (intel.vix_level / 100) / math.sqrt(252)
            intel.spy_expected_move = round(daily_move, 2)
            intel.spy_range_low = round(intel.spy_price - daily_move, 2)
            intel.spy_range_high = round(intel.spy_price + daily_move, 2)

            if intel.spx_price > 0:
                spx_move = intel.spx_price * (intel.vix_level / 100) / math.sqrt(252)
                intel.spx_expected_move = round(spx_move, 2)
                intel.spx_range_low = round(intel.spx_price - spx_move, 2)
                intel.spx_range_high = round(intel.spx_price + spx_move, 2)

    def _assess_risk_flags(self, intel: MarketIntel) -> None:
        """Flag anything the team should be aware of."""
        flags = []

        if intel.vix_level >= 35:
            flags.append("🔴 VIX >= 35 — CAPITULATION ZONE — watch for reversal")
        elif intel.vix_level >= 30:
            flags.append("🟡 VIX 30+ — IC SWEET SPOT — elevated premium, favor P1")
        elif intel.vix_level >= 25:
            flags.append("🟡 VIX 25+ — ELEVATED — reduce directional, favor credit spreads")

        if intel.flow.spy_put_call_ratio > 3:
            flags.append(f"🐻 EXTREME PUT BUYING — P/C ratio {intel.flow.spy_put_call_ratio:.1f}x")
        elif intel.flow.spy_put_call_ratio > 2:
            flags.append(f"🐻 Heavy put buying — P/C ratio {intel.flow.spy_put_call_ratio:.1f}x")

        if intel.flow.net_delta < -200_000_000:
            flags.append(f"🐻 MASSIVE BEARISH DELTA — {intel.flow.net_delta:,.0f}")

        if intel.is_event_day:
            flags.append(f"📅 EVENT DAY: {intel.event_name} — reduce size 50%")

        if intel.flow.max_pain_spy > 0 and intel.spy_price > 0:
            gap = intel.flow.max_pain_spy - intel.spy_price
            if abs(gap) > 15:
                flags.append(f"🧲 MAX PAIN GAP: SPY ${intel.spy_price:.0f} vs max pain ${intel.flow.max_pain_spy:.0f} (${gap:+.0f})")

        intel.risk_flags = flags

    # ── Brief Builders ───────────────────────────────────────────

    def _build_sunday_brief(self, intel: MarketIntel) -> str:
        """Build Sunday night intelligence brief."""
        lines = [
            f"🔭 SAGE SUNDAY INTEL — Week of {datetime.now(ET).strftime('%b %d')}",
            "",
            f"📊 MARKET: SPY ${intel.spy_price:.2f} | SPX ${intel.spx_price:.2f} | VIX {intel.vix_level:.1f}",
            f"📈 QQQ ${intel.qqq_price:.2f} | IWM ${intel.iwm_price:.2f}",
            "",
            f"🐋 FLOW ({intel.flow.flow_direction}):",
            f"  Put/Call: {intel.flow.spy_put_call_ratio:.1f}x | Bias: {intel.flow.flow_bias_score:+.1f}",
            f"  Calls: ${intel.flow.spy_call_premium/1e9:.2f}B | Puts: ${intel.flow.spy_put_premium/1e9:.2f}B",
            f"  Net Delta: {intel.flow.net_delta:,.0f} ({intel.flow.net_delta_direction})",
            f"  Max Pain: ${intel.flow.max_pain_spy:.0f}",
            "",
            f"📐 EXPECTED MOVE:",
            f"  SPY: ±${intel.spy_expected_move:.0f} → ${intel.spy_range_low:.0f} — ${intel.spy_range_high:.0f}",
            f"  SPX: ±${intel.spx_expected_move:.0f} → ${intel.spx_range_low:.0f} — ${intel.spx_range_high:.0f}",
        ]

        if intel.risk_flags:
            lines.append("")
            lines.append("⚠️ RISK FLAGS:")
            for flag in intel.risk_flags:
                lines.append(f"  {flag}")

        if intel.watchlist_alerts or intel.watchlist_approaching:
            lines.append("")
            lines.append("📋 LEAP WATCHLIST:")
            for s in intel.watchlist_alerts:
                lines.append(f"  🟢 {s} IN BUY ZONE")
            for s in intel.watchlist_approaching:
                lines.append(f"  🟡 {s} approaching zone")

        return "\n".join(lines)

    def _build_overnight_brief(self, intel: MarketIntel) -> str:
        """Build overnight intelligence brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "OVERNIGHT INTEL")

    def _build_premarket_brief(self, intel: MarketIntel) -> str:
        """Build pre-market brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "PRE-MARKET INTEL")

    def _build_intraday_brief(self, intel: MarketIntel) -> str:
        """Build intraday quick brief."""
        return (
            f"🔭 SAGE: SPY ${intel.spy_price:.2f} | VIX {intel.vix_level:.1f} | "
            f"Flow: {intel.flow.flow_direction} ({intel.flow.flow_bias_score:+.1f}) | "
            f"P/C: {intel.flow.spy_put_call_ratio:.1f}x"
        )

    def _build_eod_brief(self, intel: MarketIntel) -> str:
        """Build end-of-day brief."""
        return self._build_sunday_brief(intel).replace("SUNDAY INTEL", "EOD INTEL")

    # ── Persistence ──────────────────────────────────────────────

    def _store_intel(self, intel: MarketIntel) -> None:
        """Store intel to file and memory."""
        self._latest_intel = intel
        self._intel_history.append(intel)

        # Keep last 50 scans in memory
        if len(self._intel_history) > 50:
            self._intel_history = self._intel_history[-50:]

        # Persist to JSON
        try:
            filename = f"{intel.scan_type}_{datetime.now(ET).strftime('%Y-%m-%d_%H%M')}.json"
            filepath = INTEL_DIR / filename
            with open(filepath, "w") as f:
                json.dump(intel.model_dump(mode="json"), f, indent=2, default=str)
            logger.debug("sage_intel_stored", path=str(filepath))
        except Exception as e:
            logger.warning("sage_store_error", error=str(e))

    def get_latest_brief(self) -> str:
        """Get the most recent intel brief as plain text."""
        if self._latest_intel:
            return self._latest_intel.intel_brief
        return "No intel available yet. Run scan_now() first."

    def get_intel_for_debate(self) -> dict[str, Any]:
        """Package current intel for the AI debate system.

        This is what gets passed to Kimi/Riki/Abi/Kage as context.
        """
        if not self._latest_intel:
            return {}

        i = self._latest_intel
        return {
            "spy_price": i.spy_price,
            "spx_price": i.spx_price,
            "vix_level": i.vix_level,
            "flow_direction": i.flow.flow_direction,
            "flow_bias": i.flow.flow_bias_score,
            "put_call_ratio": i.flow.spy_put_call_ratio,
            "max_pain": i.flow.max_pain_spy,
            "net_delta": i.flow.net_delta,
            "net_delta_direction": i.flow.net_delta_direction,
            "expected_move_spy": i.spy_expected_move,
            "spy_range": f"${i.spy_range_low:.0f} — ${i.spy_range_high:.0f}",
            "regime_state": i.regime_state,
            "is_event_day": i.is_event_day,
            "event_name": i.event_name,
            "risk_flags": i.risk_flags,
            "watchlist_in_zone": i.watchlist_alerts,
            "intel_brief": i.intel_brief,
        }

    def format_telegram(self, intel: MarketIntel | None = None) -> str:
        """Format intel for Telegram delivery."""
        i = intel or self._latest_intel
        if not i:
            return "No intel available."
        return i.intel_brief


#===============================================================================
# SECTION: SIGNALS - Quality Filter
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/quality_filter.py (422 lines)
#-------------------------------------------------------------------------------

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


#===============================================================================
# SECTION: SIGNALS - Flow Analysis
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/flow.py (1407 lines)
#-------------------------------------------------------------------------------

"""Order Flow Data Integration — Institutional Flow Analysis.

Tracks institutional order flow to detect where big money is positioning.
Flow data is the single most important signal (weight: 0.25) because
institutions move markets — retail follows.

Data providers (priority order):
    1. Unusual Whales API (PRIMARY) — flow alerts, net premium ticks,
       dark pool, max pain, greek exposure
    2. Tradier time & sales (FALLBACK) — option chains with volume
    3. Manual CSV import — for backtesting

Key concepts:
    - FlowEntry: Individual option trade with premium, side, etc.
    - FlowBubble: Cluster of big trades at a specific strike
    - FlowSummary: Aggregated analysis of flow for a symbol
    - Flow Bias: -100 to +100 score based on put/call premium ratio
    - DarkPoolEntry: Dark pool print with size, price, NBBO context
    - MaxPainData: Max pain strike with surrounding levels
    - GreekExposure: Aggregate greek exposure for positioning analysis
"""

from __future__ import annotations

import csv
import time
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from esther.core.config import config, env

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Cache TTLs (seconds)
# ---------------------------------------------------------------------------
_CACHE_TTL_FLOW_ALERTS = 60        # flow alerts refresh every minute
_CACHE_TTL_OPTIONS_VOLUME = 300    # daily aggregates refresh every 5 min
_CACHE_TTL_NET_PREM_TICKS = 60    # intraday ticks refresh every minute
_CACHE_TTL_DARK_POOL = 120         # dark pool refresh every 2 min
_CACHE_TTL_MAX_PAIN = 600          # max pain refresh every 10 min
_CACHE_TTL_GREEK_EXPOSURE = 300    # greeks refresh every 5 min


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OptionSide(str, Enum):
    """Trade side — buy or sell at the ask/bid."""
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class FlowOptionType(str, Enum):
    CALL = "call"
    PUT = "put"


# ---------------------------------------------------------------------------
# Core Models (PRESERVED interfaces — other modules depend on these)
# ---------------------------------------------------------------------------

class FlowEntry(BaseModel):
    """A single options trade from the flow feed."""

    symbol: str
    strike: float
    expiry: str  # ISO date string
    option_type: FlowOptionType
    premium: float  # Total dollar premium (price * volume * 100)
    volume: int
    price: float  # Per-contract price
    exchange: str = ""
    side: OptionSide = OptionSide.UNKNOWN
    timestamp: datetime = Field(default_factory=datetime.now)
    open_interest: int = 0

    # --- Unusual Whales enrichment fields (optional, zero-default) ---
    ask_side_premium: float = 0.0
    bid_side_premium: float = 0.0
    sweep_volume: int = 0
    floor_volume: int = 0
    multileg_volume: int = 0
    volume_oi_ratio: float = 0.0
    iv_start: float = 0.0
    iv_end: float = 0.0
    underlying_price: float = 0.0
    alert_rule: str = ""
    has_sweep: bool = False
    has_floor: bool = False
    has_multileg: bool = False


class FlowBubble(BaseModel):
    """Cluster of big money at a specific strike/expiry.

    When multiple large trades land at the same strike, it signals
    institutional conviction at that level. Used for target setting.
    """

    symbol: str
    strike: float
    expiry: str
    total_premium: float
    trade_count: int
    net_side: OptionSide  # net direction of the cluster
    dominant_type: FlowOptionType  # calls or puts
    avg_price: float


class FlowSummary(BaseModel):
    """Aggregated flow analysis for a symbol on a given day."""

    symbol: str
    date: str
    total_call_premium: float = 0.0
    total_put_premium: float = 0.0
    net_premium: float = 0.0  # positive = call-heavy, negative = put-heavy
    call_volume: int = 0
    put_volume: int = 0
    put_call_ratio: float = 0.0
    biggest_trades: list[FlowEntry] = []
    unusual_trades: list[FlowEntry] = []
    flow_bubbles: list[FlowBubble] = []
    flow_bias_score: float = 0.0  # -100 to +100


# ---------------------------------------------------------------------------
# New Models (Unusual Whales enrichment)
# ---------------------------------------------------------------------------

class DarkPoolEntry(BaseModel):
    """A single dark pool print."""

    ticker: str
    price: float
    size: int
    volume: int = 0
    premium: float = 0.0
    executed_at: datetime | None = None
    nbbo_ask: float = 0.0
    nbbo_bid: float = 0.0
    market_center: str = ""


class MaxPainData(BaseModel):
    """Max pain data for a symbol/expiry."""

    symbol: str
    expiry: str = ""
    max_pain: float = 0.0
    close: float = 0.0
    open: float = 0.0
    next_upper_strike: float = 0.0
    next_lower_strike: float = 0.0


class GreekExposure(BaseModel):
    """Aggregate greek exposure across all strikes for a symbol."""

    symbol: str
    date: str = ""
    call_delta: float = 0.0
    put_delta: float = 0.0
    net_delta: float = 0.0
    call_gamma: float = 0.0
    put_gamma: float = 0.0
    net_gamma: float = 0.0
    call_charm: float = 0.0
    put_charm: float = 0.0
    call_vanna: float = 0.0
    put_vanna: float = 0.0


class OptionsVolume(BaseModel):
    """Daily options volume aggregates."""

    symbol: str
    date: str = ""
    call_volume: int = 0
    put_volume: int = 0
    call_premium: float = 0.0
    put_premium: float = 0.0
    net_call_premium: float = 0.0
    net_put_premium: float = 0.0
    bearish_premium: float = 0.0
    bullish_premium: float = 0.0
    put_open_interest: int = 0
    call_open_interest: int = 0
    call_volume_ask_side: int = 0
    call_volume_bid_side: int = 0
    put_volume_ask_side: int = 0
    put_volume_bid_side: int = 0


class NetPremiumTick(BaseModel):
    """Minute-by-minute net premium tick."""

    tape_time: str = ""
    call_volume: int = 0
    put_volume: int = 0
    net_call_premium: float = 0.0
    net_put_premium: float = 0.0
    net_call_volume: int = 0
    net_put_volume: int = 0
    net_delta: float = 0.0


# ---------------------------------------------------------------------------
# Timed Cache helper
# ---------------------------------------------------------------------------

class _TimedCache:
    """Simple TTL cache for API responses."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float) -> Any | None:
        if key in self._store:
            ts, val = self._store[key]
            if time.time() - ts < ttl:
                return val
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Unusual Whales Client
# ---------------------------------------------------------------------------

class UnusualWhalesClient:
    """Async HTTP client for the Unusual Whales API.

    All methods return parsed dicts/lists or raise on failure.
    Auth via ``Authorization: Bearer <key>`` header.
    """

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self._cache = _TimedCache()

    # ----- internal helpers ------------------------------------------------

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_ttl: float = 60,
    ) -> Any:
        """Issue GET request with optional caching.

        Returns parsed JSON body (usually a dict with a ``data`` key).
        """
        if cache_key:
            cached = self._cache.get(cache_key, cache_ttl)
            if cached is not None:
                return cached

        url = f"{self.BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        if cache_key:
            self._cache.set(cache_key, data)
        return data

    # ----- public endpoints -----------------------------------------------

    async def get_flow_alerts(
        self, ticker: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """GET /api/option-trades/flow-alerts — institutional flow alerts.

        This is THE key endpoint. Returns individual option trades flagged
        as unusual by Unusual Whales' detection engine.
        """
        cache_key = f"flow_alerts:{ticker}:{limit}"
        data = await self._get(
            "/api/option-trades/flow-alerts",
            params={"ticker": ticker, "limit": limit},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_FLOW_ALERTS,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_options_volume(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/options-volume — daily aggregates."""
        cache_key = f"opt_vol:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/options-volume",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_OPTIONS_VOLUME,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_net_premium_ticks(
        self, ticker: str, target_date: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/net-prem-ticks — minute-by-minute flow."""
        if target_date is None:
            target_date = date.today().isoformat()
        cache_key = f"net_prem:{ticker}:{target_date}"
        data = await self._get(
            f"/api/stock/{ticker}/net-prem-ticks",
            params={"date": target_date},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_NET_PREM_TICKS,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_option_contracts(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/option-contracts — per-contract data."""
        cache_key = f"opt_contracts:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/option-contracts",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_OPTIONS_VOLUME,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_dark_pool(self, limit: int = 10) -> list[dict[str, Any]]:
        """GET /api/darkpool/recent — recent dark pool prints."""
        cache_key = f"darkpool:{limit}"
        data = await self._get(
            "/api/darkpool/recent",
            params={"limit": limit},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_DARK_POOL,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_max_pain(self, ticker: str) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/max-pain — max pain per expiry."""
        cache_key = f"max_pain:{ticker}"
        data = await self._get(
            f"/api/stock/{ticker}/max-pain",
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_MAX_PAIN,
        )
        return data.get("data", data) if isinstance(data, dict) else data

    async def get_greek_exposure(
        self, ticker: str, target_date: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/stock/{ticker}/greek-exposure — aggregate greek exposure."""
        if target_date is None:
            target_date = date.today().isoformat()
        cache_key = f"greeks:{ticker}:{target_date}"
        data = await self._get(
            f"/api/stock/{ticker}/greek-exposure",
            params={"date": target_date},
            cache_key=cache_key,
            cache_ttl=_CACHE_TTL_GREEK_EXPOSURE,
        )
        return data.get("data", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# FlowAnalyzer  (preserved interface + UW enrichment)
# ---------------------------------------------------------------------------

class FlowAnalyzer:
    """Analyzes options flow data from multiple providers.

    The flow analyzer fetches institutional flow data, identifies unusual
    activity (large premium trades), clusters trades into bubbles at
    key strikes, and produces a directional bias score.

    Provider priority:
        1. Unusual Whales flow alerts (rich signal: sweep, floor, side prem)
        2. Tradier option chains (fallback if UW key missing or API fails)
    """

    # Tradier endpoints (fallback)
    PROD_BASE = "https://api.tradier.com/v1"
    SANDBOX_BASE = "https://sandbox.tradier.com/v1"

    def __init__(self) -> None:
        self._cfg = config().flow
        self._env = env()

        # Unusual Whales client (primary)
        self._uw: UnusualWhalesClient | None = None
        if self._env.unusual_whales_api_key:
            self._uw = UnusualWhalesClient(self._env.unusual_whales_api_key)
            logger.info("flow_provider_init", provider="unusual_whales")
        else:
            logger.warning(
                "flow_provider_fallback",
                reason="UNUSUAL_WHALES_API_KEY not set, using Tradier",
            )

        # Tradier (fallback)
        self._base_url = (
            self.SANDBOX_BASE if self._env.tradier_sandbox else self.PROD_BASE
        )
        self._tradier_headers = {
            "Authorization": f"Bearer {self._env.tradier_api_key}",
            "Accept": "application/json",
        }

        # Entry cache (keyed by symbol:date)
        self._cache: dict[str, list[FlowEntry]] = {}

    # --- public properties ------------------------------------------------

    @property
    def uw_client(self) -> UnusualWhalesClient | None:
        """Direct access to the Unusual Whales client for advanced queries."""
        return self._uw

    # ------------------------------------------------------------------
    # get_flow  — primary entry point
    # ------------------------------------------------------------------

    async def get_flow(
        self, symbol: str, target_date: date | None = None
    ) -> list[FlowEntry]:
        """Fetch options flow for a symbol.

        Tries Unusual Whales first; falls back to Tradier on failure.

        Args:
            symbol: Underlying symbol (e.g. "SPY", "SPX").
            target_date: Date to fetch flow for. Defaults to today.

        Returns:
            List of FlowEntry trades.
        """
        if target_date is None:
            target_date = date.today()

        cache_key = f"{symbol}:{target_date.isoformat()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        entries: list[FlowEntry] = []

        # --- Primary: Unusual Whales ---
        if self._uw is not None:
            try:
                entries = await self._fetch_from_unusual_whales(symbol)
                if entries:
                    logger.info(
                        "flow_fetched",
                        provider="unusual_whales",
                        symbol=symbol,
                        date=target_date.isoformat(),
                        trade_count=len(entries),
                    )
                    self._cache[cache_key] = entries
                    return entries
                # Empty result — fall through to Tradier
                logger.warning(
                    "uw_empty_response",
                    symbol=symbol,
                    msg="No flow alerts returned, trying Tradier",
                )
            except Exception as exc:
                logger.error(
                    "uw_fetch_failed",
                    symbol=symbol,
                    error=str(exc),
                    msg="Falling back to Tradier",
                )

        # --- Fallback: Tradier ---
        try:
            entries = await self._fetch_from_tradier(symbol, target_date)
            logger.info(
                "flow_fetched",
                provider="tradier",
                symbol=symbol,
                date=target_date.isoformat(),
                trade_count=len(entries),
            )
        except Exception as exc:
            logger.error("tradier_fetch_failed", symbol=symbol, error=str(exc))

        self._cache[cache_key] = entries
        return entries

    # ------------------------------------------------------------------
    # Unusual Whales fetch
    # ------------------------------------------------------------------

    async def _fetch_from_unusual_whales(
        self, symbol: str
    ) -> list[FlowEntry]:
        """Convert UW flow alerts into FlowEntry objects."""
        assert self._uw is not None
        raw_alerts = await self._uw.get_flow_alerts(symbol, limit=100)

        entries: list[FlowEntry] = []
        for alert in raw_alerts:
            try:
                opt_type_raw = str(alert.get("type", "")).lower()
                if opt_type_raw not in ("call", "put"):
                    # Some alerts may have "C"/"P" shorthand
                    opt_type_raw = "call" if opt_type_raw.startswith("c") else "put"

                total_premium = _safe_float(alert.get("total_premium", 0))
                total_size = _safe_int(alert.get("total_size", 0))
                volume = _safe_int(alert.get("volume", 0))
                ask_prem = _safe_float(alert.get("total_ask_side_prem", 0))
                bid_prem = _safe_float(alert.get("total_bid_side_prem", 0))

                # Determine side from ask_side vs bid_side premium
                if ask_prem > bid_prem * 1.5:
                    side = OptionSide.BUY
                elif bid_prem > ask_prem * 1.5:
                    side = OptionSide.SELL
                else:
                    side = OptionSide.UNKNOWN

                # Per-contract price from premium / (size * 100)
                effective_size = total_size if total_size > 0 else volume
                price = (
                    total_premium / (effective_size * 100)
                    if effective_size > 0
                    else _safe_float(alert.get("ask", 0))
                )

                # Parse timestamp
                created = alert.get("created_at")
                ts = _parse_timestamp(created) if created else datetime.now()

                entry = FlowEntry(
                    symbol=str(alert.get("ticker", symbol)),
                    strike=_safe_float(alert.get("strike", 0)),
                    expiry=str(alert.get("expiry", "")),
                    option_type=FlowOptionType(opt_type_raw),
                    premium=round(total_premium, 2),
                    volume=volume if volume > 0 else effective_size,
                    price=round(price, 4),
                    side=side,
                    timestamp=ts,
                    open_interest=_safe_int(alert.get("open_interest", 0)),
                    # UW enrichment
                    ask_side_premium=round(ask_prem, 2),
                    bid_side_premium=round(bid_prem, 2),
                    sweep_volume=_safe_int(alert.get("sweep_volume", 0)),
                    floor_volume=_safe_int(alert.get("floor_volume", 0)),
                    volume_oi_ratio=_safe_float(alert.get("volume_oi_ratio", 0)),
                    iv_start=_safe_float(alert.get("iv_start", 0)),
                    iv_end=_safe_float(alert.get("iv_end", 0)),
                    underlying_price=_safe_float(alert.get("underlying_price", 0)),
                    alert_rule=str(alert.get("alert_rule", "")),
                    has_sweep=bool(alert.get("has_sweep", False)),
                    has_floor=bool(alert.get("has_floor", False)),
                    has_multileg=bool(alert.get("has_multileg", False)),
                )
                entries.append(entry)
            except Exception as exc:
                logger.warning(
                    "uw_alert_parse_error",
                    alert=alert,
                    error=str(exc),
                )
                continue

        return entries

    # ------------------------------------------------------------------
    # Tradier fetch (fallback — original implementation)
    # ------------------------------------------------------------------

    async def _fetch_from_tradier(
        self, symbol: str, target_date: date
    ) -> list[FlowEntry]:
        """Fetch option chain data from Tradier as FlowEntry list."""
        entries: list[FlowEntry] = []

        async with httpx.AsyncClient(timeout=30) as client:
            # Get option expirations first
            exp_resp = await client.get(
                f"{self._base_url}/markets/options/expirations",
                headers=self._tradier_headers,
                params={"symbol": symbol, "includeAllRoots": "true"},
            )
            exp_resp.raise_for_status()
            exp_data = exp_resp.json()

            expirations = exp_data.get("expirations", {}).get("date", [])
            if isinstance(expirations, str):
                expirations = [expirations]

            # Get nearest expiration for 0DTE flow
            target_exp = None
            for exp in expirations:
                if exp >= target_date.isoformat():
                    target_exp = exp
                    break
            if target_exp is None and expirations:
                target_exp = expirations[0]
            if target_exp is None:
                logger.warning("no_expirations_found", symbol=symbol)
                return entries

            # Get option chain with greeks
            chain_resp = await client.get(
                f"{self._base_url}/markets/options/chains",
                headers=self._tradier_headers,
                params={
                    "symbol": symbol,
                    "expiration": target_exp,
                    "greeks": "true",
                },
            )
            chain_resp.raise_for_status()
            chain_data = chain_resp.json()

            options = chain_data.get("options", {}).get("option", [])
            if isinstance(options, dict):
                options = [options]

            for opt in options:
                vol = opt.get("volume", 0)
                last_price = opt.get("last", 0.0) or opt.get("bid", 0.0)
                if vol <= 0 or last_price <= 0:
                    continue

                premium = last_price * vol * 100

                ask = opt.get("ask", 0.0)
                bid = opt.get("bid", 0.0)
                if last_price >= ask and ask > 0:
                    side = OptionSide.BUY
                elif last_price <= bid and bid > 0:
                    side = OptionSide.SELL
                else:
                    side = OptionSide.UNKNOWN

                entry = FlowEntry(
                    symbol=symbol,
                    strike=opt.get("strike", 0.0),
                    expiry=target_exp,
                    option_type=(
                        FlowOptionType.CALL
                        if opt.get("option_type") == "call"
                        else FlowOptionType.PUT
                    ),
                    premium=round(premium, 2),
                    volume=vol,
                    price=last_price,
                    exchange=opt.get("exchange", ""),
                    side=side,
                    open_interest=opt.get("open_interest", 0),
                )
                entries.append(entry)

        return entries

    # ------------------------------------------------------------------
    # Unusual Whales enrichment methods
    # ------------------------------------------------------------------

    async def get_flow_alerts(
        self, symbol: str, limit: int = 50
    ) -> list[FlowEntry]:
        """Get flow alerts as FlowEntry objects.

        Convenience wrapper — same as get_flow() but forces UW provider.
        Falls back to get_flow() if UW is unavailable.
        """
        if self._uw is not None:
            try:
                return await self._fetch_from_unusual_whales(symbol)
            except Exception as exc:
                logger.error("get_flow_alerts_failed", error=str(exc))
        return await self.get_flow(symbol)

    async def get_options_volume(self, symbol: str) -> OptionsVolume | None:
        """Get daily options volume aggregates from Unusual Whales.

        Returns None if UW client is not configured or API fails.
        """
        if self._uw is None:
            logger.warning("uw_not_configured", method="get_options_volume")
            return None
        try:
            raw_list = await self._uw.get_options_volume(symbol)
            if not raw_list:
                return None
            # Take the most recent entry
            raw = raw_list[-1] if isinstance(raw_list, list) else raw_list
            return OptionsVolume(
                symbol=symbol,
                date=str(raw.get("date", "")),
                call_volume=_safe_int(raw.get("call_volume", 0)),
                put_volume=_safe_int(raw.get("put_volume", 0)),
                call_premium=_safe_float(raw.get("call_premium", 0)),
                put_premium=_safe_float(raw.get("put_premium", 0)),
                net_call_premium=_safe_float(raw.get("net_call_premium", 0)),
                net_put_premium=_safe_float(raw.get("net_put_premium", 0)),
                bearish_premium=_safe_float(raw.get("bearish_premium", 0)),
                bullish_premium=_safe_float(raw.get("bullish_premium", 0)),
                put_open_interest=_safe_int(raw.get("put_open_interest", 0)),
                call_open_interest=_safe_int(raw.get("call_open_interest", 0)),
                call_volume_ask_side=_safe_int(raw.get("call_volume_ask_side", 0)),
                call_volume_bid_side=_safe_int(raw.get("call_volume_bid_side", 0)),
                put_volume_ask_side=_safe_int(raw.get("put_volume_ask_side", 0)),
                put_volume_bid_side=_safe_int(raw.get("put_volume_bid_side", 0)),
            )
        except Exception as exc:
            logger.error("get_options_volume_failed", symbol=symbol, error=str(exc))
            return None

    async def get_net_premium_ticks(
        self, symbol: str, target_date: str | None = None
    ) -> list[NetPremiumTick]:
        """Get minute-by-minute net premium ticks from Unusual Whales.

        Args:
            symbol: Ticker symbol.
            target_date: ISO date string. Defaults to today.

        Returns:
            List of NetPremiumTick objects, or empty list on failure.
        """
        if self._uw is None:
            return []
        try:
            raw_list = await self._uw.get_net_premium_ticks(symbol, target_date)
            ticks: list[NetPremiumTick] = []
            for raw in raw_list:
                ticks.append(NetPremiumTick(
                    tape_time=str(raw.get("tape_time", "")),
                    call_volume=_safe_int(raw.get("call_volume", 0)),
                    put_volume=_safe_int(raw.get("put_volume", 0)),
                    net_call_premium=_safe_float(raw.get("net_call_premium", 0)),
                    net_put_premium=_safe_float(raw.get("net_put_premium", 0)),
                    net_call_volume=_safe_int(raw.get("net_call_volume", 0)),
                    net_put_volume=_safe_int(raw.get("net_put_volume", 0)),
                    net_delta=_safe_float(raw.get("net_delta", 0)),
                ))
            return ticks
        except Exception as exc:
            logger.error("get_net_premium_ticks_failed", symbol=symbol, error=str(exc))
            return []

    async def get_dark_pool(
        self, limit: int = 10, ticker: str | None = None
    ) -> list[DarkPoolEntry]:
        """Get recent dark pool prints from Unusual Whales.

        Args:
            limit: Max number of prints.
            ticker: Optional filter by ticker (client-side).

        Returns:
            List of DarkPoolEntry objects, or empty list on failure.
        """
        if self._uw is None:
            return []
        try:
            raw_list = await self._uw.get_dark_pool(limit)
            entries: list[DarkPoolEntry] = []
            for raw in raw_list:
                dp_ticker = str(raw.get("ticker", ""))
                if ticker and dp_ticker.upper() != ticker.upper():
                    continue
                executed = raw.get("executed_at")
                entries.append(DarkPoolEntry(
                    ticker=dp_ticker,
                    price=_safe_float(raw.get("price", 0)),
                    size=_safe_int(raw.get("size", 0)),
                    volume=_safe_int(raw.get("volume", 0)),
                    premium=_safe_float(raw.get("premium", 0)),
                    executed_at=_parse_timestamp(executed) if executed else None,
                    nbbo_ask=_safe_float(raw.get("nbbo_ask", 0)),
                    nbbo_bid=_safe_float(raw.get("nbbo_bid", 0)),
                    market_center=str(raw.get("market_center", "")),
                ))
            return entries
        except Exception as exc:
            logger.error("get_dark_pool_failed", error=str(exc))
            return []

    async def get_max_pain(self, symbol: str) -> MaxPainData | None:
        """Get max pain data for a symbol from Unusual Whales.

        Returns the nearest expiry's max pain data, or None on failure.
        """
        if self._uw is None:
            return None
        try:
            raw_list = await self._uw.get_max_pain(symbol)
            if not raw_list:
                return None
            # Use the nearest expiry (first entry)
            raw = raw_list[0] if isinstance(raw_list, list) else raw_list
            return MaxPainData(
                symbol=symbol,
                expiry=str(raw.get("expiry", "")),
                max_pain=_safe_float(raw.get("max_pain", 0)),
                close=_safe_float(raw.get("close", 0)),
                open=_safe_float(raw.get("open", 0)),
                next_upper_strike=_safe_float(raw.get("next_upper_strike", 0)),
                next_lower_strike=_safe_float(raw.get("next_lower_strike", 0)),
            )
        except Exception as exc:
            logger.error("get_max_pain_failed", symbol=symbol, error=str(exc))
            return None

    async def get_greek_exposure(
        self, symbol: str, target_date: str | None = None
    ) -> GreekExposure | None:
        """Get aggregate greek exposure for a symbol from Unusual Whales.

        Args:
            symbol: Ticker symbol.
            target_date: ISO date string. Defaults to today.

        Returns:
            GreekExposure object, or None on failure.
        """
        if self._uw is None:
            return None
        try:
            raw_list = await self._uw.get_greek_exposure(symbol, target_date)
            if not raw_list:
                return None
            # Use the most recent entry
            raw = raw_list[-1] if isinstance(raw_list, list) else raw_list
            call_delta = _safe_float(raw.get("call_delta", 0))
            put_delta = _safe_float(raw.get("put_delta", 0))
            call_gamma = _safe_float(raw.get("call_gamma", 0))
            put_gamma = _safe_float(raw.get("put_gamma", 0))
            return GreekExposure(
                symbol=symbol,
                date=str(raw.get("date", target_date or "")),
                call_delta=call_delta,
                put_delta=put_delta,
                net_delta=call_delta + put_delta,
                call_gamma=call_gamma,
                put_gamma=put_gamma,
                net_gamma=call_gamma + put_gamma,
                call_charm=_safe_float(raw.get("call_charm", 0)),
                put_charm=_safe_float(raw.get("put_charm", 0)),
                call_vanna=_safe_float(raw.get("call_vanna", 0)),
                put_vanna=_safe_float(raw.get("put_vanna", 0)),
            )
        except Exception as exc:
            logger.error("get_greek_exposure_failed", symbol=symbol, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # analyze_flow  (preserved interface)
    # ------------------------------------------------------------------

    def analyze_flow(self, entries: list[FlowEntry]) -> FlowSummary:
        """Analyze a list of flow entries into a summary.

        Calculates net call/put premium, identifies the biggest trades,
        detects unusual activity, clusters into flow bubbles, and
        produces a directional bias score.

        Args:
            entries: List of FlowEntry trades for a symbol.

        Returns:
            FlowSummary with all analysis results.
        """
        if not entries:
            return FlowSummary(
                symbol="UNKNOWN",
                date=date.today().isoformat(),
            )

        symbol = entries[0].symbol
        today_str = date.today().isoformat()

        total_call_premium = sum(
            e.premium for e in entries if e.option_type == FlowOptionType.CALL
        )
        total_put_premium = sum(
            e.premium for e in entries if e.option_type == FlowOptionType.PUT
        )
        call_volume = sum(
            e.volume for e in entries if e.option_type == FlowOptionType.CALL
        )
        put_volume = sum(
            e.volume for e in entries if e.option_type == FlowOptionType.PUT
        )

        net_premium = total_call_premium - total_put_premium
        pcr = put_volume / call_volume if call_volume > 0 else 0.0

        # Top 5 biggest trades by premium
        sorted_by_premium = sorted(entries, key=lambda e: e.premium, reverse=True)
        biggest = sorted_by_premium[:5]

        # Unusual activity
        unusual = self.detect_unusual_activity(entries)

        # Flow bubbles
        bubbles = self._cluster_flow_bubbles(entries)

        # Bias score — uses UW-enriched data when available
        bias = self._calculate_flow_bias(
            total_call_premium, total_put_premium, entries
        )

        summary = FlowSummary(
            symbol=symbol,
            date=today_str,
            total_call_premium=round(total_call_premium, 2),
            total_put_premium=round(total_put_premium, 2),
            net_premium=round(net_premium, 2),
            call_volume=call_volume,
            put_volume=put_volume,
            put_call_ratio=round(pcr, 3),
            biggest_trades=biggest,
            unusual_trades=unusual,
            flow_bubbles=bubbles,
            flow_bias_score=round(bias, 2),
        )

        logger.info(
            "flow_analyzed",
            symbol=symbol,
            call_premium=summary.total_call_premium,
            put_premium=summary.total_put_premium,
            pcr=summary.put_call_ratio,
            bias=summary.flow_bias_score,
            unusual_count=len(unusual),
        )
        return summary

    def detect_unusual_activity(
        self, entries: list[FlowEntry]
    ) -> list[FlowEntry]:
        """Filter for trades with premium exceeding the configured minimum.

        Default minimum is $100K. These are the trades that move markets —
        institutional block orders, hedge fund positioning, etc.

        Also flags entries with sweeps, floor trades, and high vol/OI ratio
        as unusual even if below the premium threshold.

        Args:
            entries: All flow entries for the day.

        Returns:
            List of FlowEntry trades exceeding min_premium threshold.
        """
        min_premium = self._cfg.min_premium
        unusual: list[FlowEntry] = []

        for e in entries:
            is_big = e.premium >= min_premium
            is_sweep = e.has_sweep and e.premium >= min_premium * 0.5
            is_floor = e.has_floor and e.premium >= min_premium * 0.5
            high_vol_oi = e.volume_oi_ratio > 3.0 and e.premium >= min_premium * 0.3

            if is_big or is_sweep or is_floor or high_vol_oi:
                unusual.append(e)

        if unusual:
            logger.info(
                "unusual_flow_detected",
                count=len(unusual),
                total_premium=sum(e.premium for e in unusual),
                sweeps=sum(1 for e in unusual if e.has_sweep),
                floor_trades=sum(1 for e in unusual if e.has_floor),
                min_threshold=min_premium,
            )

        return sorted(unusual, key=lambda e: e.premium, reverse=True)

    # ------------------------------------------------------------------
    # Bias methods (preserved interface)
    # ------------------------------------------------------------------

    async def get_flow_bias(self, symbol: str) -> float:
        """Get the flow bias score for a symbol.

        Convenience method that fetches flow data and returns just the
        bias score (-100 to +100).

        Args:
            symbol: Ticker symbol.

        Returns:
            Flow bias score. Positive = bullish flow, negative = bearish.
        """
        entries = await self.get_flow(symbol)
        if not entries:
            return 0.0
        summary = self.analyze_flow(entries)
        return summary.flow_bias_score

    def get_flow_bias_sync(self, entries: list[FlowEntry]) -> float:
        """Synchronous version — get flow bias from pre-fetched entries.

        Args:
            entries: Pre-fetched flow entries.

        Returns:
            Flow bias score -100 to +100.
        """
        if not entries:
            return 0.0
        summary = self.analyze_flow(entries)
        return summary.flow_bias_score

    # ------------------------------------------------------------------
    # Bias calculation (enhanced with UW data)
    # ------------------------------------------------------------------

    def _calculate_flow_bias(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
    ) -> float:
        """Calculate the directional bias from flow data.

        Enhanced scoring using Unusual Whales data when available:

        1. **Premium ratio** (20%) — net call vs put premium
        2. **Ask/Bid side premium** (25%) — aggressive buying at ask = bullish,
           aggressive selling at bid = bearish. This is THE most reliable
           signal from UW data.
        3. **Sweep/floor activity** (20%) — sweeps signal urgency,
           floor trades signal institutional size
        4. **Volume/OI ratio** (15%) — high ratio = new positioning,
           weighted by direction
        5. **Unusual activity direction** (20%) — big trades set the tone

        Falls back to the simpler 3-component model when UW enrichment
        fields are all zero (i.e. Tradier data).

        Args:
            call_premium: Total call premium.
            put_premium: Total put premium.
            entries: All flow entries.

        Returns:
            Score from -100 (extreme bearish) to +100 (extreme bullish).
        """
        total = call_premium + put_premium
        if total == 0:
            return 0.0

        # Check if we have UW-enriched data
        has_uw_data = any(
            e.ask_side_premium > 0 or e.bid_side_premium > 0
            for e in entries
        )

        if has_uw_data:
            return self._calculate_flow_bias_uw(
                call_premium, put_premium, entries, total
            )
        else:
            return self._calculate_flow_bias_tradier(
                call_premium, put_premium, entries, total
            )

    def _calculate_flow_bias_uw(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
        total: float,
    ) -> float:
        """UW-enriched bias calculation — uses ask/bid side premium,
        sweep activity, floor trades, and vol/OI ratio."""

        # Component 1: Premium ratio (20%)
        call_pct = call_premium / total
        premium_score = (call_pct - 0.5) * 200

        # Component 2: Ask/Bid side premium (25%)
        # Ask-side premium on calls = aggressive call buying = bullish
        # Ask-side premium on puts = aggressive put buying = bearish
        # Bid-side premium on calls = call selling = bearish
        # Bid-side premium on puts = put selling = bullish
        bull_side_prem = sum(
            e.ask_side_premium for e in entries
            if e.option_type == FlowOptionType.CALL
        ) + sum(
            e.bid_side_premium for e in entries
            if e.option_type == FlowOptionType.PUT
        )
        bear_side_prem = sum(
            e.ask_side_premium for e in entries
            if e.option_type == FlowOptionType.PUT
        ) + sum(
            e.bid_side_premium for e in entries
            if e.option_type == FlowOptionType.CALL
        )
        side_total = bull_side_prem + bear_side_prem
        if side_total > 0:
            side_score = ((bull_side_prem / side_total) - 0.5) * 200
        else:
            side_score = 0.0

        # Component 3: Sweep/Floor activity direction (20%)
        # Sweeps signal urgency — direction matters
        sweep_entries = [e for e in entries if e.has_sweep]
        floor_entries = [e for e in entries if e.has_floor]
        urgent_entries = sweep_entries + floor_entries

        if urgent_entries:
            urgent_call_prem = sum(
                e.premium for e in urgent_entries
                if e.option_type == FlowOptionType.CALL
            )
            urgent_put_prem = sum(
                e.premium for e in urgent_entries
                if e.option_type == FlowOptionType.PUT
            )
            urgent_total = urgent_call_prem + urgent_put_prem
            if urgent_total > 0:
                sweep_score = ((urgent_call_prem / urgent_total) - 0.5) * 200
            else:
                sweep_score = 0.0
        else:
            sweep_score = 0.0

        # Component 4: Volume/OI ratio weighted direction (15%)
        # High vol/OI = new positioning. Direction tells the story.
        high_voi_entries = [e for e in entries if e.volume_oi_ratio > 1.5]
        if high_voi_entries:
            voi_call_prem = sum(
                e.premium * min(e.volume_oi_ratio, 10)
                for e in high_voi_entries
                if e.option_type == FlowOptionType.CALL
            )
            voi_put_prem = sum(
                e.premium * min(e.volume_oi_ratio, 10)
                for e in high_voi_entries
                if e.option_type == FlowOptionType.PUT
            )
            voi_total = voi_call_prem + voi_put_prem
            if voi_total > 0:
                voi_score = ((voi_call_prem / voi_total) - 0.5) * 200
            else:
                voi_score = 0.0
        else:
            voi_score = 0.0

        # Component 5: Unusual activity direction (20%)
        unusual = [e for e in entries if e.premium >= self._cfg.min_premium]
        if unusual:
            unusual_call = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.CALL
            )
            unusual_put = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.PUT
            )
            unusual_total = unusual_call + unusual_put
            if unusual_total > 0:
                unusual_score = ((unusual_call / unusual_total) - 0.5) * 200
            else:
                unusual_score = 0.0
        else:
            unusual_score = 0.0

        # Weighted combination
        final = (
            premium_score * 0.20
            + side_score * 0.25
            + sweep_score * 0.20
            + voi_score * 0.15
            + unusual_score * 0.20
        )
        return float(max(-100, min(100, final)))

    def _calculate_flow_bias_tradier(
        self,
        call_premium: float,
        put_premium: float,
        entries: list[FlowEntry],
        total: float,
    ) -> float:
        """Legacy Tradier-based bias — 3-component model."""

        # Component 1: Premium ratio
        call_pct = call_premium / total
        premium_score = (call_pct - 0.5) * 200

        # Component 2: Aggressive flow
        aggressive_bull = sum(
            e.premium for e in entries
            if e.side == OptionSide.BUY and e.option_type == FlowOptionType.CALL
        )
        aggressive_bear = sum(
            e.premium for e in entries
            if e.side == OptionSide.BUY and e.option_type == FlowOptionType.PUT
        )
        agg_total = aggressive_bull + aggressive_bear
        agg_score = (
            ((aggressive_bull / agg_total) - 0.5) * 200 if agg_total > 0 else 0.0
        )

        # Component 3: Unusual activity
        unusual = [e for e in entries if e.premium >= self._cfg.min_premium]
        if unusual:
            unusual_call = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.CALL
            )
            unusual_put = sum(
                e.premium for e in unusual if e.option_type == FlowOptionType.PUT
            )
            unusual_total = unusual_call + unusual_put
            unusual_score = (
                ((unusual_call / unusual_total) - 0.5) * 200
                if unusual_total > 0
                else 0.0
            )
        else:
            unusual_score = 0.0

        final = premium_score * 0.30 + agg_score * 0.30 + unusual_score * 0.40
        return float(max(-100, min(100, final)))

    # ------------------------------------------------------------------
    # Flow bubble clustering (preserved)
    # ------------------------------------------------------------------

    def _cluster_flow_bubbles(
        self, entries: list[FlowEntry]
    ) -> list[FlowBubble]:
        """Cluster flow entries into bubbles at specific strikes.

        A bubble forms when multiple large trades land at the same
        strike/expiry combination. These are key levels where
        institutions are building positions.

        Args:
            entries: All flow entries.

        Returns:
            List of FlowBubble clusters, sorted by total premium.
        """
        clusters: dict[tuple[float, str, str], list[FlowEntry]] = {}
        for entry in entries:
            key = (entry.strike, entry.expiry, entry.option_type.value)
            clusters.setdefault(key, []).append(entry)

        bubbles: list[FlowBubble] = []
        for (strike, expiry, opt_type), cluster_entries in clusters.items():
            total_prem = sum(e.premium for e in cluster_entries)
            if total_prem < self._cfg.min_premium / 2:
                continue

            buy_premium = sum(
                e.premium for e in cluster_entries if e.side == OptionSide.BUY
            )
            sell_premium = sum(
                e.premium for e in cluster_entries if e.side == OptionSide.SELL
            )
            net_side = (
                OptionSide.BUY if buy_premium >= sell_premium else OptionSide.SELL
            )

            total_vol = sum(e.volume for e in cluster_entries)
            avg_price = (
                sum(e.price * e.volume for e in cluster_entries) / total_vol
                if total_vol > 0
                else 0.0
            )

            bubble = FlowBubble(
                symbol=cluster_entries[0].symbol,
                strike=strike,
                expiry=expiry,
                total_premium=round(total_prem, 2),
                trade_count=len(cluster_entries),
                net_side=net_side,
                dominant_type=FlowOptionType(opt_type),
                avg_price=round(avg_price, 4),
            )
            bubbles.append(bubble)

        bubbles.sort(key=lambda b: b.total_premium, reverse=True)
        return bubbles[:10]

    # ------------------------------------------------------------------
    # CSV import (preserved)
    # ------------------------------------------------------------------

    @classmethod
    def load_from_csv(cls, filepath: str | Path) -> list[FlowEntry]:
        """Load flow data from a CSV file for manual import.

        Expected columns: symbol, strike, expiry, option_type, premium,
        volume, price, side, timestamp

        Args:
            filepath: Path to the CSV file.

        Returns:
            List of FlowEntry parsed from the CSV.
        """
        entries: list[FlowEntry] = []
        path = Path(filepath)

        if not path.exists():
            logger.warning("csv_not_found", path=str(path))
            return entries

        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry = FlowEntry(
                        symbol=row["symbol"],
                        strike=float(row["strike"]),
                        expiry=row["expiry"],
                        option_type=FlowOptionType(row["option_type"]),
                        premium=float(row["premium"]),
                        volume=int(row["volume"]),
                        price=float(row["price"]),
                        side=OptionSide(row.get("side", "unknown")),
                        timestamp=datetime.fromisoformat(
                            row.get("timestamp", datetime.now().isoformat())
                        ),
                    )
                    entries.append(entry)
                except (KeyError, ValueError) as e:
                    logger.warning("csv_parse_error", row=row, error=str(e))

        logger.info("csv_loaded", path=str(path), count=len(entries))
        return entries


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: Any) -> int:
    """Safely convert a value to int, returning 0 on failure."""
    if val is None:
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def _parse_timestamp(val: Any) -> datetime:
    """Parse various timestamp formats into datetime."""
    if isinstance(val, datetime):
        return val
    if not val:
        return datetime.now()
    s = str(val)
    # Try common formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("timestamp_parse_failed", raw=s)
        return datetime.now()


#===============================================================================
# SECTION: SIGNALS - Market Regime
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/regime.py (256 lines)
#-------------------------------------------------------------------------------

"""Market Regime Detection — 20/50 SMA Cross System.

Detects macro regime changes using Simple Moving Average crossovers:
    - Golden Cross (20SMA > 50SMA) = BULLISH regime → +20 bias bonus
    - Death Cross (20SMA < 50SMA) = BEARISH regime → -30 bias penalty
    - Cross detected today = TRANSITIONING with extra urgency

The regime bias adjustment is applied across ALL tickers, not per-symbol.
This is a market-wide signal that shifts the entire trading posture.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import Bar

logger = structlog.get_logger(__name__)


class RegimeState(str, Enum):
    """Market regime states based on SMA crossover."""

    BULLISH = "BULLISH"           # Golden cross — 20SMA > 50SMA
    BEARISH = "BEARISH"           # Death cross — 20SMA < 50SMA
    TRANSITIONING = "TRANSITIONING"  # Cross detected today — extra urgency


class RegimeResult(BaseModel):
    """Result of regime detection analysis."""

    state: RegimeState
    sma_fast: float          # Current fast SMA value
    sma_slow: float          # Current slow SMA value
    spread_pct: float        # Spread between SMAs as percentage
    cross_today: bool        # Whether a cross happened on the last bar
    bias_adjustment: float   # The penalty/bonus to apply to bias engine
    bars_since_cross: int    # How many bars since the last cross


class RegimeDetector:
    """Detects market regime using SMA crossovers on SPX/SPY daily bars.

    The regime signal is the slowest-moving component of the bias engine.
    It takes many days for SMAs to cross, so regime changes are rare but
    significant. A death cross is a strong bearish signal that should
    reduce bullish exposure across all tickers.

    Typical usage:
        detector = RegimeDetector()
        result = detector.detect_regime(daily_bars)
        adjustment = result.bias_adjustment  # apply to all symbols
    """

    # Bias adjustments for each regime
    BULLISH_BONUS = 20.0
    BEARISH_PENALTY = -30.0
    TRANSITION_MULTIPLIER = 1.5  # Extra urgency when cross is fresh

    def __init__(self):
        self._cfg = config().regime
        self._last_result: RegimeResult | None = None

    def detect_regime(self, bars: list[Bar]) -> RegimeResult:
        """Detect the current market regime from daily bars.

        Requires at least sma_slow bars of data. Calculates 20-day and
        50-day SMAs, determines if we're in a golden or death cross,
        and checks if the cross happened on the most recent bar.

        Args:
            bars: Daily OHLCV bars for SPX or SPY. Must have at least
                  sma_slow (default 50) bars.

        Returns:
            RegimeResult with state, SMA values, and bias adjustment.
        """
        fast_period = self._cfg.sma_fast
        slow_period = self._cfg.sma_slow

        if len(bars) < slow_period:
            logger.warning(
                "insufficient_bars_for_regime",
                needed=slow_period,
                got=len(bars),
            )
            return RegimeResult(
                state=RegimeState.BULLISH,  # default to bullish (no data)
                sma_fast=0.0,
                sma_slow=0.0,
                spread_pct=0.0,
                cross_today=False,
                bias_adjustment=0.0,
                bars_since_cross=0,
            )

        closes = np.array([b.close for b in bars])

        # Calculate SMAs
        sma_fast_arr = self._compute_sma(closes, fast_period)
        sma_slow_arr = self._compute_sma(closes, slow_period)

        # Current values (last element)
        current_fast = float(sma_fast_arr[-1])
        current_slow = float(sma_slow_arr[-1])

        # Spread as percentage
        spread_pct = ((current_fast - current_slow) / current_slow) * 100 if current_slow > 0 else 0.0

        # Determine state
        if current_fast > current_slow:
            base_state = RegimeState.BULLISH
        else:
            base_state = RegimeState.BEARISH

        # Check for cross on the most recent bar
        cross_today = False
        if len(sma_fast_arr) >= 2 and len(sma_slow_arr) >= 2:
            prev_fast = sma_fast_arr[-2]
            prev_slow = sma_slow_arr[-2]
            prev_above = prev_fast > prev_slow
            curr_above = current_fast > current_slow

            if prev_above != curr_above:
                cross_today = True
                logger.warning(
                    "regime_cross_detected",
                    cross_type="golden" if curr_above else "death",
                    sma_fast=round(current_fast, 2),
                    sma_slow=round(current_slow, 2),
                )

        # Count bars since last cross
        bars_since_cross = self._count_bars_since_cross(sma_fast_arr, sma_slow_arr)

        # Final state — if cross happened today, it's transitioning
        state = RegimeState.TRANSITIONING if cross_today else base_state

        # Calculate bias adjustment
        if base_state == RegimeState.BULLISH:
            adjustment = self.BULLISH_BONUS
        else:
            adjustment = self.BEARISH_PENALTY

        # Amplify if cross is fresh (within last 3 bars)
        if cross_today or bars_since_cross <= 3:
            adjustment *= self.TRANSITION_MULTIPLIER

        result = RegimeResult(
            state=state,
            sma_fast=round(current_fast, 2),
            sma_slow=round(current_slow, 2),
            spread_pct=round(spread_pct, 4),
            cross_today=cross_today,
            bias_adjustment=round(adjustment, 2),
            bars_since_cross=bars_since_cross,
        )

        self._last_result = result

        logger.info(
            "regime_detected",
            state=state.value,
            sma_fast=result.sma_fast,
            sma_slow=result.sma_slow,
            spread_pct=result.spread_pct,
            adjustment=result.bias_adjustment,
        )
        return result

    def get_regime_bias_adjustment(self) -> float:
        """Get the current regime bias adjustment.

        Returns the penalty/bonus to apply to the bias engine.
        If no regime has been detected yet, returns 0.

        Returns:
            Float adjustment: +20 for bullish, -30 for bearish,
            amplified by 1.5x if cross is fresh.
        """
        if self._last_result is None:
            return 0.0
        return self._last_result.bias_adjustment

    def get_last_result(self) -> RegimeResult | None:
        """Get the most recent regime detection result."""
        return self._last_result

    @staticmethod
    def _compute_sma(data: np.ndarray, period: int) -> np.ndarray:
        """Compute Simple Moving Average.

        Uses a cumulative sum approach for efficiency.

        Args:
            data: Array of prices.
            period: SMA lookback period.

        Returns:
            Array of SMA values (same length as input, NaN-padded at start).
        """
        if len(data) < period:
            return np.full_like(data, np.nan, dtype=float)

        cumsum = np.cumsum(data, dtype=float)
        sma = np.full_like(data, np.nan, dtype=float)
        sma[period - 1] = cumsum[period - 1] / period
        for i in range(period, len(data)):
            sma[i] = (cumsum[i] - cumsum[i - period]) / period
        return sma

    @staticmethod
    def _count_bars_since_cross(
        sma_fast: np.ndarray, sma_slow: np.ndarray
    ) -> int:
        """Count how many bars since the last SMA cross.

        Walks backward from the most recent bar until we find a sign change.

        Args:
            sma_fast: Fast SMA array.
            sma_slow: Slow SMA array.

        Returns:
            Number of bars since the last crossover.
        """
        # Find valid range (both SMAs have values)
        valid_start = 0
        for i in range(len(sma_fast)):
            if not (np.isnan(sma_fast[i]) or np.isnan(sma_slow[i])):
                valid_start = i
                break

        if valid_start >= len(sma_fast) - 1:
            return len(sma_fast)  # No valid cross data

        # Current direction
        current_above = sma_fast[-1] > sma_slow[-1]

        # Walk backward
        count = 0
        for i in range(len(sma_fast) - 2, valid_start - 1, -1):
            if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
                break
            was_above = sma_fast[i] > sma_slow[i]
            if was_above != current_above:
                return count
            count += 1

        return count


#===============================================================================
# SECTION: SIGNALS - Key Levels
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/levels.py (562 lines)
#-------------------------------------------------------------------------------

"""Key Level Tracker — Track critical support/resistance levels per ticker.

Tracks:
    - Premarket Low (4:00 AM - 9:30 AM ET) — #1 entry support
    - Previous Day Close — reversal trigger
    - Previous Friday Close — weekly S/R
    - NWOG (New Week Opening Gap) — Friday close to Monday open gap
    - Fibonacci Retracements — 38.2%, 50%, 61.8%
    - Session High/Low — intraday tracking

Levels are stored per-symbol and persisted to JSON for cross-session reuse.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import Bar

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")
PREMARKET_START = time(4, 0)
PREMARKET_END = time(9, 30)

# Persistence path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEVELS_FILE = _PROJECT_ROOT / "data" / "key_levels.json"


class FibonacciLevels(BaseModel):
    """Fibonacci retracement levels calculated from a high/low range."""

    high: float
    low: float
    fib_382: float = 0.0
    fib_500: float = 0.0
    fib_618: float = 0.0

    def __init__(self, **data: Any):
        super().__init__(**data)
        range_size = self.high - self.low
        self.fib_382 = self.high - range_size * 0.382
        self.fib_500 = self.high - range_size * 0.500
        self.fib_618 = self.high - range_size * 0.618


class NWOGLevels(BaseModel):
    """New Week Opening Gap — gap between Friday close and Monday open."""

    friday_close: float
    monday_open: float
    gap_high: float = 0.0
    gap_low: float = 0.0
    gap_mid: float = 0.0

    def __init__(self, **data: Any):
        super().__init__(**data)
        self.gap_high = max(self.friday_close, self.monday_open)
        self.gap_low = min(self.friday_close, self.monday_open)
        self.gap_mid = (self.gap_high + self.gap_low) / 2.0


class DemandZone(BaseModel):
    """A demand/supply zone identified from chart structure."""

    zone_high: float
    zone_low: float
    zone_type: str = "demand"  # "demand" or "supply"
    strength: str = "moderate"  # "weak", "moderate", "strong"
    notes: str = ""


class KeyLevels(BaseModel):
    """Aggregated key levels for a single symbol on a given day."""

    symbol: str
    date: str  # ISO date string
    premarket_low: float | None = None
    premarket_high: float | None = None
    prev_day_close: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    prev_friday_close: float | None = None
    nwog: NWOGLevels | None = None
    fibonacci: FibonacciLevels | None = None
    session_high: float | None = None
    session_low: float | None = None
    sma_200: float | None = None  # 200-day SMA — major resistance/support
    sma_50: float | None = None   # 50-day SMA — floor level
    market_open: float | None = None  # Today's opening price
    demand_zones: list[DemandZone] = Field(default_factory=list)


class LevelTracker:
    """Tracks and manages key price levels for all symbols.

    Calculates premarket low, prev close, NWOG, fibs, and session
    extremes. Persists to JSON so levels survive restarts.
    """

    def __init__(self):
        self._cfg = config().levels
        self._levels: dict[str, KeyLevels] = {}
        self._load_persisted()

    def _load_persisted(self) -> None:
        """Load previously persisted levels from JSON file."""
        if LEVELS_FILE.exists():
            try:
                with open(LEVELS_FILE) as f:
                    raw = json.load(f)
                for symbol, data in raw.items():
                    self._levels[symbol] = KeyLevels.model_validate(data)
                logger.info("levels_loaded", count=len(self._levels))
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("levels_load_failed", error=str(e))

    def _persist(self) -> None:
        """Persist current levels to JSON file."""
        LEVELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {sym: lvl.model_dump() for sym, lvl in self._levels.items()}
        with open(LEVELS_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.debug("levels_persisted", count=len(self._levels))

    def calculate_premarket_levels(self, bars: list[Bar]) -> tuple[float | None, float | None]:
        """Calculate premarket low and high between 4:00 AM and 9:30 AM ET.

        Premarket low = #1 entry support. PM high = resistance for puts.
        Price holding PM low = bullish. Price breaking PM low = bearish.

        From SuperLuckeee's 6 key levels system:
        - PM Low: Support for calls entry
        - PM High: Resistance for puts entry
        - Works on individual stocks AND indices

        Args:
            bars: Intraday bars (1m or 5m) that include premarket hours.

        Returns:
            Tuple of (premarket_low, premarket_high), either may be None.
        """
        pm_bars = []
        for bar in bars:
            bar_time_et = bar.timestamp.astimezone(ET).time()
            if PREMARKET_START <= bar_time_et < PREMARKET_END:
                pm_bars.append(bar)

        if not pm_bars:
            logger.debug("no_premarket_bars", count=len(bars))
            return None, None

        pm_low = min(b.low for b in pm_bars)
        pm_high = max(b.high for b in pm_bars)
        logger.info("premarket_levels_calculated", pm_low=pm_low, pm_high=pm_high, bar_count=len(pm_bars))
        return pm_low, pm_high

    def calculate_premarket_low(self, bars: list[Bar]) -> float | None:
        """Calculate the lowest price between 4:00 AM and 9:30 AM ET.

        Legacy wrapper — prefer calculate_premarket_levels() for both values.
        """
        pm_low, _ = self.calculate_premarket_levels(bars)
        return pm_low

    def calculate_sma(self, daily_bars: list[Bar], period: int) -> float | None:
        """Calculate Simple Moving Average from daily bars.

        Used for 200SMA (major resistance/support) and 50SMA (floor level).
        SuperLuckeee: "SPY at 200SMA $661 = serious resistance."
        SuperLuckeee: "50SMA at $650-651, SPY doesn't stay below this for long."

        Args:
            daily_bars: Historical daily bars. Need at least `period` bars.
            period: SMA period (e.g., 200 or 50).

        Returns:
            The SMA value, or None if insufficient data.
        """
        if len(daily_bars) < period:
            return None
        closes = [b.close for b in daily_bars[-period:]]
        sma = sum(closes) / len(closes)
        return round(sma, 2)

    def calculate_nwog(
        self, friday_close: float, monday_open: float
    ) -> dict[str, float]:
        """Calculate the New Week Opening Gap.

        The NWOG is the gap between Friday's close and Monday's open.
        This gap zone acts as a magnet — price tends to fill the gap
        and the midpoint is a key reversal level.

        Args:
            friday_close: Friday's closing price.
            monday_open: Monday's opening price.

        Returns:
            Dict with gap_high, gap_low, gap_mid values.
        """
        nwog = NWOGLevels(friday_close=friday_close, monday_open=monday_open)
        logger.info(
            "nwog_calculated",
            friday_close=friday_close,
            monday_open=monday_open,
            gap_high=nwog.gap_high,
            gap_low=nwog.gap_low,
            gap_mid=nwog.gap_mid,
        )
        return {"gap_high": nwog.gap_high, "gap_low": nwog.gap_low, "gap_mid": nwog.gap_mid}

    def calculate_fibonacci(
        self, high: float, low: float
    ) -> dict[str, float]:
        """Calculate Fibonacci retracement levels for a given range.

        Standard retracement levels at 38.2%, 50%, and 61.8%.
        These are measured from the high — so fib_382 is closer to the high
        (shallower pullback) and fib_618 is closer to the low (deeper pullback).

        Args:
            high: The swing high price.
            low: The swing low price.

        Returns:
            Dict with fib_382, fib_500, fib_618 levels.
        """
        if high <= low:
            logger.warning("fibonacci_invalid_range", high=high, low=low)
            return {"fib_382": 0.0, "fib_500": 0.0, "fib_618": 0.0}

        fib = FibonacciLevels(high=high, low=low)
        result = {
            "fib_382": round(fib.fib_382, 2),
            "fib_500": round(fib.fib_500, 2),
            "fib_618": round(fib.fib_618, 2),
        }
        logger.info("fibonacci_calculated", high=high, low=low, **result)
        return result

    def update_session_extremes(self, symbol: str, bar: Bar) -> None:
        """Update the session high/low as intraday bars come in.

        Call this on every new bar to keep session extremes current.

        Args:
            symbol: Ticker symbol.
            bar: Latest OHLCV bar.
        """
        levels = self._levels.get(symbol)
        if levels is None:
            return

        updated = False
        if levels.session_high is None or bar.high > levels.session_high:
            levels.session_high = bar.high
            updated = True
        if levels.session_low is None or bar.low < levels.session_low:
            levels.session_low = bar.low
            updated = True

        if updated:
            logger.debug(
                "session_extremes_updated",
                symbol=symbol,
                high=levels.session_high,
                low=levels.session_low,
            )

    def build_levels(
        self,
        symbol: str,
        intraday_bars: list[Bar],
        daily_bars: list[Bar],
        friday_close: float | None = None,
        monday_open: float | None = None,
    ) -> KeyLevels:
        """Build all key levels for a symbol from available data.

        This is the main entry point — call once at start of day with
        premarket + daily bars, then use update_session_extremes() for
        intraday tracking.

        Args:
            symbol: Ticker symbol.
            intraday_bars: Today's intraday bars (including premarket if available).
            daily_bars: Historical daily bars (at least 5 for weekly levels).
            friday_close: Previous Friday's close (for NWOG calc on Mondays).
            monday_open: Monday's opening price (for NWOG calc).

        Returns:
            KeyLevels with all available level data populated.
        """
        today_str = datetime.now(ET).strftime("%Y-%m-%d")

        levels = KeyLevels(symbol=symbol, date=today_str)

        # Premarket low + high (The 6 Key Levels: #1 PM High, #2 PM Low)
        if self._cfg.track_pm_low and intraday_bars:
            pm_low, pm_high = self.calculate_premarket_levels(intraday_bars)
            levels.premarket_low = pm_low
            levels.premarket_high = pm_high

        # Previous day close (The 6 Key Levels: #5 Market Close)
        if self._cfg.track_prev_close and len(daily_bars) >= 2:
            levels.prev_day_close = daily_bars[-2].close
            levels.prev_day_high = daily_bars[-2].high
            levels.prev_day_low = daily_bars[-2].low
            logger.info(
                "prev_day_levels_set", symbol=symbol,
                close=levels.prev_day_close,
                high=levels.prev_day_high,
                low=levels.prev_day_low,
            )

        # 200SMA and 50SMA — major resistance/support
        if len(daily_bars) >= 200:
            levels.sma_200 = self.calculate_sma(daily_bars, 200)
            logger.info("sma_200_set", symbol=symbol, sma_200=levels.sma_200)
        if len(daily_bars) >= 50:
            levels.sma_50 = self.calculate_sma(daily_bars, 50)
            logger.info("sma_50_set", symbol=symbol, sma_50=levels.sma_50)

        # Previous Friday close (look back through daily bars to find last Friday)
        if daily_bars:
            for bar in reversed(daily_bars[:-1]):  # skip today
                bar_dt = bar.timestamp.astimezone(ET) if bar.timestamp.tzinfo else bar.timestamp
                if bar_dt.weekday() == 4:  # Friday
                    levels.prev_friday_close = bar.close
                    logger.info("prev_friday_close_set", symbol=symbol, close=bar.close)
                    break

        # NWOG
        if self._cfg.track_nwog and friday_close is not None and monday_open is not None:
            nwog_data = self.calculate_nwog(friday_close, monday_open)
            levels.nwog = NWOGLevels(
                friday_close=friday_close, monday_open=monday_open
            )

        # Fibonacci from previous day's high/low
        if len(daily_bars) >= 2:
            prev_day = daily_bars[-2]
            fib_data = self.calculate_fibonacci(prev_day.high, prev_day.low)
            levels.fibonacci = FibonacciLevels(high=prev_day.high, low=prev_day.low)

        # Session high/low from intraday bars
        if intraday_bars:
            session_bars = [
                b for b in intraday_bars
                if b.timestamp.astimezone(ET).time() >= PREMARKET_END
            ]
            if session_bars:
                levels.session_high = max(b.high for b in session_bars)
                levels.session_low = min(b.low for b in session_bars)

        self._levels[symbol] = levels
        self._persist()

        logger.info(
            "levels_built",
            symbol=symbol,
            pm_low=levels.premarket_low,
            prev_close=levels.prev_day_close,
            session_high=levels.session_high,
            session_low=levels.session_low,
        )
        return levels

    def get_key_levels(self, symbol: str) -> KeyLevels | None:
        """Get the current key levels for a symbol.

        Args:
            symbol: Ticker symbol.

        Returns:
            KeyLevels if available, None otherwise.
        """
        return self._levels.get(symbol)

    def is_at_support(
        self, price: float, levels: KeyLevels, tolerance_pct: float = 0.001
    ) -> bool:
        """Check if current price is near a support level.

        Support levels checked: premarket low, prev close (if below),
        session low, NWOG gap low, fibonacci 618 and 500 (deeper pullback = support).

        Args:
            price: Current price.
            levels: KeyLevels for the symbol.
            tolerance_pct: How close price must be (as fraction, default 0.1%).

        Returns:
            True if price is within tolerance of any support level.
        """
        support_levels: list[float] = []

        if levels.premarket_low is not None:
            support_levels.append(levels.premarket_low)
        if levels.prev_day_close is not None:
            support_levels.append(levels.prev_day_close)
        if levels.prev_day_low is not None:
            support_levels.append(levels.prev_day_low)
        if levels.session_low is not None:
            support_levels.append(levels.session_low)
        if levels.nwog is not None:
            support_levels.append(levels.nwog.gap_low)
            support_levels.append(levels.nwog.gap_mid)
        if levels.fibonacci is not None:
            support_levels.append(levels.fibonacci.fib_500)
            support_levels.append(levels.fibonacci.fib_618)
        if levels.prev_friday_close is not None:
            support_levels.append(levels.prev_friday_close)
        # 50SMA and 200SMA as support when price is above them
        if levels.sma_50 is not None and price >= levels.sma_50:
            support_levels.append(levels.sma_50)
        if levels.sma_200 is not None and price >= levels.sma_200:
            support_levels.append(levels.sma_200)
        # Demand zones
        for zone in levels.demand_zones:
            if zone.zone_type == "demand":
                support_levels.append(zone.zone_low)
                support_levels.append(zone.zone_high)

        for level in support_levels:
            if level <= 0:
                continue
            if abs(price - level) / level <= tolerance_pct:
                logger.info(
                    "at_support",
                    price=price,
                    level=round(level, 2),
                    distance_pct=round(abs(price - level) / level * 100, 4),
                )
                return True
        return False

    def is_at_resistance(
        self, price: float, levels: KeyLevels, tolerance_pct: float = 0.001
    ) -> bool:
        """Check if current price is near a resistance level.

        Resistance levels checked: session high, prev close (if above),
        NWOG gap high, fibonacci 382 (shallow pullback = resistance).

        Args:
            price: Current price.
            levels: KeyLevels for the symbol.
            tolerance_pct: How close price must be (as fraction, default 0.1%).

        Returns:
            True if price is within tolerance of any resistance level.
        """
        resistance_levels: list[float] = []

        if levels.session_high is not None:
            resistance_levels.append(levels.session_high)
        if levels.premarket_high is not None:
            resistance_levels.append(levels.premarket_high)
        if levels.prev_day_close is not None:
            resistance_levels.append(levels.prev_day_close)
        if levels.prev_day_high is not None:
            resistance_levels.append(levels.prev_day_high)
        if levels.nwog is not None:
            resistance_levels.append(levels.nwog.gap_high)
            resistance_levels.append(levels.nwog.gap_mid)
        if levels.fibonacci is not None:
            resistance_levels.append(levels.fibonacci.fib_382)
        if levels.prev_friday_close is not None:
            resistance_levels.append(levels.prev_friday_close)
        # 200SMA and 50SMA as resistance when price is below them
        if levels.sma_200 is not None and price < levels.sma_200:
            resistance_levels.append(levels.sma_200)
        if levels.sma_50 is not None and price < levels.sma_50:
            resistance_levels.append(levels.sma_50)
        # Supply zones
        for zone in levels.demand_zones:
            if zone.zone_type == "supply":
                resistance_levels.append(zone.zone_low)
                resistance_levels.append(zone.zone_high)

        for level in resistance_levels:
            if level <= 0:
                continue
            if abs(price - level) / level <= tolerance_pct:
                logger.info(
                    "at_resistance",
                    price=price,
                    level=round(level, 2),
                    distance_pct=round(abs(price - level) / level * 100, 4),
                )
                return True
        return False

    def get_levels_bias(self, symbol: str, current_price: float) -> float:
        """Get a bias score from -100 to +100 based on price position relative to key levels.

        Above key levels = bullish, below = bearish. Distance matters.

        Args:
            symbol: Ticker symbol.
            current_price: Current market price.

        Returns:
            Bias score from -100 (all below resistance) to +100 (all above support).
        """
        levels = self._levels.get(symbol)
        if levels is None:
            return 0.0

        scores: list[float] = []

        # Price vs premarket low
        if levels.premarket_low is not None and levels.premarket_low > 0:
            pct_from_pm = ((current_price - levels.premarket_low) / levels.premarket_low) * 100
            scores.append(float(min(max(pct_from_pm * 30, -100), 100)))

        # Price vs prev close
        if levels.prev_day_close is not None and levels.prev_day_close > 0:
            pct_from_close = ((current_price - levels.prev_day_close) / levels.prev_day_close) * 100
            scores.append(float(min(max(pct_from_close * 40, -100), 100)))

        # Price vs NWOG midpoint
        if levels.nwog is not None and levels.nwog.gap_mid > 0:
            pct_from_nwog = ((current_price - levels.nwog.gap_mid) / levels.nwog.gap_mid) * 100
            scores.append(float(min(max(pct_from_nwog * 25, -100), 100)))

        # Price vs session midpoint
        if levels.session_high is not None and levels.session_low is not None:
            session_mid = (levels.session_high + levels.session_low) / 2
            if session_mid > 0:
                pct_from_mid = ((current_price - session_mid) / session_mid) * 100
                scores.append(float(min(max(pct_from_mid * 35, -100), 100)))

        # Price vs 200SMA — SuperLuckeee's key macro level
        # Below 200SMA = strongly bearish, above = bullish
        if levels.sma_200 is not None and levels.sma_200 > 0:
            pct_from_200sma = ((current_price - levels.sma_200) / levels.sma_200) * 100
            # 200SMA gets heavy weighting — it's a regime-level signal
            scores.append(float(min(max(pct_from_200sma * 50, -100), 100)))

        # Price vs 50SMA — floor level
        if levels.sma_50 is not None and levels.sma_50 > 0:
            pct_from_50sma = ((current_price - levels.sma_50) / levels.sma_50) * 100
            scores.append(float(min(max(pct_from_50sma * 35, -100), 100)))

        if not scores:
            return 0.0

        avg_score = sum(scores) / len(scores)
        return round(min(max(avg_score, -100), 100), 2)


#===============================================================================
# SECTION: SIGNALS - Economic Calendar
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/calendar.py (615 lines)
#-------------------------------------------------------------------------------

"""Economic Calendar — Market Event Awareness.

Tracks market-moving economic events and adjusts trading behavior:
    - FOMC meetings (rate decisions, minutes)
    - CPI releases (inflation data, 8:30 AM ET)
    - PPI releases (producer prices, 8:30 AM ET)
    - NFP releases (jobs data, 8:30 AM ET)
    - OPEX dates (monthly 3rd Friday, weekly Fridays)

On event days, position sizes are reduced by the configured multiplier
(default 50%) to account for increased volatility and gap risk.

The 8:30 AM data candle is flagged specifically because CPI/PPI/NFP
drop at that exact time, creating massive moves in the first minute.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from esther.core.config import config

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class EventType(str, Enum):
    """Types of market-moving economic events."""

    FOMC = "FOMC"
    CPI = "CPI"
    PPI = "PPI"
    NFP = "NFP"
    OPEX_MONTHLY = "OPEX_MONTHLY"
    OPEX_WEEKLY = "OPEX_WEEKLY"


class EventImpact(str, Enum):
    """Expected market impact level."""

    HIGH = "HIGH"      # FOMC, CPI, NFP
    MEDIUM = "MEDIUM"  # PPI, OPEX monthly
    LOW = "LOW"        # OPEX weekly


class EconomicEvent(BaseModel):
    """A single economic calendar event."""

    date: date
    event_type: EventType
    name: str
    time_et: str = ""  # e.g., "08:30", "14:00", or "" if all-day
    impact: EventImpact = EventImpact.HIGH
    notes: str = ""


# --- 2025-2026 HARDCODED ECONOMIC CALENDAR ---
# These are confirmed/projected dates. Update annually.

FOMC_DATES_2026 = [
    date(2026, 1, 28),   # January meeting
    date(2026, 3, 18),   # March meeting
    date(2026, 5, 6),    # May meeting
    date(2026, 6, 17),   # June meeting
    date(2026, 7, 29),   # July meeting
    date(2026, 9, 16),   # September meeting
    date(2026, 11, 4),   # November meeting
    date(2026, 12, 16),  # December meeting
]

# CPI releases — typically 2nd or 3rd Tuesday/Wednesday of the month, 8:30 AM ET
CPI_DATES_2026 = [
    date(2026, 1, 14),
    date(2026, 2, 11),
    date(2026, 3, 11),
    date(2026, 4, 14),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 15),
    date(2026, 10, 13),
    date(2026, 11, 10),
    date(2026, 12, 9),
]

# PPI releases — typically day before or after CPI, 8:30 AM ET
PPI_DATES_2026 = [
    date(2026, 1, 15),
    date(2026, 2, 12),
    date(2026, 3, 12),
    date(2026, 4, 9),
    date(2026, 5, 13),
    date(2026, 6, 11),
    date(2026, 7, 15),
    date(2026, 8, 13),
    date(2026, 9, 16),
    date(2026, 10, 14),
    date(2026, 11, 12),
    date(2026, 12, 10),
]

# NFP (Non-Farm Payrolls) — first Friday of each month, 8:30 AM ET
NFP_DATES_2026 = [
    date(2026, 1, 2),
    date(2026, 2, 6),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 6, 5),
    date(2026, 7, 2),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
]


def _get_third_friday(year: int, month: int) -> date:
    """Calculate the third Friday of a given month (monthly OPEX)."""
    # Find the first day of the month
    first_day = date(year, month, 1)
    # Find first Friday: weekday() 4 = Friday
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    # Third Friday = first Friday + 14 days
    return first_friday + timedelta(days=14)


def _get_monthly_opex_dates(year: int) -> list[date]:
    """Get all monthly OPEX dates (3rd Friday) for a year."""
    return [_get_third_friday(year, month) for month in range(1, 13)]


class CalendarModule:
    """Economic calendar for market event awareness.

    Provides event lookups, size reduction recommendations, and
    expected move calculations. Integrates with the bias engine to
    reduce confidence on event days.
    """

    def __init__(self):
        self._cfg = config().calendar
        self._events = self._build_event_list()

    def _build_event_list(self) -> list[EconomicEvent]:
        """Build the complete event list from hardcoded dates."""
        events: list[EconomicEvent] = []

        if self._cfg.track_fomc:
            for d in FOMC_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.FOMC,
                    name=f"FOMC Rate Decision",
                    time_et="14:00",
                    impact=EventImpact.HIGH,
                    notes="Rate decision at 2 PM, press conference at 2:30 PM ET",
                ))

        if self._cfg.track_cpi:
            for d in CPI_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.CPI,
                    name="CPI Release",
                    time_et="08:30",
                    impact=EventImpact.HIGH,
                    notes="Consumer Price Index — 8:30 AM data candle",
                ))

        if self._cfg.track_ppi:
            for d in PPI_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.PPI,
                    name="PPI Release",
                    time_et="08:30",
                    impact=EventImpact.MEDIUM,
                    notes="Producer Price Index — 8:30 AM data candle",
                ))

        if self._cfg.track_nfp:
            for d in NFP_DATES_2026:
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.NFP,
                    name="Non-Farm Payrolls",
                    time_et="08:30",
                    impact=EventImpact.HIGH,
                    notes="Jobs report — 8:30 AM data candle, expect massive volume",
                ))

        if self._cfg.track_opex:
            # Monthly OPEX — 3rd Friday
            for d in _get_monthly_opex_dates(2026):
                events.append(EconomicEvent(
                    date=d,
                    event_type=EventType.OPEX_MONTHLY,
                    name="Monthly OPEX",
                    time_et="",
                    impact=EventImpact.MEDIUM,
                    notes="Monthly options expiration — pin risk, gamma exposure",
                ))

            # Weekly OPEX — every Friday (that isn't already monthly)
            monthly_dates = set(_get_monthly_opex_dates(2026))
            start = date(2026, 1, 2)
            end = date(2026, 12, 31)
            d = start
            while d <= end:
                if d.weekday() == 4 and d not in monthly_dates:  # Friday
                    events.append(EconomicEvent(
                        date=d,
                        event_type=EventType.OPEX_WEEKLY,
                        name="Weekly OPEX",
                        time_et="",
                        impact=EventImpact.LOW,
                        notes="Weekly options expiration",
                    ))
                d += timedelta(days=1)

        events.sort(key=lambda e: e.date)
        logger.info("calendar_built", total_events=len(events))
        return events

    def get_events_today(self, today: date | None = None) -> list[EconomicEvent]:
        """Get all economic events scheduled for today.

        Args:
            today: Override date for testing. Defaults to current date.

        Returns:
            List of events happening today, sorted by time.
        """
        if today is None:
            today = datetime.now(ET).date()
        return [e for e in self._events if e.date == today]

    def get_events_this_week(
        self, today: date | None = None
    ) -> list[EconomicEvent]:
        """Get all economic events for the current week (Mon-Fri).

        Args:
            today: Override date. Defaults to current date.

        Returns:
            List of events this week, sorted by date.
        """
        if today is None:
            today = datetime.now(ET).date()

        # Find Monday of this week
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)

        return [e for e in self._events if monday <= e.date <= friday]

    def is_event_day(self, today: date | None = None) -> bool:
        """Check if today has any market-moving events.

        Args:
            today: Override date.

        Returns:
            True if there are HIGH or MEDIUM impact events today.
        """
        events = self.get_events_today(today)
        return any(
            e.impact in (EventImpact.HIGH, EventImpact.MEDIUM) for e in events
        )

    def is_830_data_candle(self, now: datetime | None = None) -> bool:
        """Check if we're in the 8:30 AM data candle window.

        CPI, PPI, and NFP all drop at exactly 8:30 AM ET.
        This flag indicates we should expect extreme volatility
        in the first 1-5 minutes after the release.

        Args:
            now: Override datetime.

        Returns:
            True if current time is between 8:28-8:35 AM ET AND
            today has an 8:30 AM event.
        """
        if now is None:
            now = datetime.now(ET)
        else:
            now = now.astimezone(ET) if now.tzinfo else now

        today = now.date()
        events = self.get_events_today(today)

        # Check if any event is at 8:30 AM
        has_830_event = any(e.time_et == "08:30" for e in events)
        if not has_830_event:
            return False

        # Check if current time is in the window (8:28 - 8:35)
        current_time = now.time()
        window_start = time(8, 28)
        window_end = time(8, 35)

        return window_start <= current_time <= window_end

    def get_expected_move(self, vix: float, dte: int) -> float:
        """Calculate the implied expected move from VIX.

        The VIX represents annualized expected volatility. We convert
        to a daily (or multi-day) expected move using:
            daily_move = (VIX / 100) * price * sqrt(dte / 252)

        For SPX at ~5800 with VIX at 20:
            daily_move = (20/100) * sqrt(1/252) ≈ 1.26% ≈ 73 points

        Args:
            vix: Current VIX level.
            dte: Days to expiration (1 for 0DTE).

        Returns:
            Expected move as a percentage (e.g., 1.26 = 1.26%).
        """
        if dte <= 0:
            dte = 1
        daily_vol = vix / math.sqrt(252)
        expected_pct = daily_vol * math.sqrt(dte)
        logger.debug(
            "expected_move_calculated",
            vix=vix,
            dte=dte,
            expected_pct=round(expected_pct, 4),
        )
        return round(expected_pct, 4)

    def should_reduce_size(
        self, today: date | None = None
    ) -> tuple[bool, float]:
        """Determine if position sizes should be reduced today.

        On event days, reduce size by the configured multiplier to
        account for increased volatility and gap risk.

        Args:
            today: Override date.

        Returns:
            Tuple of (should_reduce: bool, multiplier: float).
            Multiplier is 1.0 if no reduction needed, or the configured
            reduce_size_on_event value (default 0.5) on event days.
        """
        if not self.is_event_day(today):
            return False, 1.0

        events = self.get_events_today(today)
        multiplier = self._cfg.reduce_size_on_event

        # If there's a HIGH impact event, use full reduction
        # If only MEDIUM, use a gentler reduction (midpoint between 1.0 and configured)
        has_high = any(e.impact == EventImpact.HIGH for e in events)
        if not has_high:
            multiplier = (1.0 + multiplier) / 2.0  # e.g., 0.75 instead of 0.50

        event_names = [e.name for e in events]
        logger.info(
            "size_reduction_recommended",
            events=event_names,
            multiplier=multiplier,
            has_high_impact=has_high,
        )
        return True, multiplier

    def get_confidence_adjustment(self, today: date | None = None) -> float:
        """Get confidence adjustment factor for the bias engine.

        On event days, bias confidence should be reduced because
        the data release can invalidate any technical signal.

        Returns:
            Multiplier from 0.0 to 1.0. 1.0 = full confidence.
            On event days: 0.6-0.8 depending on impact.
        """
        if not self.is_event_day(today):
            return 1.0

        events = self.get_events_today(today)
        has_high = any(e.impact == EventImpact.HIGH for e in events)
        has_multiple = len([
            e for e in events
            if e.impact in (EventImpact.HIGH, EventImpact.MEDIUM)
        ]) > 1

        if has_high and has_multiple:
            return 0.5  # Very uncertain day
        elif has_high:
            return 0.65
        else:
            return 0.80

    # ── Macro Data Feed ──────────────────────────────────────────

    async def fetch_macro_data(self) -> dict[str, Any]:
        """Fetch actual economic data from FRED (Federal Reserve) API.

        Gets actual vs expected values for key economic indicators:
        - CPI (Consumer Price Index)
        - PPI (Producer Price Index)
        - NFP (Non-Farm Payrolls)
        - GDP (Gross Domestic Product)

        Stores results in data/macro_data.json.

        Returns:
            Dict with indicator data and computed macro_bias score.
        """
        import os
        fred_api_key = os.environ.get("FRED_API_KEY", "")

        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        macro_file = data_dir / "macro_data.json"

        # FRED series IDs for key indicators
        # CPI: CPIAUCSL (CPI for All Urban Consumers)
        # PPI: PPIACO (PPI All Commodities)
        # NFP: PAYEMS (Total Nonfarm Payrolls)
        # GDP: GDP (Gross Domestic Product)
        series_map = {
            "CPI": "CPIAUCSL",
            "PPI": "PPIACO",
            "NFP": "PAYEMS",
            "GDP": "GDP",
        }

        macro_data: dict[str, Any] = {
            "fetched_at": datetime.now().isoformat(),
            "indicators": {},
            "macro_bias": 0.0,
        }

        total_bias = 0.0

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for indicator, series_id in series_map.items():
                try:
                    # Fetch last 3 observations to compare current vs previous
                    params: dict[str, Any] = {
                        "series_id": series_id,
                        "sort_order": "desc",
                        "limit": 3,
                        "file_type": "json",
                    }
                    if fred_api_key:
                        params["api_key"] = fred_api_key

                    url = "https://api.stlouisfed.org/fred/series/observations"
                    resp = await client.get(url, params=params)

                    if resp.status_code != 200:
                        logger.warning(
                            "fred_fetch_failed",
                            indicator=indicator,
                            status=resp.status_code,
                        )
                        continue

                    result = resp.json()
                    observations = result.get("observations", [])

                    if len(observations) < 2:
                        continue

                    # Latest observation = actual, previous = "expected" baseline
                    latest = observations[0]
                    previous = observations[1]

                    try:
                        actual = float(latest.get("value", 0))
                        expected = float(previous.get("value", 0))
                    except (ValueError, TypeError):
                        continue

                    # Calculate surprise (actual - expected as % of expected)
                    if expected != 0:
                        surprise_pct = ((actual - expected) / abs(expected)) * 100
                    else:
                        surprise_pct = 0.0

                    # Determine bias contribution
                    indicator_bias = self._compute_indicator_bias(
                        indicator, actual, expected, surprise_pct
                    )
                    total_bias += indicator_bias

                    macro_data["indicators"][indicator] = {
                        "actual": actual,
                        "expected": expected,
                        "surprise_pct": round(surprise_pct, 4),
                        "bias_contribution": round(indicator_bias, 2),
                        "date": latest.get("date", ""),
                        "series_id": series_id,
                    }

                    logger.info(
                        "macro_indicator_fetched",
                        indicator=indicator,
                        actual=actual,
                        expected=expected,
                        surprise_pct=round(surprise_pct, 2),
                        bias=round(indicator_bias, 2),
                    )

                except Exception as e:
                    logger.error(
                        "macro_fetch_error",
                        indicator=indicator,
                        error=str(e),
                    )
                    continue

        # Clamp total bias to [-50, +50]
        macro_bias = max(-50.0, min(50.0, total_bias))
        macro_data["macro_bias"] = round(macro_bias, 2)

        # Save to file
        try:
            macro_file.write_text(json.dumps(macro_data, indent=2))
        except Exception as e:
            logger.error("macro_data_save_failed", error=str(e))

        logger.info("macro_data_fetched", bias=macro_bias, indicators=len(macro_data["indicators"]))
        return macro_data

    def _compute_indicator_bias(
        self,
        indicator: str,
        actual: float,
        expected: float,
        surprise_pct: float,
    ) -> float:
        """Compute bias contribution for a single macro indicator.

        Logic:
        - CPI higher than expected = hawkish = bearish (Fed tightens)
        - PPI higher than expected = hawkish = bearish (inflation upstream)
        - NFP higher than expected = hawkish = initially bearish (Fed stays tight)
        - GDP lower than expected = bearish (economy slowing)

        Args:
            indicator: "CPI", "PPI", "NFP", or "GDP".
            actual: Actual reported value.
            expected: Expected/previous value.
            surprise_pct: (actual - expected) / expected * 100.

        Returns:
            Bias contribution from -15 to +15 per indicator.
        """
        # Scale: 1% surprise ≈ ±10 bias points, capped at ±15
        base_magnitude = min(15.0, abs(surprise_pct) * 10)

        if indicator == "CPI":
            # CPI higher = hawkish = bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "PPI":
            # PPI higher = upstream inflation = bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "NFP":
            # Strong jobs = Fed stays tight = initially bearish
            return -base_magnitude if actual > expected else base_magnitude

        elif indicator == "GDP":
            # GDP lower = economy slowing = bearish
            # GDP higher = economy strong = bullish
            return base_magnitude if actual > expected else -base_magnitude

        return 0.0

    def get_macro_bias(self) -> float:
        """Get the cached macro bias score from data/macro_data.json.

        Returns:
            Macro bias score from -50 to +50. Returns 0.0 if no data.
        """
        macro_file = Path("data") / "macro_data.json"
        if not macro_file.exists():
            return 0.0

        try:
            data = json.loads(macro_file.read_text())
            # Only use data less than 24 hours old
            fetched_at = data.get("fetched_at", "")
            if fetched_at:
                fetch_time = datetime.fromisoformat(fetched_at)
                if (datetime.now() - fetch_time).total_seconds() > 86400:
                    return 0.0  # Data too old
            return float(data.get("macro_bias", 0.0))
        except (json.JSONDecodeError, Exception):
            return 0.0


#===============================================================================
# SECTION: SIGNALS - Pre-Market Scanner
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/premarket.py (892 lines)
#-------------------------------------------------------------------------------

"""Pre-Market Research System — 4:00 AM to 9:30 AM ET Pipeline.

Runs the complete pre-market analysis pipeline that prepares everything
Esther needs before market open. Pulls flow data, builds key levels,
checks the economic calendar, scans the LEAP watchlist, and generates
a trade plan with recommended pillars and strike zones.

Schedule (all times ET):
    4:00 AM  — Wake up, start monitoring
    6:00 AM  — Pull overnight flow data, dark pool, check futures
    7:00 AM  — Build initial key levels (PM high/low developing)
    8:00 AM  — Full flow analysis, check economic calendar
    8:30 AM  — DATA CANDLE — watch for CPI/PPI/NFP releases
    9:00 AM  — Final bias computation, set trade plan
    9:15 AM  — Generate PreMarketReport and publish to engine
    9:30 AM  — Market open — hand off to main engine
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config, env
from esther.data.tradier import TradierClient
from esther.signals.calendar import CalendarModule, EventImpact
from esther.signals.flow import (
    FlowAnalyzer,
    FlowOptionType,
)
from esther.signals.levels import LevelTracker
from esther.signals.regime import RegimeDetector
from esther.signals.watchlist import BuyZoneStatus, WatchlistMonitor

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")

# Default symbols to track
DEFAULT_SYMBOLS = ["SPY", "SPX", "QQQ"]


# ---------------------------------------------------------------------------
# PreMarketReport — delivered to the engine at 9:15 AM
# ---------------------------------------------------------------------------


class PreMarketReport(BaseModel):
    """Complete pre-market analysis delivered to the engine at 9:15 AM."""

    generated_at: datetime

    # Market overview
    spy_price: float = 0.0
    spx_price: float = 0.0
    vix_level: float = 0.0
    futures_direction: str = ""  # "UP", "DOWN", "FLAT"
    overnight_range_pct: float = 0.0

    # Key levels for the day (6 levels per SuperLuckeee)
    key_levels: dict[str, dict] = Field(default_factory=dict)
    # {symbol: {pm_high, pm_low, prev_high, prev_low, prev_close, sma_200, sma_50}}

    # Flow analysis
    flow_direction: str = ""  # "BULLISH", "BEARISH", "NEUTRAL"
    flow_bias_score: float = 0.0
    top_flow_alerts: list[dict] = Field(default_factory=list)
    dark_pool_summary: str = ""
    put_call_ratio: float = 0.0
    max_pain: dict[str, float] = Field(default_factory=dict)

    # Regime
    regime_state: str = ""  # "BULLISH", "BEARISH", "TRANSITIONING"
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0

    # Calendar
    is_event_day: bool = False
    event_name: str = ""
    expected_move: float = 0.0

    # LEAP watchlist alerts
    watchlist_in_zone: list[str] = Field(default_factory=list)
    watchlist_approaching: list[str] = Field(default_factory=list)

    # Trade plan
    recommended_pillars: list[int] = Field(default_factory=list)
    recommended_direction: str = ""  # "BULL", "BEAR", "NEUTRAL"
    call_zones: str = ""
    put_zones: str = ""
    ic_strikes: str = ""
    confidence: float = 0.0

    # Risk adjustments
    sizing_modifier: float = 1.0
    vix_regime: str = ""  # "IC_SWEET_SPOT", "NORMAL", "ELEVATED", "PANIC"
    chop_warning: bool = False


# ---------------------------------------------------------------------------
# PreMarketResearcher
# ---------------------------------------------------------------------------


class PreMarketResearcher:
    """Runs the pre-market analysis pipeline from 4 AM to 9:30 AM ET.

    Coordinates all signal modules (flow, levels, regime, calendar,
    watchlist) into a single PreMarketReport that drives the trading
    engine's decisions for the day.

    Usage::

        researcher = PreMarketResearcher()
        report = await researcher.run_full_scan()
        telegram_msg = researcher.format_telegram_report(report)
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        flow_analyzer: FlowAnalyzer | None = None,
        level_tracker: LevelTracker | None = None,
        regime_detector: RegimeDetector | None = None,
        calendar_module: CalendarModule | None = None,
        watchlist_monitor: WatchlistMonitor | None = None,
    ) -> None:
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._flow = flow_analyzer or FlowAnalyzer()
        self._levels = level_tracker or LevelTracker()
        self._regime = regime_detector or RegimeDetector()
        self._calendar = calendar_module or CalendarModule()
        self._watchlist = watchlist_monitor or WatchlistMonitor()
        self._cfg = config()
        self._env = env()

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def run_full_scan(self) -> PreMarketReport:
        """Run the complete pre-market analysis pipeline.

        Executes all sub-scans in order and assembles the final report.
        Designed to be called at ~9:00-9:15 AM ET after data has settled.

        Returns:
            PreMarketReport with all fields populated.
        """
        report = PreMarketReport(generated_at=datetime.now(ET))

        # 1. Fetch current quotes for market overview
        try:
            await self._fetch_market_overview(report)
        except Exception as exc:
            logger.error("premarket_market_overview_failed", error=str(exc))

        # 2. Overnight flow + dark pool
        try:
            flow_data = await self.check_overnight_flow()
            report.top_flow_alerts = flow_data.get("top_alerts", [])
            report.dark_pool_summary = flow_data.get("dark_pool_summary", "")
            report.put_call_ratio = flow_data.get("put_call_ratio", 0.0)
        except Exception as exc:
            logger.error("premarket_overnight_flow_failed", error=str(exc))

        # 3. Build key levels
        try:
            levels_data = await self.build_daily_levels(self._symbols)
            report.key_levels = levels_data
        except Exception as exc:
            logger.error("premarket_levels_failed", error=str(exc))

        # 4. Flow direction analysis
        try:
            direction, score = await self.analyze_flow_direction()
            report.flow_direction = direction
            report.flow_bias_score = score
        except Exception as exc:
            logger.error("premarket_flow_analysis_failed", error=str(exc))

        # 5. Max pain
        try:
            await self._fetch_max_pain(report)
        except Exception as exc:
            logger.error("premarket_max_pain_failed", error=str(exc))

        # 6. Regime detection
        try:
            await self._detect_regime(report)
        except Exception as exc:
            logger.error("premarket_regime_failed", error=str(exc))

        # 7. Economic calendar
        try:
            cal_data = await self.check_economic_calendar()
            report.is_event_day = cal_data.get("is_event_day", False)
            report.event_name = cal_data.get("event_name", "")
            report.expected_move = cal_data.get("expected_move", 0.0)
        except Exception as exc:
            logger.error("premarket_calendar_failed", error=str(exc))

        # 8. LEAP watchlist scan
        try:
            wl_data = await self.scan_watchlist()
            report.watchlist_in_zone = wl_data.get("in_zone", [])
            report.watchlist_approaching = wl_data.get("approaching", [])
        except Exception as exc:
            logger.error("premarket_watchlist_failed", error=str(exc))

        # 9. Generate trade plan (uses all the above)
        try:
            report = await self.generate_trade_plan(report)
        except Exception as exc:
            logger.error("premarket_trade_plan_failed", error=str(exc))

        logger.info(
            "premarket_scan_complete",
            spy=report.spy_price,
            vix=report.vix_level,
            direction=report.flow_direction,
            confidence=report.confidence,
            pillars=report.recommended_pillars,
        )
        return report

    # ------------------------------------------------------------------
    # Sub-scan: Market overview (quotes)
    # ------------------------------------------------------------------

    async def _fetch_market_overview(self, report: PreMarketReport) -> None:
        """Fetch SPY, SPX, VIX quotes and derive futures direction."""
        async with TradierClient() as client:
            quotes = await client.get_quotes(["SPY", "SPX", "VIX"])

        quote_map = {q.symbol: q for q in quotes}

        spy_q = quote_map.get("SPY")
        spx_q = quote_map.get("SPX")
        vix_q = quote_map.get("VIX")

        if spy_q:
            report.spy_price = spy_q.last
            # Derive futures direction from change
            if spy_q.change > 0.15:
                report.futures_direction = "UP"
            elif spy_q.change < -0.15:
                report.futures_direction = "DOWN"
            else:
                report.futures_direction = "FLAT"
            # Overnight range as pct of close
            if spy_q.close and spy_q.close > 0:
                day_range = spy_q.high - spy_q.low
                report.overnight_range_pct = round(
                    (day_range / spy_q.close) * 100, 2
                )

        if spx_q:
            report.spx_price = spx_q.last

        if vix_q:
            report.vix_level = vix_q.last

        logger.info(
            "market_overview_fetched",
            spy=report.spy_price,
            spx=report.spx_price,
            vix=report.vix_level,
            futures=report.futures_direction,
        )

    # ------------------------------------------------------------------
    # Sub-scan: Overnight flow
    # ------------------------------------------------------------------

    async def check_overnight_flow(self) -> dict:
        """Pull UW flow alerts and dark pool from overnight session.

        Returns:
            Dict with top_alerts, dark_pool_summary, put_call_ratio.
        """
        result: dict[str, Any] = {
            "top_alerts": [],
            "dark_pool_summary": "",
            "put_call_ratio": 0.0,
        }

        # Flow alerts for SPY/SPX
        all_entries = []
        for symbol in ["SPY", "SPX"]:
            entries = await self._flow.get_flow(symbol)
            all_entries.extend(entries)

        if all_entries:
            summary = self._flow.analyze_flow(all_entries)
            result["put_call_ratio"] = summary.put_call_ratio

            # Top 5 alerts by premium
            top = sorted(all_entries, key=lambda e: e.premium, reverse=True)[:5]
            result["top_alerts"] = [
                {
                    "symbol": e.symbol,
                    "strike": e.strike,
                    "expiry": e.expiry,
                    "type": e.option_type.value,
                    "premium": e.premium,
                    "side": e.side.value,
                    "has_sweep": e.has_sweep,
                }
                for e in top
            ]

        # Dark pool
        dp_entries = await self._flow.get_dark_pool(limit=20)
        if dp_entries:
            # Summarize the biggest dark pool prints
            top_dp = sorted(dp_entries, key=lambda d: d.size, reverse=True)[:3]
            parts = []
            for dp in top_dp:
                direction = "buying" if dp.price >= dp.nbbo_ask else "selling"
                parts.append(
                    f"{dp.ticker} {direction} at ${dp.price:.2f} ({dp.size:,} shares)"
                )
            result["dark_pool_summary"] = "; ".join(parts)

        logger.info(
            "overnight_flow_checked",
            alert_count=len(result["top_alerts"]),
            pcr=result["put_call_ratio"],
            dark_pool=result["dark_pool_summary"][:80],
        )
        return result

    # ------------------------------------------------------------------
    # Sub-scan: Key levels
    # ------------------------------------------------------------------

    async def build_daily_levels(self, symbols: list[str]) -> dict:
        """Calculate all 6 key levels for each symbol.

        Uses Tradier for intraday (premarket) bars and daily bars.
        Returns dict of {symbol: {pm_high, pm_low, prev_high, prev_low,
        prev_close, sma_200, sma_50}}.
        """
        levels_data: dict[str, dict] = {}
        today = datetime.now(ET).date()

        async with TradierClient() as client:
            for symbol in symbols:
                try:
                    # Daily bars for SMA + prev day levels (need 200+ days)
                    start_date = today - timedelta(days=365)
                    daily_bars = await client.get_bars(
                        symbol, interval="daily", start=start_date, end=today
                    )

                    # Intraday bars for premarket levels
                    intraday_bars = await client.get_bars(
                        symbol,
                        interval="5min",
                        start=today,
                        end=today,
                    )

                    # Build via LevelTracker
                    key_lvls = self._levels.build_levels(
                        symbol=symbol,
                        intraday_bars=intraday_bars,
                        daily_bars=daily_bars,
                    )

                    levels_data[symbol] = {
                        "pm_high": key_lvls.premarket_high,
                        "pm_low": key_lvls.premarket_low,
                        "prev_high": key_lvls.prev_day_high,
                        "prev_low": key_lvls.prev_day_low,
                        "prev_close": key_lvls.prev_day_close,
                        "sma_200": key_lvls.sma_200,
                        "sma_50": key_lvls.sma_50,
                    }

                except Exception as exc:
                    logger.error(
                        "build_levels_failed", symbol=symbol, error=str(exc)
                    )
                    levels_data[symbol] = {}

        logger.info("daily_levels_built", symbols=list(levels_data.keys()))
        return levels_data

    # ------------------------------------------------------------------
    # Sub-scan: Flow direction
    # ------------------------------------------------------------------

    async def analyze_flow_direction(self) -> tuple[str, float]:
        """Determine overall flow direction and bias score.

        Aggregates flow from SPY + SPX and returns the direction
        label and the raw score (-100 to +100).
        """
        all_entries = []
        for symbol in ["SPY", "SPX"]:
            entries = await self._flow.get_flow(symbol)
            all_entries.extend(entries)

        if not all_entries:
            return "NEUTRAL", 0.0

        summary = self._flow.analyze_flow(all_entries)
        score = summary.flow_bias_score

        if score > 15:
            direction = "BULLISH"
        elif score < -15:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        logger.info(
            "flow_direction_analyzed",
            direction=direction,
            score=round(score, 1),
            call_prem=summary.total_call_premium,
            put_prem=summary.total_put_premium,
        )
        return direction, round(score, 1)

    # ------------------------------------------------------------------
    # Sub-scan: Max pain
    # ------------------------------------------------------------------

    async def _fetch_max_pain(self, report: PreMarketReport) -> None:
        """Fetch max pain data for tracked symbols."""
        for symbol in self._symbols:
            mp = await self._flow.get_max_pain(symbol)
            if mp:
                report.max_pain[symbol] = mp.max_pain

    # ------------------------------------------------------------------
    # Sub-scan: Regime
    # ------------------------------------------------------------------

    async def _detect_regime(self, report: PreMarketReport) -> None:
        """Detect market regime from SPY daily bars."""
        async with TradierClient() as client:
            today = datetime.now(ET).date()
            start = today - timedelta(days=365)
            daily_bars = await client.get_bars(
                "SPY", interval="daily", start=start, end=today
            )

        if daily_bars:
            result = self._regime.detect_regime(daily_bars)
            report.regime_state = result.state.value
            report.sma_20 = result.sma_fast
            report.sma_50 = result.sma_slow

            # Also grab 200SMA from level tracker
            sma_200 = self._levels.calculate_sma(daily_bars, 200)
            if sma_200:
                report.sma_200 = sma_200

    # ------------------------------------------------------------------
    # Sub-scan: Economic calendar
    # ------------------------------------------------------------------

    async def check_economic_calendar(self) -> dict:
        """Check if today is FOMC/CPI/PPI/NFP and get expected move.

        Returns:
            Dict with is_event_day, event_name, expected_move, sizing_modifier.
        """
        today = datetime.now(ET).date()
        events = self._calendar.get_events_today(today)
        is_event = self._calendar.is_event_day(today)
        should_reduce, modifier = self._calendar.should_reduce_size(today)

        event_name = ""
        if events:
            # Pick the highest-impact event name
            high_events = [
                e for e in events if e.impact == EventImpact.HIGH
            ]
            if high_events:
                event_name = high_events[0].name
            else:
                event_name = events[0].name

        # Expected move from VIX (will be updated later with actual VIX)
        # Use a placeholder — the caller sets real VIX after market overview
        expected_move = 0.0

        return {
            "is_event_day": is_event,
            "event_name": event_name,
            "expected_move": expected_move,
            "sizing_modifier": modifier,
        }

    # ------------------------------------------------------------------
    # Sub-scan: LEAP watchlist
    # ------------------------------------------------------------------

    async def scan_watchlist(self) -> dict:
        """Check LEAP watchlist for buy zone entries.

        Fetches current prices for all watchlist symbols via Tradier,
        then scans for stocks in or approaching their buy zones.

        Returns:
            Dict with in_zone and approaching symbol lists.
        """
        symbols = [
            e.symbol
            for e in self._watchlist.watchlist
            if e.buy_zone_high > 0
        ]
        if not symbols:
            return {"in_zone": [], "approaching": []}

        prices: dict[str, float] = {}
        try:
            async with TradierClient() as client:
                quotes = await client.get_quotes(symbols)
            for q in quotes:
                if q.last > 0:
                    prices[q.symbol] = q.last
        except Exception as exc:
            logger.error("watchlist_quotes_failed", error=str(exc))
            return {"in_zone": [], "approaching": []}

        alerts = self._watchlist.scan(prices)

        in_zone = [
            a.symbol for a in alerts if a.status == BuyZoneStatus.IN_ZONE
        ]
        approaching = [
            a.symbol
            for a in alerts
            if a.status == BuyZoneStatus.ENTERING_ZONE
        ]

        # Also include previously-identified stocks still in zone
        for entry in self._watchlist.get_in_zone():
            if entry.symbol not in in_zone:
                in_zone.append(entry.symbol)
        for entry in self._watchlist.get_approaching():
            if entry.symbol not in approaching:
                approaching.append(entry.symbol)

        logger.info(
            "watchlist_scanned",
            in_zone=in_zone,
            approaching=approaching,
        )
        return {"in_zone": in_zone, "approaching": approaching}

    # ------------------------------------------------------------------
    # Trade plan generation
    # ------------------------------------------------------------------

    async def generate_trade_plan(
        self, report: PreMarketReport
    ) -> PreMarketReport:
        """Set recommended pillars, directions, and strike zones.

        Uses VIX level, flow direction, regime, and calendar to determine
        the optimal trading plan for the day.

        Args:
            report: Partially filled PreMarketReport.

        Returns:
            Same report with trade plan fields populated.
        """
        vix = report.vix_level
        flow_score = report.flow_bias_score

        # --- VIX regime classification ---
        if 18 <= vix <= 25:
            report.vix_regime = "IC_SWEET_SPOT"
        elif vix < 18:
            report.vix_regime = "NORMAL"
        elif 25 < vix <= 35:
            report.vix_regime = "ELEVATED"
        else:
            report.vix_regime = "PANIC"

        # --- Expected move from VIX ---
        if vix > 0 and report.spx_price > 0:
            daily_vol = vix / math.sqrt(252)
            report.expected_move = round(
                report.spx_price * daily_vol / 100, 1
            )

        # --- Sizing modifier ---
        if report.is_event_day:
            report.sizing_modifier = 0.5
        elif report.vix_regime == "PANIC":
            report.sizing_modifier = 0.25
        elif report.vix_regime == "ELEVATED":
            report.sizing_modifier = 0.75
        else:
            report.sizing_modifier = 1.0

        # --- Chop warning ---
        # Chop = neutral flow + no clear regime + range-bound overnight
        if (
            abs(flow_score) < 10
            and report.overnight_range_pct < 0.5
            and report.regime_state != "TRANSITIONING"
        ):
            report.chop_warning = True

        # --- Pillar selection ---
        # P1 (Iron Condor) — VIX in sweet spot or elevated, neutral-ish flow
        # P2 (Bear Put Spread) — bearish flow
        # P3 (Bull Call Spread) — bullish flow
        # P4 (Directional momentum) — strong directional signal

        pillars: list[int] = []

        if report.vix_regime in ("IC_SWEET_SPOT", "ELEVATED"):
            pillars.append(1)  # ICs are primary when VIX is juicy

        if flow_score < -20:
            pillars.append(2)  # Bear spread
            if flow_score < -50:
                pillars.append(4)  # Strong bear momentum
        elif flow_score > 20:
            pillars.append(3)  # Bull spread
            if flow_score > 50:
                pillars.append(4)  # Strong bull momentum

        # Default to P1 if nothing else selected
        if not pillars:
            pillars.append(1)

        report.recommended_pillars = sorted(set(pillars))

        # --- Direction ---
        if flow_score > 15:
            report.recommended_direction = "BULL"
        elif flow_score < -15:
            report.recommended_direction = "BEAR"
        else:
            report.recommended_direction = "NEUTRAL"

        # --- Strike zones ---
        spy_levels = report.key_levels.get("SPY", {})
        spy_price = report.spy_price

        if spy_price > 0:
            # Round to nearest 5 for clean zones
            base = round(spy_price / 5) * 5

            # Call zone: above current price
            call_low = base + 5
            call_high = call_low + 5
            report.call_zones = f"SPY calls {call_low}-{call_high}"

            # Put zone: below current price
            put_high = base - 5
            put_low = put_high - 5
            report.put_zones = f"SPY puts {put_high}-{put_low}"

            # IC strikes: use expected move for short strikes
            if report.expected_move > 0:
                em = report.expected_move
                # SPX IC — short strikes at ~1 expected move, wings 10 wide
                spx_price = report.spx_price or spy_price * 10
                short_call = round((spx_price + em) / 10) * 10
                long_call = short_call + 10
                short_put = round((spx_price - em) / 10) * 10
                long_put = short_put - 10
                report.ic_strikes = (
                    f"IC: {short_call}/{long_call} calls, "
                    f"{short_put}/{long_put} puts"
                )

        # --- Confidence ---
        # Base confidence from flow conviction
        confidence = min(abs(flow_score) / 100, 0.5) + 0.3

        # Calendar adjustment
        cal_adj = self._calendar.get_confidence_adjustment()
        confidence *= cal_adj

        # Regime alignment boost
        if (
            report.regime_state == "BULLISH"
            and report.recommended_direction == "BULL"
        ) or (
            report.regime_state == "BEARISH"
            and report.recommended_direction == "BEAR"
        ):
            confidence += 0.1

        # Chop penalty
        if report.chop_warning:
            confidence *= 0.7

        report.confidence = round(min(max(confidence, 0.0), 1.0), 2)

        logger.info(
            "trade_plan_generated",
            pillars=report.recommended_pillars,
            direction=report.recommended_direction,
            vix_regime=report.vix_regime,
            confidence=report.confidence,
            sizing=report.sizing_modifier,
        )
        return report

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram_report(self, report: PreMarketReport) -> str:
        """Format the report as a Telegram message for Shawn.

        Produces a concise, emoji-rich summary of the pre-market analysis
        that fits well in a Telegram chat.

        Args:
            report: Completed PreMarketReport.

        Returns:
            Formatted string ready for Telegram.
        """
        now = report.generated_at
        day_str = now.strftime("%a %b %d")

        # VIX emoji
        if report.vix_level >= 30:
            vix_emoji = "🔴"
        elif report.vix_level >= 20:
            vix_emoji = "🟡"
        else:
            vix_emoji = "🟢"

        # Futures direction emoji
        futures_emoji = {
            "UP": "🟢",
            "DOWN": "🔴",
            "FLAT": "⚪",
        }.get(report.futures_direction, "⚪")

        lines = [
            f"⚡ ESTHER PRE-MARKET REPORT — {day_str}",
            "",
            "📊 MARKET",
            f"SPY: ${report.spy_price:.2f} | SPX: ${report.spx_price:,.0f} | VIX: {report.vix_level:.2f} {vix_emoji}",
            f"Futures: {report.futures_direction} {futures_emoji} | Overnight range: {report.overnight_range_pct:.1f}%",
        ]

        # Key levels (SPY)
        spy_lvls = report.key_levels.get("SPY", {})
        if spy_lvls:
            lines.append("")
            lines.append("📈 KEY LEVELS (SPY)")
            pm_high = spy_lvls.get("pm_high")
            pm_low = spy_lvls.get("pm_low")
            if pm_high is not None and pm_low is not None:
                lines.append(
                    f"PM High: ${pm_high:.2f} | PM Low: ${pm_low:.2f}"
                )
            prev_close = spy_lvls.get("prev_close")
            sma_200 = spy_lvls.get("sma_200")
            if prev_close is not None:
                sma_str = f" | 200SMA: ${sma_200:.0f}" if sma_200 else ""
                lines.append(f"Prev Close: ${prev_close:.2f}{sma_str}")
            spy_mp = report.max_pain.get("SPY")
            if spy_mp:
                lines.append(f"Max Pain: ${spy_mp:.0f}")

        # Flow
        lines.append("")
        lines.append("🐋 FLOW")
        lines.append(
            f"Direction: {report.flow_direction} ({report.flow_bias_score:+.1f})"
        )
        if report.put_call_ratio > 0:
            lines.append(f"Put/Call Ratio: {report.put_call_ratio:.2f}")
        if report.top_flow_alerts:
            top = report.top_flow_alerts[:3]
            alert_parts = []
            for a in top:
                strike = a.get("strike", 0)
                prem_k = a.get("premium", 0) / 1000
                otype = a.get("type", "?")[0].upper()
                alert_parts.append(
                    f"{a.get('symbol', '?')} {strike:.0f}{otype} ${prem_k:.0f}K"
                )
            lines.append(f"Top: {', '.join(alert_parts)}")
        if report.dark_pool_summary:
            lines.append(f"Dark pool: {report.dark_pool_summary}")

        # Calendar
        lines.append("")
        lines.append("📅 CALENDAR")
        if report.is_event_day:
            lines.append(f"⚠️ {report.event_name}")
            if report.expected_move > 0:
                lines.append(f"Expected move: ±{report.expected_move:.0f} pts")
        else:
            lines.append("No major events today")

        # Trade plan
        lines.append("")
        lines.append("🎯 TRADE PLAN")
        pillar_names = {
            1: "P1 (IC)",
            2: "P2 (Bear Spread)",
            3: "P3 (Bull Spread)",
            4: "P4 (Momentum)",
        }
        pillar_str = ", ".join(
            pillar_names.get(p, f"P{p}") for p in report.recommended_pillars
        )
        vix_note = ""
        if report.vix_regime == "IC_SWEET_SPOT":
            vix_note = f" — VIX {report.vix_level:.0f} = IC sweet spot"
        elif report.vix_regime == "PANIC":
            vix_note = f" — VIX {report.vix_level:.0f} ⚠️ PANIC"

        lines.append(f"Pillar: {pillar_str}{vix_note}")

        if report.ic_strikes:
            lines.append(f"IC Zone: {report.ic_strikes.replace('IC: ', '')}")
        if report.recommended_direction == "BULL" and report.call_zones:
            lines.append(f"Calls: {report.call_zones}")
        elif report.recommended_direction == "BEAR" and report.put_zones:
            lines.append(f"Puts: {report.put_zones}")

        # Sizing
        size_label = {
            1.0: "full",
            0.75: "reduced",
            0.5: "half (event day)",
            0.25: "quarter (panic)",
        }.get(report.sizing_modifier, f"{report.sizing_modifier:.0%}")
        lines.append(f"Size: {size_label}")
        lines.append(f"Confidence: {report.confidence:.0%}")

        if report.chop_warning:
            lines.append("⚠️ CHOP WARNING — low conviction, tight stops")

        # LEAP watchlist
        if report.watchlist_in_zone or report.watchlist_approaching:
            lines.append("")
            lines.append("📋 LEAP WATCH")
            for sym in report.watchlist_in_zone:
                entry = self._find_watchlist_entry(sym)
                if entry:
                    lines.append(
                        f"🟢 {sym} IN BUY ZONE "
                        f"(${entry.current_price:.0f} → "
                        f"${entry.buy_zone_low:.0f}-{entry.buy_zone_high:.0f})"
                    )
                else:
                    lines.append(f"🟢 {sym} IN BUY ZONE")
            for sym in report.watchlist_approaching:
                entry = self._find_watchlist_entry(sym)
                if entry:
                    lines.append(
                        f"🟡 {sym} approaching buy zone "
                        f"(${entry.current_price:.0f} → "
                        f"${entry.buy_zone_low:.0f}-{entry.buy_zone_high:.0f})"
                    )
                else:
                    lines.append(f"🟡 {sym} approaching buy zone")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_watchlist_entry(self, symbol: str) -> Any:
        """Find a watchlist entry by symbol."""
        for entry in self._watchlist.watchlist:
            if entry.symbol == symbol:
                return entry
        return None


#===============================================================================
# SECTION: SIGNALS - Re-Entry Guard
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/reentry.py (159 lines)
#-------------------------------------------------------------------------------

"""Smart Re-entry Guard — Candle Confirmation After Losses.

Instead of a dumb 30-minute cooldown, this module requires price
confirmation before re-entering a symbol after a loss:

    - BULL re-entry (calls/bull puts): Need 2 consecutive green candles
    - BEAR re-entry (puts/bear calls): Need 2 consecutive red candles
    - This ensures momentum has shifted before re-entering

Uses 5-minute bars for fast confirmation while filtering noise.

From @SuperLuckeee: "Wait for confirmation. Don't revenge trade."
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ReentryBlock(BaseModel):
    """Tracks a blocked symbol awaiting candle confirmation."""
    symbol: str
    direction: str  # "BULL" or "BEAR" — the direction that LOST
    loss_time: datetime
    loss_pnl: float
    confirmed: bool = False  # True once candle confirmation is met


class ReentryGuard:
    """Manages re-entry after losses using candle confirmation.

    After a loss on symbol X in direction D:
    - To re-enter BEAR (puts): need 2 consecutive red 5m candles
    - To re-enter BULL (calls): need 2 consecutive green 5m candles
    - To re-enter the OPPOSITE direction: allowed immediately (inversion)
    """

    def __init__(self, required_candles: int = 2):
        self._blocks: dict[str, ReentryBlock] = {}
        self._required_candles = required_candles

    def record_loss(self, symbol: str, direction: str, pnl: float) -> None:
        """Record a loss — block re-entry in same direction until confirmed."""
        self._blocks[symbol] = ReentryBlock(
            symbol=symbol,
            direction=direction,
            loss_time=datetime.now(),
            loss_pnl=pnl,
        )
        logger.info(
            "reentry_blocked",
            symbol=symbol,
            direction=direction,
            pnl=pnl,
            required=f"{self._required_candles} confirming candles",
        )

    def can_reenter(self, symbol: str, direction: str) -> bool:
        """Check if re-entry is allowed.

        Returns True if:
        - No block exists for this symbol
        - Direction is OPPOSITE to the losing direction (inversion = OK)
        - Candle confirmation has been met
        """
        if symbol not in self._blocks:
            return True

        block = self._blocks[symbol]

        # Opposite direction is always allowed (inversion logic)
        if direction != block.direction:
            logger.info(
                "reentry_allowed_inversion",
                symbol=symbol,
                lost_direction=block.direction,
                new_direction=direction,
            )
            return True

        # Same direction — need confirmation
        if block.confirmed:
            # Already confirmed, allow and clear
            del self._blocks[symbol]
            return True

        logger.info(
            "reentry_denied",
            symbol=symbol,
            direction=direction,
            reason=f"awaiting {self._required_candles} confirming candles",
        )
        return False

    def check_candles(self, symbol: str, bars: list[Any]) -> bool:
        """Check recent bars for candle confirmation.

        Call this with recent 5-minute bars after a loss.

        Args:
            symbol: Ticker symbol.
            bars: Recent bars (need at least `required_candles` bars).
                  Each bar must have .open and .close attributes.

        Returns:
            True if confirmation achieved.
        """
        if symbol not in self._blocks:
            return True  # No block

        block = self._blocks[symbol]
        if block.confirmed:
            return True

        if len(bars) < self._required_candles:
            return False

        # Get the most recent N candles
        recent = bars[-self._required_candles:]

        if block.direction == "BEAR":
            # Lost on BEAR → need 2 red candles to confirm bearish re-entry
            confirmed = all(bar.close < bar.open for bar in recent)
        elif block.direction == "BULL":
            # Lost on BULL → need 2 green candles to confirm bullish re-entry
            confirmed = all(bar.close > bar.open for bar in recent)
        else:
            confirmed = True  # Unknown direction, allow

        if confirmed:
            block.confirmed = True
            logger.info(
                "reentry_confirmed",
                symbol=symbol,
                direction=block.direction,
                candles=self._required_candles,
            )
            return True

        return False

    def clear(self, symbol: str | None = None) -> None:
        """Clear blocks. If symbol given, clear just that one."""
        if symbol:
            self._blocks.pop(symbol, None)
        else:
            self._blocks.clear()

    @property
    def blocked_symbols(self) -> list[str]:
        """List of currently blocked symbols."""
        return list(self._blocks.keys())


#===============================================================================
# SECTION: SIGNALS - IFVG Detection
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/ifvg.py (483 lines)
#-------------------------------------------------------------------------------

"""Inverse Fair Value Gap (IFVG) Detection — ICT Concept.

Fair Value Gaps (FVGs) are 3-candle patterns where price gaps through a zone,
leaving an imbalance. When price returns to fill the gap and reverses,
that's an Inverse FVG — one of the highest-probability entry signals.

FVG Types:
    - Bullish FVG: candle 1 high < candle 3 low (price gapped up)
    - Bearish FVG: candle 1 low > candle 3 high (price gapped down)

IFVG Entry Signals:
    - Bullish IFVG: price drops into a bullish FVG zone and bounces → BUY
    - Bearish IFVG: price rises into a bearish FVG zone and rejects → SELL

The FVG zone (high, low, mid) provides natural targets and stops.
Multi-timeframe confluence (1m + 5m) dramatically increases win rate.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel

from esther.data.tradier import Bar

logger = structlog.get_logger(__name__)


class FVGDirection(str, Enum):
    """Direction of the Fair Value Gap."""

    BULLISH = "BULLISH"  # Gap up — candle1 high < candle3 low
    BEARISH = "BEARISH"  # Gap down — candle1 low > candle3 high


class FVG(BaseModel):
    """A detected Fair Value Gap zone.

    The FVG zone is defined by the gap between candle 1 and candle 3.
    This zone acts as a magnet — price tends to return to fill it.
    """

    direction: FVGDirection
    zone_high: float    # Top of the gap zone
    zone_low: float     # Bottom of the gap zone
    zone_mid: float     # Midpoint — key reversal level
    candle1_idx: int    # Index of the first candle in the pattern
    candle3_idx: int    # Index of the third candle
    timestamp: datetime  # Timestamp of candle 2 (the gap candle)
    filled: bool = False  # Whether price has returned to the zone
    invalidated: bool = False  # Whether price blew through the zone


class IFVGSignal(BaseModel):
    """Signal when price reverses off a Fair Value Gap."""

    direction: FVGDirection  # BULLISH = buy signal, BEARISH = sell signal
    fvg: FVG
    entry_price: float       # Price where the reversal was detected
    target_price: float      # Target based on FVG zone
    stop_price: float        # Stop loss just beyond the FVG zone
    risk_reward: float       # Target/stop ratio
    timestamp: datetime
    confidence: float = 0.0  # 0-1, based on confluence factors


class IFVGEntry(BaseModel):
    """Final entry signal combining multi-timeframe analysis.

    This is the actual trade signal that the execution engine uses.
    Requires confluence between 1m and 5m timeframes.
    """

    symbol: str
    signal: IFVGSignal
    timeframe_1m_confirmed: bool
    timeframe_5m_confirmed: bool
    confluence_score: float  # 0-1, higher = more confluence
    recommended_action: str  # "BUY" or "SELL"


class IFVGDetector:
    """Detects Fair Value Gaps and Inverse FVG reversal entries.

    The IFVG is the core price action setup. Process:
    1. Scan for FVGs (3-candle gap patterns)
    2. Track FVG zones as they age
    3. Detect when price returns to fill the gap
    4. Confirm reversal (candle closes back outside the zone)
    5. Generate entry signal with target/stop from zone levels
    """

    # Minimum gap size as percentage of price to filter noise
    MIN_GAP_PCT = 0.0005  # 0.05% — filters sub-tick gaps
    # Maximum age of FVGs to track (in bars)
    MAX_FVG_AGE = 200
    # Reversal confirmation: candle must close this % back out of the zone
    REVERSAL_CONFIRMATION_PCT = 0.3  # 30% of zone must be reclaimed

    def __init__(self):
        self._active_fvgs: dict[str, list[FVG]] = {}  # symbol -> active FVGs

    def detect_fvgs(self, bars: list[Bar]) -> list[FVG]:
        """Detect all Fair Value Gaps in a sequence of bars.

        Scans through bars looking for 3-candle patterns where:
        - Bullish FVG: bar[i].high < bar[i+2].low (gap up between 1 and 3)
        - Bearish FVG: bar[i].low > bar[i+2].high (gap down between 1 and 3)

        Args:
            bars: OHLCV bars (1m, 5m, or any timeframe).

        Returns:
            List of FVG zones detected, newest first.
        """
        if len(bars) < 3:
            return []

        fvgs: list[FVG] = []

        for i in range(len(bars) - 2):
            candle1 = bars[i]
            candle2 = bars[i + 1]
            candle3 = bars[i + 2]

            # Bullish FVG: candle 1 high < candle 3 low
            # There's a gap between candle 1's high and candle 3's low
            if candle1.high < candle3.low:
                gap_size = candle3.low - candle1.high
                mid_price = (candle1.high + candle3.low) / 2
                gap_pct = gap_size / mid_price if mid_price > 0 else 0

                if gap_pct >= self.MIN_GAP_PCT:
                    fvg = FVG(
                        direction=FVGDirection.BULLISH,
                        zone_high=candle3.low,
                        zone_low=candle1.high,
                        zone_mid=(candle1.high + candle3.low) / 2,
                        candle1_idx=i,
                        candle3_idx=i + 2,
                        timestamp=candle2.timestamp,
                    )
                    fvgs.append(fvg)

            # Bearish FVG: candle 1 low > candle 3 high
            # There's a gap between candle 1's low and candle 3's high
            if candle1.low > candle3.high:
                gap_size = candle1.low - candle3.high
                mid_price = (candle1.low + candle3.high) / 2
                gap_pct = gap_size / mid_price if mid_price > 0 else 0

                if gap_pct >= self.MIN_GAP_PCT:
                    fvg = FVG(
                        direction=FVGDirection.BEARISH,
                        zone_high=candle1.low,
                        zone_low=candle3.high,
                        zone_mid=(candle1.low + candle3.high) / 2,
                        candle1_idx=i,
                        candle3_idx=i + 2,
                        timestamp=candle2.timestamp,
                    )
                    fvgs.append(fvg)

        logger.info(
            "fvgs_detected",
            count=len(fvgs),
            bullish=sum(1 for f in fvgs if f.direction == FVGDirection.BULLISH),
            bearish=sum(1 for f in fvgs if f.direction == FVGDirection.BEARISH),
            bar_count=len(bars),
        )
        return list(reversed(fvgs))  # Newest first

    def detect_ifvg_reversal(
        self, bars: list[Bar], fvgs: list[FVG]
    ) -> IFVGSignal | None:
        """Detect if the current price action is reversing off a FVG.

        Checks if the most recent bars are:
        1. Inside an FVG zone (price returned to fill the gap)
        2. Showing reversal (candle closing back outside the zone)

        For a bullish IFVG:
            - Price dropped into the bullish FVG zone
            - Current candle closes above the zone midpoint (bounce)
            - Entry = current close, target = zone high + extension, stop = zone low

        For a bearish IFVG:
            - Price rose into the bearish FVG zone
            - Current candle closes below the zone midpoint (rejection)
            - Entry = current close, target = zone low - extension, stop = zone high

        Args:
            bars: Recent bars including the current one.
            fvgs: Previously detected FVGs to check against.

        Returns:
            IFVGSignal if a reversal is detected, None otherwise.
        """
        if len(bars) < 2 or not fvgs:
            return None

        current_bar = bars[-1]
        prev_bar = bars[-2]
        current_close = current_bar.close
        current_low = current_bar.low
        current_high = current_bar.high

        for fvg in fvgs:
            if fvg.invalidated:
                continue

            zone_size = fvg.zone_high - fvg.zone_low
            if zone_size <= 0:
                continue

            # Check BULLISH IFVG
            if fvg.direction == FVGDirection.BULLISH:
                # Price must have dipped into the zone
                price_entered_zone = current_low <= fvg.zone_high and current_low >= fvg.zone_low

                # Or previous bar was in the zone
                if not price_entered_zone:
                    price_entered_zone = prev_bar.low <= fvg.zone_high and prev_bar.low >= fvg.zone_low

                if not price_entered_zone:
                    continue

                # Reversal confirmation: close above zone midpoint
                reclaim_pct = (current_close - fvg.zone_low) / zone_size if zone_size > 0 else 0
                if reclaim_pct < self.REVERSAL_CONFIRMATION_PCT:
                    continue

                # Check price didn't blow through (invalidation)
                if current_low < fvg.zone_low - zone_size * 0.5:
                    fvg.invalidated = True
                    continue

                fvg.filled = True

                # Calculate target and stop
                extension = zone_size * 1.5  # 1.5x the gap for target
                target = fvg.zone_high + extension
                stop = fvg.zone_low - zone_size * 0.25  # Small buffer below zone

                risk = current_close - stop
                reward = target - current_close
                rr = reward / risk if risk > 0 else 0

                # Confidence based on how clean the reversal is
                confidence = min(1.0, reclaim_pct * 0.5 + (0.3 if rr > 2 else 0))

                signal = IFVGSignal(
                    direction=FVGDirection.BULLISH,
                    fvg=fvg,
                    entry_price=current_close,
                    target_price=round(target, 2),
                    stop_price=round(stop, 2),
                    risk_reward=round(rr, 2),
                    timestamp=current_bar.timestamp,
                    confidence=round(confidence, 2),
                )

                logger.info(
                    "bullish_ifvg_detected",
                    entry=current_close,
                    target=target,
                    stop=stop,
                    rr=rr,
                    zone=f"{fvg.zone_low:.2f}-{fvg.zone_high:.2f}",
                )
                return signal

            # Check BEARISH IFVG
            elif fvg.direction == FVGDirection.BEARISH:
                # Price must have risen into the zone
                price_entered_zone = current_high >= fvg.zone_low and current_high <= fvg.zone_high

                if not price_entered_zone:
                    price_entered_zone = prev_bar.high >= fvg.zone_low and prev_bar.high <= fvg.zone_high

                if not price_entered_zone:
                    continue

                # Reversal confirmation: close below zone midpoint
                reject_pct = (fvg.zone_high - current_close) / zone_size if zone_size > 0 else 0
                if reject_pct < self.REVERSAL_CONFIRMATION_PCT:
                    continue

                # Invalidation check
                if current_high > fvg.zone_high + zone_size * 0.5:
                    fvg.invalidated = True
                    continue

                fvg.filled = True

                extension = zone_size * 1.5
                target = fvg.zone_low - extension
                stop = fvg.zone_high + zone_size * 0.25

                risk = stop - current_close
                reward = current_close - target
                rr = reward / risk if risk > 0 else 0

                confidence = min(1.0, reject_pct * 0.5 + (0.3 if rr > 2 else 0))

                signal = IFVGSignal(
                    direction=FVGDirection.BEARISH,
                    fvg=fvg,
                    entry_price=current_close,
                    target_price=round(target, 2),
                    stop_price=round(stop, 2),
                    risk_reward=round(rr, 2),
                    timestamp=current_bar.timestamp,
                    confidence=round(confidence, 2),
                )

                logger.info(
                    "bearish_ifvg_detected",
                    entry=current_close,
                    target=target,
                    stop=stop,
                    rr=rr,
                    zone=f"{fvg.zone_low:.2f}-{fvg.zone_high:.2f}",
                )
                return signal

        return None

    def get_ifvg_entry(
        self,
        symbol: str,
        bars_1m: list[Bar],
        bars_5m: list[Bar],
    ) -> IFVGEntry | None:
        """Generate an IFVG entry signal using multi-timeframe confluence.

        The strongest entries occur when both 1m and 5m timeframes show
        the same IFVG setup. This dramatically reduces false signals.

        Process:
        1. Detect FVGs on 5m bars (higher timeframe = stronger zones)
        2. Detect FVGs on 1m bars (lower timeframe = precise entries)
        3. Check for IFVG reversals on both timeframes
        4. Require at least one timeframe to confirm
        5. Score confluence and produce final entry

        Args:
            symbol: Ticker symbol.
            bars_1m: 1-minute bars (at least 50 for FVG detection).
            bars_5m: 5-minute bars (at least 50 for FVG detection).

        Returns:
            IFVGEntry if a valid setup is found, None otherwise.
        """
        if len(bars_1m) < 10 or len(bars_5m) < 10:
            logger.debug(
                "insufficient_bars_for_ifvg",
                symbol=symbol,
                bars_1m=len(bars_1m),
                bars_5m=len(bars_5m),
            )
            return None

        # Detect FVGs on both timeframes
        fvgs_5m = self.detect_fvgs(bars_5m)
        fvgs_1m = self.detect_fvgs(bars_1m)

        # Store active FVGs
        self._active_fvgs[symbol] = fvgs_5m + fvgs_1m

        # Check for reversals
        signal_5m = self.detect_ifvg_reversal(bars_5m, fvgs_5m)
        signal_1m = self.detect_ifvg_reversal(bars_1m, fvgs_1m)

        # Need at least one signal
        if signal_5m is None and signal_1m is None:
            return None

        # Determine primary signal (prefer 5m as it's stronger)
        primary_signal = signal_5m or signal_1m
        assert primary_signal is not None

        # Check directional alignment
        directions_agree = True
        if signal_5m and signal_1m:
            directions_agree = signal_5m.direction == signal_1m.direction

        if not directions_agree:
            logger.warning(
                "ifvg_timeframe_conflict",
                symbol=symbol,
                tf_1m=signal_1m.direction.value if signal_1m else None,
                tf_5m=signal_5m.direction.value if signal_5m else None,
            )
            return None  # Conflicting signals — no trade

        # Calculate confluence score
        confluence = 0.0
        tf_1m_confirmed = signal_1m is not None
        tf_5m_confirmed = signal_5m is not None

        if tf_1m_confirmed:
            confluence += 0.4
        if tf_5m_confirmed:
            confluence += 0.4  # 5m is stronger

        # Bonus for both confirming
        if tf_1m_confirmed and tf_5m_confirmed:
            confluence += 0.2

        # Risk/reward bonus
        if primary_signal.risk_reward >= 3.0:
            confluence = min(1.0, confluence + 0.1)
        elif primary_signal.risk_reward >= 2.0:
            confluence = min(1.0, confluence + 0.05)

        action = "BUY" if primary_signal.direction == FVGDirection.BULLISH else "SELL"

        entry = IFVGEntry(
            symbol=symbol,
            signal=primary_signal,
            timeframe_1m_confirmed=tf_1m_confirmed,
            timeframe_5m_confirmed=tf_5m_confirmed,
            confluence_score=round(confluence, 2),
            recommended_action=action,
        )

        logger.info(
            "ifvg_entry_generated",
            symbol=symbol,
            action=action,
            confluence=entry.confluence_score,
            entry_price=primary_signal.entry_price,
            target=primary_signal.target_price,
            stop=primary_signal.stop_price,
            rr=primary_signal.risk_reward,
            tf_1m=tf_1m_confirmed,
            tf_5m=tf_5m_confirmed,
        )
        return entry

    def get_active_fvgs(self, symbol: str) -> list[FVG]:
        """Get currently active (unfilled, non-invalidated) FVGs for a symbol.

        Args:
            symbol: Ticker symbol.

        Returns:
            List of active FVG zones.
        """
        fvgs = self._active_fvgs.get(symbol, [])
        return [f for f in fvgs if not f.filled and not f.invalidated]

    def cleanup_old_fvgs(self, symbol: str, current_bar_idx: int) -> int:
        """Remove old FVGs that are too far in the past to be relevant.

        Args:
            symbol: Ticker symbol.
            current_bar_idx: Current bar index for age comparison.

        Returns:
            Number of FVGs removed.
        """
        if symbol not in self._active_fvgs:
            return 0

        original_count = len(self._active_fvgs[symbol])
        self._active_fvgs[symbol] = [
            fvg for fvg in self._active_fvgs[symbol]
            if (current_bar_idx - fvg.candle3_idx) <= self.MAX_FVG_AGE
            and not fvg.invalidated
        ]
        removed = original_count - len(self._active_fvgs[symbol])

        if removed > 0:
            logger.debug("fvgs_cleaned", symbol=symbol, removed=removed)

        return removed


#===============================================================================
# SECTION: SIGNALS - Watchlist
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/signals/watchlist.py (295 lines)
#-------------------------------------------------------------------------------

"""LEAP Watchlist — Buy Zone Monitoring for Long-Term Swing Positions.

Tracks SuperLuckeee's 16-stock LEAP watchlist with exact buy zones.
When a stock enters its buy zone, it signals a LEAP entry opportunity.

The watchlist is the wealth-building layer:
    - Daily bread ICs fund the account → profits buy LEAPs
    - LEAPs held 3-12 months → 400-1800% returns (CVNA, GOOGL, AMD history)
    - "Bottom will be in as soon as WAR ends" — geopolitical catalyst timing

Historical context:
    - Average 1-year return from wartime bottoms = +32.6%
    - Market bottoms BEFORE war ends (Iraq: days before invasion)
    - "The market doesn't wait for peace — it moves ahead of it"

P/E valuation priority:
    - NVDA & AMZN = biggest P/E discounts (~67-68% below peak multiples)
    - MSFT at 46-50% discount = strong value
    - TSLA still expensive at 179 P/E = avoid for pure value
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class BuyZoneStatus(str, Enum):
    """Status of a stock relative to its buy zone."""

    ABOVE_ZONE = "ABOVE_ZONE"      # Not yet in buy zone
    ENTERING_ZONE = "ENTERING_ZONE"  # Within 5% of zone top
    IN_ZONE = "IN_ZONE"            # Inside buy zone — ALERT
    BELOW_ZONE = "BELOW_ZONE"      # Broke below buy zone — deeper value or breakdown


class WatchlistEntry(BaseModel):
    """A single stock on the LEAP watchlist with buy zone."""

    symbol: str
    peak_price: float       # Recent/all-time high
    current_price: float = 0.0
    decline_pct: float = 0.0  # % decline from peak
    buy_zone_low: float     # Bottom of buy zone
    buy_zone_high: float    # Top of buy zone
    thesis: str             # Why this level matters
    sector: str = ""
    forward_pe: float = 0.0
    peak_pe: float = 0.0
    pe_discount_pct: float = 0.0  # How far P/E has compressed from peak

    @property
    def status(self) -> BuyZoneStatus:
        if self.current_price <= 0:
            return BuyZoneStatus.ABOVE_ZONE
        if self.current_price < self.buy_zone_low:
            return BuyZoneStatus.BELOW_ZONE
        if self.buy_zone_low <= self.current_price <= self.buy_zone_high:
            return BuyZoneStatus.IN_ZONE
        # Check if within 5% of zone top
        approach_threshold = self.buy_zone_high * 1.05
        if self.current_price <= approach_threshold:
            return BuyZoneStatus.ENTERING_ZONE
        return BuyZoneStatus.ABOVE_ZONE

    @property
    def distance_to_zone_pct(self) -> float:
        """Percentage distance from current price to top of buy zone. Negative = in/below zone."""
        if self.current_price <= 0 or self.buy_zone_high <= 0:
            return 0.0
        return round(((self.current_price - self.buy_zone_high) / self.buy_zone_high) * 100, 2)


class WatchlistAlert(BaseModel):
    """Alert generated when a stock enters its buy zone."""

    symbol: str
    status: BuyZoneStatus
    current_price: float
    buy_zone: str  # e.g., "$155-165"
    thesis: str
    pe_info: str = ""


# ── The Watchlist ─────────────────────────────────────────────────

# SuperLuckeee's 16-stock LEAP watchlist — March/April 2026
# Source: "16 stocks I'm adding in March/April 2026 (bottom will be in as soon as WAR ends)"

LEAP_WATCHLIST: list[WatchlistEntry] = [
    # Tier 1 — Mega Cap Tech (LEAP core)
    WatchlistEntry(
        symbol="NVDA", peak_price=212.0, buy_zone_low=155.0, buy_zone_high=165.0,
        thesis="Prior breakout + big demand zone",
        sector="Semiconductors", forward_pe=20.29, peak_pe=63.5, pe_discount_pct=67.3,
    ),
    WatchlistEntry(
        symbol="TSLA", peak_price=499.0, buy_zone_low=340.0, buy_zone_high=350.0,
        thesis="Major institutional demand zone",
        sector="EV/Energy", forward_pe=179.04, peak_pe=300.0, pe_discount_pct=10.5,
    ),
    WatchlistEntry(
        symbol="AMZN", peak_price=259.0, buy_zone_low=195.0, buy_zone_high=200.0,
        thesis="Previous consolidation base",
        sector="Tech/Retail", forward_pe=25.57, peak_pe=90.0, pe_discount_pct=68.0,
    ),
    WatchlistEntry(
        symbol="META", peak_price=789.0, buy_zone_low=530.0, buy_zone_high=550.0,
        thesis="Long-term trendline support",
        sector="Social/AI", forward_pe=0.0, peak_pe=0.0, pe_discount_pct=41.0,
    ),
    WatchlistEntry(
        symbol="GOOG", peak_price=334.0, buy_zone_low=280.0, buy_zone_high=290.0,
        thesis="Multi-month support + 200DMA",
        sector="Tech/Search", forward_pe=23.90, peak_pe=32.5, pe_discount_pct=30.3,
    ),
    WatchlistEntry(
        symbol="AAPL", peak_price=289.0, buy_zone_low=225.0, buy_zone_high=235.0,
        thesis="Strong institutional accumulation area",
        sector="Tech/Consumer", forward_pe=29.25, peak_pe=32.5, pe_discount_pct=16.0,
    ),
    WatchlistEntry(
        symbol="MSFT", peak_price=0.0, buy_zone_low=0.0, buy_zone_high=0.0,
        thesis="Not on explicit buy list but tracked for P/E",
        sector="Tech/Cloud", forward_pe=18.91, peak_pe=36.5, pe_discount_pct=46.0,
    ),

    # Tier 2 — Growth / Healthcare
    WatchlistEntry(
        symbol="LLY", peak_price=1112.0, buy_zone_low=940.0, buy_zone_high=950.0,
        thesis="Healthcare trend support",
        sector="Healthcare/Pharma",
    ),
    WatchlistEntry(
        symbol="AVGO", peak_price=415.0, buy_zone_low=320.0, buy_zone_high=340.0,
        thesis="Previous consolidation range",
        sector="Semiconductors",
    ),
    WatchlistEntry(
        symbol="CRWD", peak_price=558.0, buy_zone_low=350.0, buy_zone_high=360.0,
        thesis="Cybersecurity sector support",
        sector="Cybersecurity",
    ),

    # Tier 3 — High Beta / Speculative
    WatchlistEntry(
        symbol="ASTS", peak_price=130.0, buy_zone_low=70.0, buy_zone_high=80.0,
        thesis="Last accumulation zone pre-rally",
        sector="Space/Telecom",
    ),
    WatchlistEntry(
        symbol="IONQ", peak_price=85.0, buy_zone_low=25.0, buy_zone_high=30.0,
        thesis="Early cycle support",
        sector="Quantum Computing",
    ),
    WatchlistEntry(
        symbol="SOFI", peak_price=32.0, buy_zone_low=15.0, buy_zone_high=17.0,
        thesis="Strong psychological demand",
        sector="Fintech",
    ),
    WatchlistEntry(
        symbol="AMD", peak_price=267.0, buy_zone_low=170.0, buy_zone_high=180.0,
        thesis="Semiconductor cycle support",
        sector="Semiconductors",
    ),
    WatchlistEntry(
        symbol="INTC", peak_price=62.0, buy_zone_low=35.0, buy_zone_high=38.0,
        thesis="Long-term value floor",
        sector="Semiconductors",
    ),
    WatchlistEntry(
        symbol="NKE", peak_price=179.0, buy_zone_low=50.0, buy_zone_high=55.0,
        thesis="Major historical support",
        sector="Consumer/Retail",
    ),
    WatchlistEntry(
        symbol="PLTR", peak_price=207.0, buy_zone_low=120.0, buy_zone_high=130.0,
        thesis="Prior breakout base",
        sector="AI/Defense",
    ),
]


class WatchlistMonitor:
    """Monitors LEAP watchlist stocks and generates buy zone alerts.

    Call scan() periodically with current prices to check if any stocks
    have entered their buy zones. Returns alerts for stocks in zone.
    """

    def __init__(self, watchlist: list[WatchlistEntry] | None = None):
        self._watchlist = watchlist or LEAP_WATCHLIST
        self._last_alerts: dict[str, BuyZoneStatus] = {}

    @property
    def watchlist(self) -> list[WatchlistEntry]:
        return self._watchlist

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for all watchlist entries.

        Args:
            prices: Dict of {symbol: current_price}.
        """
        for entry in self._watchlist:
            if entry.symbol in prices:
                entry.current_price = prices[entry.symbol]
                if entry.peak_price > 0:
                    entry.decline_pct = round(
                        ((entry.current_price - entry.peak_price) / entry.peak_price) * 100, 1
                    )

    def scan(self, prices: dict[str, float] | None = None) -> list[WatchlistAlert]:
        """Scan watchlist for buy zone entries.

        Args:
            prices: Optional fresh prices to update before scanning.

        Returns:
            List of alerts for stocks in or entering buy zones.
        """
        if prices:
            self.update_prices(prices)

        alerts: list[WatchlistAlert] = []

        for entry in self._watchlist:
            if entry.current_price <= 0 or entry.buy_zone_high <= 0:
                continue

            status = entry.status
            prev_status = self._last_alerts.get(entry.symbol)

            # Alert on new zone entries or status changes
            if status in (BuyZoneStatus.IN_ZONE, BuyZoneStatus.ENTERING_ZONE, BuyZoneStatus.BELOW_ZONE):
                # Only alert if status changed or first time
                if status != prev_status:
                    pe_info = ""
                    if entry.forward_pe > 0:
                        pe_info = f"Fwd P/E: {entry.forward_pe:.1f} (peak: {entry.peak_pe:.0f}, -{entry.pe_discount_pct:.0f}% from peak)"

                    alert = WatchlistAlert(
                        symbol=entry.symbol,
                        status=status,
                        current_price=entry.current_price,
                        buy_zone=f"${entry.buy_zone_low:.0f}-${entry.buy_zone_high:.0f}",
                        thesis=entry.thesis,
                        pe_info=pe_info,
                    )
                    alerts.append(alert)

                    logger.warning(
                        "watchlist_alert",
                        symbol=entry.symbol,
                        status=status.value,
                        price=entry.current_price,
                        zone=f"${entry.buy_zone_low}-${entry.buy_zone_high}",
                        thesis=entry.thesis,
                    )

            self._last_alerts[entry.symbol] = status

        return alerts

    def get_in_zone(self) -> list[WatchlistEntry]:
        """Get all stocks currently inside their buy zone."""
        return [e for e in self._watchlist if e.status == BuyZoneStatus.IN_ZONE]

    def get_approaching(self) -> list[WatchlistEntry]:
        """Get all stocks approaching their buy zone (within 5%)."""
        return [e for e in self._watchlist if e.status == BuyZoneStatus.ENTERING_ZONE]

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the watchlist status."""
        in_zone = self.get_in_zone()
        approaching = self.get_approaching()

        return {
            "total_tracked": len(self._watchlist),
            "in_buy_zone": len(in_zone),
            "approaching": len(approaching),
            "in_zone_symbols": [e.symbol for e in in_zone],
            "approaching_symbols": [e.symbol for e in approaching],
            "best_pe_value": sorted(
                [e for e in self._watchlist if e.pe_discount_pct > 0],
                key=lambda e: e.pe_discount_pct,
                reverse=True,
            )[:3],
        }


#===============================================================================
# SECTION: EXECUTION - Pillar Executor
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/execution/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: esther/execution/pillars.py (1238 lines)
#-------------------------------------------------------------------------------

"""4 Pillars Execution — Strategy-Specific Option Order Construction and Submission.

Each pillar handles a different market condition:

    P1: Iron Condors — Neutral. Sell OTM put spread + OTM call spread.
        Short strikes at 0.16 delta, 10-point wings.

    P2: Bear Call Spreads — Bearish. Sell OTM call, buy further OTM call.
        Short strike at 0.25 delta, 10-point wings.

    P3: Bull Put Spreads — Bullish. Sell OTM put, buy further OTM put.
        Short strike at 0.25 delta, 10-point wings.

    P4: 0DTE Directional Scalps — High conviction. Buy ATM-ish options.
        Calls for bull, puts for bear. Trailing stop managed by PositionManager.
        Power Hour mode (3:00-3:45 PM): 0.40-0.45 delta momentum scalps.

    IC Ladder: Staggered iron condors at multiple strike levels with bias-weighted sizing.

    Multi-pillar execution: P2 + P3 + P4 can run simultaneously on the same ticker.

    Expire worthless mode: Spreads >80% OTM with <15 min to close skip buy-back.

    Pyramid/scale-in: Add to winning positions only.

Each pillar follows the same interface:
    1. find_strikes(chain, target_delta) → select optimal strikes from chain
    2. build_order(strikes, quantity) → construct the order legs
    3. submit_order(order) → send to Tradier API
"""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel

from esther.core.config import config
from esther.data.tradier import OptionQuote, OptionType, TradierClient
from esther.data.alpaca import AlpacaClient

logger = structlog.get_logger(__name__)


class OrderSide(str, Enum):
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_OPEN = "sell_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_CLOSE = "sell_to_close"


class OrderLeg(BaseModel):
    """Single leg of an option order."""

    option_symbol: str
    side: OrderSide
    quantity: int
    strike: float
    option_type: OptionType
    delta: float = 0.0


class SpreadOrder(BaseModel):
    """Complete spread order ready for submission."""

    symbol: str  # underlying
    pillar: int
    legs: list[OrderLeg]
    order_type: str = "credit"  # credit, debit, market
    net_price: float = 0.0  # net credit or debit
    max_loss: float = 0.0  # max possible loss per contract
    max_profit: float = 0.0  # max possible profit per contract
    quantity: int = 1
    expiration: str = ""
    created_at: datetime = datetime.now()
    rung_label: str = ""  # For IC ladder: "rung_1", "rung_2", "rung_3"
    expire_worthless_eligible: bool = False  # Flag for expire-worthless mode
    time_in_force: str = "day"  # "day" or "gtc" (Good Till Cancel)


class ICLadderOrder(BaseModel):
    """A complete IC Ladder consisting of 2-3 staggered iron condors."""

    symbol: str
    rungs: list[SpreadOrder]
    total_credit: float = 0.0
    total_max_loss: float = 0.0
    total_contracts: int = 0
    bias_direction: str = "NEUTRAL"  # BULL, BEAR, NEUTRAL
    created_at: datetime = datetime.now()


class StrikeSelection(BaseModel):
    """Selected strikes for a spread."""

    short_strike: OptionQuote
    long_strike: OptionQuote
    short_delta: float
    wing_width: float


class MultiPillarResult(BaseModel):
    """Result of executing multiple pillars on the same ticker."""

    symbol: str
    orders: list[SpreadOrder] = []
    pillars_executed: list[int] = []
    total_risk: float = 0.0
    total_credit: float = 0.0


def find_closest_delta(
    chain: list[OptionQuote],
    target_delta: float,
    option_type: OptionType,
) -> OptionQuote | None:
    """Find the option closest to a target delta in the chain.

    Args:
        chain: Full option chain.
        target_delta: Target absolute delta (e.g., 0.16).
        option_type: CALL or PUT.

    Returns:
        The OptionQuote closest to the target delta, or None.
    """
    candidates = [
        opt for opt in chain
        if opt.option_type == option_type
        and opt.greeks is not None
        and opt.bid > 0  # Must have a bid
    ]

    if not candidates:
        return None

    # Sort by distance from target delta
    return min(
        candidates,
        key=lambda o: abs(abs(o.greeks.delta) - target_delta) if o.greeks else float("inf"),
    )


def find_wing(
    chain: list[OptionQuote],
    short_strike: float,
    wing_width: float,
    option_type: OptionType,
    direction: str = "otm",
) -> OptionQuote | None:
    """Find the long wing strike at a fixed width from the short strike.

    For credit spreads, the long wing is further OTM:
    - Bull put: long strike = short strike - wing_width
    - Bear call: long strike = short strike + wing_width

    Args:
        chain: Full option chain.
        short_strike: The short strike price.
        wing_width: Points between short and long strikes (now 10 points).
        option_type: CALL or PUT.
        direction: "otm" for further OTM (credit spreads).

    Returns:
        The OptionQuote for the wing, or None.
    """
    if option_type == OptionType.PUT:
        target_strike = short_strike - wing_width  # further OTM for puts
    else:
        target_strike = short_strike + wing_width  # further OTM for calls

    candidates = [
        opt for opt in chain
        if opt.option_type == option_type
        and abs(opt.strike - target_strike) < 0.5  # within $0.50 of target
    ]

    if not candidates:
        # Try closest available
        candidates = [
            opt for opt in chain if opt.option_type == option_type
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda o: abs(o.strike - target_strike))

    return candidates[0]


def _is_power_hour() -> bool:
    """Check if we're in power hour (3:00-3:45 PM ET)."""
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return time(15, 0) <= now_et.time() <= time(15, 45)


def _minutes_to_close() -> float:
    """Calculate minutes until market close (4:00 PM ET)."""
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    close_dt = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = (close_dt - now_et).total_seconds() / 60
    return max(0, delta)


def check_expire_worthless(
    spread_value: float,
    entry_credit: float,
    short_strike: float,
    current_price: float,
    option_type: OptionType,
    wing_width: float = 10.0,
) -> bool:
    """Check if a spread qualifies for expire worthless mode.

    A spread qualifies if:
    1. It's >80% OTM (short strike is far from current price relative to wing)
    2. There's <15 minutes to close
    3. The spread is profitable (trading below entry credit)

    This saves a day trade and captures max profit from time decay.

    Args:
        spread_value: Current value of the spread.
        entry_credit: Original credit received.
        short_strike: The short strike price.
        current_price: Current underlying price.
        option_type: PUT or CALL (which side of the spread).
        wing_width: Width of the spread in points.

    Returns:
        True if the spread should be left to expire worthless.
    """
    minutes_left = _minutes_to_close()
    if minutes_left > 15:
        return False

    # Calculate how far OTM the short strike is
    if option_type == OptionType.PUT:
        # Put spread: short strike is OTM if below current price
        distance_otm = current_price - short_strike
    else:
        # Call spread: short strike is OTM if above current price
        distance_otm = short_strike - current_price

    # Need to be significantly OTM — at least 80% of wing width away
    otm_pct = distance_otm / wing_width if wing_width > 0 else 0

    if otm_pct < 0.80:
        return False

    # Spread must be trading at less than 20% of entry credit (i.e., >80% profit)
    if entry_credit > 0 and spread_value / entry_credit > 0.20:
        return False

    logger.info(
        "expire_worthless_eligible",
        short_strike=short_strike,
        current_price=current_price,
        otm_pct=f"{otm_pct:.1%}",
        minutes_left=f"{minutes_left:.0f}",
        spread_value=spread_value,
        entry_credit=entry_credit,
    )
    return True


class PillarExecutor:
    """Constructs and submits orders for all four pillars.

    Updated features:
    - 10-point wing widths (was 5)
    - 75% profit target (was 50%)
    - Expire worthless mode for near-expiry OTM spreads
    - IC Ladder execution with bias-weighted sizing
    - Power hour mode for P4 (3:00-3:45 PM)
    - Multi-pillar execution on same ticker
    - Pyramid/scale-in for winning positions
    """

    def __init__(self, client: TradierClient | AlpacaClient):
        self.client = client
        self._cfg = config().pillars

    # ── Pillar 1: Iron Condors ───────────────────────────────────

    async def build_iron_condor(
        self,
        symbol: str,
        chain: list[OptionQuote],
        quantity: int = 1,
        expiration: str = "",
    ) -> SpreadOrder | None:
        """Build an iron condor: sell OTM put spread + sell OTM call spread.

        The iron condor profits from time decay when the underlying stays
        between the short strikes. Max profit = total credit received.
        Max loss = wing width - credit.

        Uses 10-point wings and 75% profit target.

        Args:
            symbol: Underlying symbol.
            chain: Full option chain for the expiration.
            quantity: Number of iron condors.
            expiration: Expiration date string.

        Returns:
            SpreadOrder ready for submission, or None if strikes can't be found.
        """
        cfg = self._cfg.p1
        wing_width = 10  # 10-point wings (was 5)

        # Find short put (OTM, target delta)
        short_put = find_closest_delta(chain, cfg.short_delta, OptionType.PUT)
        if not short_put:
            logger.warning("ic_no_short_put", symbol=symbol)
            return None

        # Find long put (further OTM) — 10 points wide
        long_put = find_wing(chain, short_put.strike, wing_width, OptionType.PUT)
        if not long_put:
            logger.warning("ic_no_long_put", symbol=symbol)
            return None

        # Find short call (OTM, target delta)
        short_call = find_closest_delta(chain, cfg.short_delta, OptionType.CALL)
        if not short_call:
            logger.warning("ic_no_short_call", symbol=symbol)
            return None

        # Find long call (further OTM) — 10 points wide
        long_call = find_wing(chain, short_call.strike, wing_width, OptionType.CALL)
        if not long_call:
            logger.warning("ic_no_long_call", symbol=symbol)
            return None

        # Calculate credit and risk
        put_credit = short_put.mid - long_put.mid
        call_credit = short_call.mid - long_call.mid
        total_credit = put_credit + call_credit
        max_loss = wing_width - total_credit  # 10 - credit

        if total_credit <= 0:
            logger.warning("ic_negative_credit", symbol=symbol, credit=total_credit)
            return None

        legs = [
            OrderLeg(
                option_symbol=short_put.symbol, side=OrderSide.SELL_TO_OPEN,
                quantity=quantity, strike=short_put.strike, option_type=OptionType.PUT,
                delta=short_put.greeks.delta if short_put.greeks else 0,
            ),
            OrderLeg(
                option_symbol=long_put.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=long_put.strike, option_type=OptionType.PUT,
            ),
            OrderLeg(
                option_symbol=short_call.symbol, side=OrderSide.SELL_TO_OPEN,
                quantity=quantity, strike=short_call.strike, option_type=OptionType.CALL,
                delta=short_call.greeks.delta if short_call.greeks else 0,
            ),
            OrderLeg(
                option_symbol=long_call.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=long_call.strike, option_type=OptionType.CALL,
            ),
        ]

        order = SpreadOrder(
            symbol=symbol, pillar=1, legs=legs, order_type="credit",
            net_price=round(total_credit, 2), max_loss=round(max_loss * 100, 2),
            max_profit=round(total_credit * 100, 2), quantity=quantity,
            expiration=expiration,
        )

        logger.info(
            "iron_condor_built", symbol=symbol,
            put_spread=f"{short_put.strike}/{long_put.strike}",
            call_spread=f"{short_call.strike}/{long_call.strike}",
            credit=total_credit, max_loss=max_loss,
            wing_width=wing_width,
        )
        return order

    # ── Pillar 2: Bear Call Spreads ──────────────────────────────

    async def build_bear_call(
        self,
        symbol: str,
        chain: list[OptionQuote],
        quantity: int = 1,
        expiration: str = "",
    ) -> SpreadOrder | None:
        """Build a bear call spread: sell OTM call + buy further OTM call.

        Profits when the underlying stays below the short call strike.
        Bearish strategy — sells call premium expecting the price to stay down.

        Uses 10-point wings and 75% profit target.
        """
        cfg = self._cfg.p2
        wing_width = 10  # 10-point wings (was 5)

        short_call = find_closest_delta(chain, cfg.short_delta, OptionType.CALL)
        if not short_call:
            logger.warning("bear_call_no_short", symbol=symbol)
            return None

        long_call = find_wing(chain, short_call.strike, wing_width, OptionType.CALL)
        if not long_call:
            logger.warning("bear_call_no_long", symbol=symbol)
            return None

        credit = short_call.mid - long_call.mid
        max_loss = wing_width - credit

        if credit <= 0:
            logger.warning("bear_call_negative_credit", symbol=symbol)
            return None

        legs = [
            OrderLeg(
                option_symbol=short_call.symbol, side=OrderSide.SELL_TO_OPEN,
                quantity=quantity, strike=short_call.strike, option_type=OptionType.CALL,
                delta=short_call.greeks.delta if short_call.greeks else 0,
            ),
            OrderLeg(
                option_symbol=long_call.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=long_call.strike, option_type=OptionType.CALL,
            ),
        ]

        order = SpreadOrder(
            symbol=symbol, pillar=2, legs=legs, order_type="credit",
            net_price=round(credit, 2), max_loss=round(max_loss * 100, 2),
            max_profit=round(credit * 100, 2), quantity=quantity,
            expiration=expiration,
        )

        logger.info(
            "bear_call_built", symbol=symbol,
            spread=f"{short_call.strike}/{long_call.strike}",
            credit=credit, wing_width=wing_width,
        )
        return order

    # ── Pillar 3: Bull Put Spreads ───────────────────────────────

    async def build_bull_put(
        self,
        symbol: str,
        chain: list[OptionQuote],
        quantity: int = 1,
        expiration: str = "",
    ) -> SpreadOrder | None:
        """Build a bull put spread: sell OTM put + buy further OTM put.

        Profits when the underlying stays above the short put strike.
        Bullish strategy — sells put premium expecting the price to stay up.

        Uses 10-point wings and 75% profit target.
        """
        cfg = self._cfg.p3
        wing_width = 10  # 10-point wings (was 5)

        short_put = find_closest_delta(chain, cfg.short_delta, OptionType.PUT)
        if not short_put:
            logger.warning("bull_put_no_short", symbol=symbol)
            return None

        long_put = find_wing(chain, short_put.strike, wing_width, OptionType.PUT)
        if not long_put:
            logger.warning("bull_put_no_long", symbol=symbol)
            return None

        credit = short_put.mid - long_put.mid
        max_loss = wing_width - credit

        if credit <= 0:
            logger.warning("bull_put_negative_credit", symbol=symbol)
            return None

        legs = [
            OrderLeg(
                option_symbol=short_put.symbol, side=OrderSide.SELL_TO_OPEN,
                quantity=quantity, strike=short_put.strike, option_type=OptionType.PUT,
                delta=short_put.greeks.delta if short_put.greeks else 0,
            ),
            OrderLeg(
                option_symbol=long_put.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=long_put.strike, option_type=OptionType.PUT,
            ),
        ]

        order = SpreadOrder(
            symbol=symbol, pillar=3, legs=legs, order_type="credit",
            net_price=round(credit, 2), max_loss=round(max_loss * 100, 2),
            max_profit=round(credit * 100, 2), quantity=quantity,
            expiration=expiration,
        )

        logger.info(
            "bull_put_built", symbol=symbol,
            spread=f"{short_put.strike}/{long_put.strike}",
            credit=credit, wing_width=wing_width,
        )
        return order

    # ── Pillar 4: 0DTE Directional Scalps ────────────────────────

    async def build_directional_scalp(
        self,
        symbol: str,
        chain: list[OptionQuote],
        direction: str,  # "BULL" or "BEAR"
        quantity: int = 1,
        expiration: str = "",
    ) -> SpreadOrder | None:
        """Build a directional scalp: buy ATM-ish call (bull) or put (bear).

        This is the only debit strategy. We're buying premium and relying on
        the trailing stop (managed by PositionManager) for risk management.

        Normal mode: Target delta 0.40-0.55 (ATM-ish, enough delta to capture the move).
        Power Hour mode (3:00-3:45 PM): Target delta 0.40-0.45 for momentum scalps
        with wider stops and faster targets.
        """
        cfg = self._cfg.p4

        # Power hour mode: use tighter delta range for momentum scalps
        if _is_power_hour():
            target_delta = 0.425  # Midpoint of 0.40-0.45 range
            logger.info(
                "power_hour_scalp",
                symbol=symbol,
                direction=direction,
                delta_target=target_delta,
            )
        else:
            target_delta = (cfg.delta_range[0] + cfg.delta_range[1]) / 2

        opt_type = OptionType.CALL if direction == "BULL" else OptionType.PUT

        option = find_closest_delta(chain, target_delta, opt_type)
        if not option:
            logger.warning("scalp_no_option", symbol=symbol, direction=direction)
            return None

        if option.ask <= 0:
            logger.warning("scalp_no_ask", symbol=symbol)
            return None

        debit = option.ask  # We pay the ask to get in
        max_loss = debit  # Max loss on a long option = premium paid

        legs = [
            OrderLeg(
                option_symbol=option.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=option.strike, option_type=opt_type,
                delta=option.greeks.delta if option.greeks else 0,
            ),
        ]

        order = SpreadOrder(
            symbol=symbol, pillar=4, legs=legs, order_type="debit",
            net_price=round(debit, 2), max_loss=round(max_loss * 100, 2),
            max_profit=0.0,  # Unlimited for long options
            quantity=quantity, expiration=expiration,
        )

        mode = "POWER_HOUR" if _is_power_hour() else "NORMAL"
        logger.info(
            "scalp_built", symbol=symbol, direction=direction,
            strike=option.strike, debit=debit,
            delta=option.greeks.delta if option.greeks else "?",
            mode=mode,
        )
        return order

    # ── Pillar 5: Butterfly Spreads ────────────────────────────────

    async def build_butterfly(
        self,
        symbol: str,
        chain: list[OptionQuote],
        direction: str,  # "BULL" or "BEAR"
        current_price: float,
        quantity: int = 1,
        expiration: str = "",
    ) -> SpreadOrder | None:
        """Build a butterfly spread: Buy 1 lower, Sell 2 middle, Buy 1 upper.

        Butterfly spreads are debit strategies with very defined risk (max loss = debit paid).
        Ideal for small accounts ($1K-$5K) due to low capital requirements.

        BEAR butterfly: uses puts (expecting price to settle at middle strike)
        BULL butterfly: uses calls (expecting price to settle at middle strike)

        Middle strike = ATM (closest to current price)
        Wing width: 5 points for ETFs (SPY/QQQ), 10 points for indices (SPX)

        Args:
            symbol: Underlying symbol.
            chain: Full option chain for the expiration.
            direction: "BULL" for call butterfly, "BEAR" for put butterfly.
            current_price: Current underlying price for ATM determination.
            quantity: Number of butterfly spreads.
            expiration: Expiration date string.

        Returns:
            SpreadOrder ready for submission, or None if strikes can't be found.
        """
        cfg = self._cfg.p5

        # Determine wing width based on symbol
        index_symbols = {"SPX", "SPXW", "XSP", "$SPX"}
        if symbol.upper() in index_symbols:
            wing_width = cfg.wing_width_index
        else:
            wing_width = cfg.wing_width_etf

        # Determine option type based on direction
        opt_type = OptionType.CALL if direction == "BULL" else OptionType.PUT

        # Find ATM strike (closest to current price)
        atm_candidates = [
            opt for opt in chain
            if opt.option_type == opt_type and opt.bid > 0
        ]
        if not atm_candidates:
            logger.warning("butterfly_no_atm", symbol=symbol, direction=direction)
            return None

        middle_option = min(atm_candidates, key=lambda o: abs(o.strike - current_price))
        middle_strike = middle_option.strike

        # Find lower and upper wing strikes
        lower_strike = middle_strike - wing_width
        upper_strike = middle_strike + wing_width

        # Find the actual options at those strikes
        lower_candidates = [
            opt for opt in chain
            if opt.option_type == opt_type and abs(opt.strike - lower_strike) < 1.0
        ]
        upper_candidates = [
            opt for opt in chain
            if opt.option_type == opt_type and abs(opt.strike - upper_strike) < 1.0
        ]

        if not lower_candidates:
            logger.warning("butterfly_no_lower_wing", symbol=symbol, target_strike=lower_strike)
            return None
        if not upper_candidates:
            logger.warning("butterfly_no_upper_wing", symbol=symbol, target_strike=upper_strike)
            return None

        lower_option = min(lower_candidates, key=lambda o: abs(o.strike - lower_strike))
        upper_option = min(upper_candidates, key=lambda o: abs(o.strike - upper_strike))

        # Calculate debit: Buy 1 lower + Buy 1 upper - Sell 2 middle
        debit = (lower_option.ask + upper_option.ask) - (2 * middle_option.bid)

        if debit <= 0:
            logger.warning("butterfly_no_debit", symbol=symbol, debit=debit)
            return None

        # Max profit = wing_width - debit (at expiration, if price = middle strike)
        max_profit = wing_width - debit
        # Max loss = debit paid
        max_loss = debit

        legs = [
            OrderLeg(
                option_symbol=lower_option.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=lower_option.strike, option_type=opt_type,
                delta=lower_option.greeks.delta if lower_option.greeks else 0,
            ),
            OrderLeg(
                option_symbol=middle_option.symbol, side=OrderSide.SELL_TO_OPEN,
                quantity=quantity * 2, strike=middle_option.strike, option_type=opt_type,
                delta=middle_option.greeks.delta if middle_option.greeks else 0,
            ),
            OrderLeg(
                option_symbol=upper_option.symbol, side=OrderSide.BUY_TO_OPEN,
                quantity=quantity, strike=upper_option.strike, option_type=opt_type,
                delta=upper_option.greeks.delta if upper_option.greeks else 0,
            ),
        ]

        order = SpreadOrder(
            symbol=symbol, pillar=5, legs=legs, order_type="debit",
            net_price=round(debit, 2), max_loss=round(max_loss * 100, 2),
            max_profit=round(max_profit * 100, 2), quantity=quantity,
            expiration=expiration,
        )

        logger.info(
            "butterfly_built", symbol=symbol, direction=direction,
            lower=lower_option.strike, middle=middle_option.strike,
            upper=upper_option.strike, debit=round(debit, 2),
            max_profit=round(max_profit, 2), wing_width=wing_width,
        )
        return order

    # ── IC Ladder Execution ──────────────────────────────────────

    async def build_ic_ladder(
        self,
        symbol: str,
        chain: list[OptionQuote],
        current_price: float,
        bias_direction: str = "NEUTRAL",
        expiration: str = "",
    ) -> ICLadderOrder | None:
        """Build an IC Ladder: 2-3 iron condors at staggered strikes.

        The IC Ladder creates multiple iron condors at different distances from
        ATM, with sizing that increases as you go further OTM (lower risk per rung).

        Bias-weighted sizing:
        - BULLISH: Put side gets more contracts (price unlikely to drop)
        - BEARISH: Call side gets more contracts (price unlikely to rise)
        - NEUTRAL: Balanced sizing on both sides

        Rung structure:
        - Rung 1: Closest to ATM, smallest size (40 contracts) — highest premium, highest risk
        - Rung 2: Further OTM, medium size (60 contracts) — moderate premium/risk
        - Rung 3: Furthest OTM, largest size (100 contracts) — lowest premium, lowest risk

        Args:
            symbol: Underlying symbol.
            chain: Full option chain.
            current_price: Current underlying price for strike staggering.
            bias_direction: "BULL", "BEAR", or "NEUTRAL" for sizing weighting.
            expiration: Expiration date string.

        Returns:
            ICLadderOrder with all rungs, or None if construction fails.
        """
        wing_width = 10  # 10-point wings

        # Define rung configurations: (delta_offset, put_contracts, call_contracts)
        # Delta offsets: rung 1 is closest to ATM, rung 3 is furthest OTM
        rung_configs = self._get_ladder_rung_configs(bias_direction)

        rungs: list[SpreadOrder] = []
        total_credit = 0.0
        total_max_loss = 0.0
        total_contracts = 0

        for rung_idx, (put_delta, call_delta, put_qty, call_qty) in enumerate(rung_configs, 1):
            rung_label = f"rung_{rung_idx}"

            # Find put side strikes
            short_put = find_closest_delta(chain, put_delta, OptionType.PUT)
            if not short_put:
                logger.warning("ic_ladder_no_short_put", symbol=symbol, rung=rung_idx)
                continue

            long_put = find_wing(chain, short_put.strike, wing_width, OptionType.PUT)
            if not long_put:
                logger.warning("ic_ladder_no_long_put", symbol=symbol, rung=rung_idx)
                continue

            # Find call side strikes
            short_call = find_closest_delta(chain, call_delta, OptionType.CALL)
            if not short_call:
                logger.warning("ic_ladder_no_short_call", symbol=symbol, rung=rung_idx)
                continue

            long_call = find_wing(chain, short_call.strike, wing_width, OptionType.CALL)
            if not long_call:
                logger.warning("ic_ladder_no_long_call", symbol=symbol, rung=rung_idx)
                continue

            # Calculate credit for this rung
            put_credit = short_put.mid - long_put.mid
            call_credit = short_call.mid - long_call.mid

            # Build legs — put side and call side may have different quantities
            # Use the max of put_qty and call_qty for the order, adjust per leg
            rung_qty = max(put_qty, call_qty)
            rung_credit = (put_credit * put_qty + call_credit * call_qty) / rung_qty if rung_qty > 0 else 0
            rung_max_loss = wing_width - min(put_credit, call_credit)

            if rung_credit <= 0:
                logger.warning("ic_ladder_negative_credit", symbol=symbol, rung=rung_idx)
                continue

            legs = [
                OrderLeg(
                    option_symbol=short_put.symbol, side=OrderSide.SELL_TO_OPEN,
                    quantity=put_qty, strike=short_put.strike, option_type=OptionType.PUT,
                    delta=short_put.greeks.delta if short_put.greeks else 0,
                ),
                OrderLeg(
                    option_symbol=long_put.symbol, side=OrderSide.BUY_TO_OPEN,
                    quantity=put_qty, strike=long_put.strike, option_type=OptionType.PUT,
                ),
                OrderLeg(
                    option_symbol=short_call.symbol, side=OrderSide.SELL_TO_OPEN,
                    quantity=call_qty, strike=short_call.strike, option_type=OptionType.CALL,
                    delta=short_call.greeks.delta if short_call.greeks else 0,
                ),
                OrderLeg(
                    option_symbol=long_call.symbol, side=OrderSide.BUY_TO_OPEN,
                    quantity=call_qty, strike=long_call.strike, option_type=OptionType.CALL,
                ),
            ]

            rung_order = SpreadOrder(
                symbol=symbol, pillar=1, legs=legs, order_type="credit",
                net_price=round(rung_credit, 2),
                max_loss=round(rung_max_loss * 100 * rung_qty, 2),
                max_profit=round(rung_credit * 100 * rung_qty, 2),
                quantity=rung_qty,
                expiration=expiration,
                rung_label=rung_label,
            )

            rungs.append(rung_order)
            total_credit += rung_credit * rung_qty
            total_max_loss += rung_max_loss * 100 * rung_qty
            total_contracts += put_qty + call_qty

            logger.info(
                "ic_ladder_rung_built",
                symbol=symbol,
                rung=rung_idx,
                put_spread=f"{short_put.strike}/{long_put.strike}",
                call_spread=f"{short_call.strike}/{long_call.strike}",
                put_qty=put_qty,
                call_qty=call_qty,
                credit=rung_credit,
            )

        if not rungs:
            logger.warning("ic_ladder_no_rungs", symbol=symbol)
            return None

        ladder = ICLadderOrder(
            symbol=symbol,
            rungs=rungs,
            total_credit=round(total_credit, 2),
            total_max_loss=round(total_max_loss, 2),
            total_contracts=total_contracts,
            bias_direction=bias_direction,
        )

        logger.info(
            "ic_ladder_built",
            symbol=symbol,
            rungs=len(rungs),
            total_credit=ladder.total_credit,
            total_max_loss=ladder.total_max_loss,
            total_contracts=total_contracts,
            bias=bias_direction,
        )
        return ladder

    def _get_ladder_rung_configs(
        self, bias_direction: str
    ) -> list[tuple[float, float, int, int]]:
        """Get rung configurations based on bias direction.

        Each rung is: (put_delta, call_delta, put_contracts, call_contracts)

        BULLISH bias: More contracts on put side (price unlikely to drop, so sell more puts).
        BEARISH bias: More contracts on call side (price unlikely to rise, so sell more calls).
        NEUTRAL: Balanced.

        Returns:
            List of (put_delta, call_delta, put_qty, call_qty) tuples.
        """
        if bias_direction == "BULL":
            # Bullish: load up on puts (they'll expire worthless if price stays up)
            return [
                (0.16, 0.16, 50, 30),    # Rung 1: Closest ATM, smallest total
                (0.12, 0.12, 70, 50),    # Rung 2: Further OTM, medium
                (0.08, 0.08, 100, 60),   # Rung 3: Furthest OTM, largest on put side
            ]
        elif bias_direction == "BEAR":
            # Bearish: load up on calls (they'll expire worthless if price stays down)
            return [
                (0.16, 0.16, 30, 50),    # Rung 1: More calls
                (0.12, 0.12, 50, 70),    # Rung 2: More calls
                (0.08, 0.08, 60, 100),   # Rung 3: Most calls at furthest OTM
            ]
        else:
            # Neutral: balanced sizing
            return [
                (0.16, 0.16, 40, 40),    # Rung 1: Balanced, smallest
                (0.12, 0.12, 60, 60),    # Rung 2: Balanced, medium
                (0.08, 0.08, 100, 100),  # Rung 3: Balanced, largest
            ]

    # ── Multi-Pillar Execution ───────────────────────────────────

    async def execute_multi_pillar(
        self,
        symbol: str,
        chain: list[OptionQuote],
        eligible_pillars: list[int],
        direction: str,
        quantities: dict[int, int] | None = None,
        expiration: str = "",
    ) -> MultiPillarResult:
        """Execute multiple pillars simultaneously on the same ticker.

        Unlike the old approach that exits after the first pillar match,
        this runs ALL eligible pillars (e.g., P2 + P3 + P4 at the same time).

        This enables strategies like:
        - P2 (bear call) + P3 (bull put) = synthetic iron condor with independent legs
        - P3 (bull put) + P4 (bull scalp) = income + directional upside
        - P2 + P3 + P4 = full multi-strategy coverage

        Args:
            symbol: Underlying symbol.
            chain: Full option chain.
            eligible_pillars: List of pillar numbers to execute (e.g., [2, 3, 4]).
            direction: Overall market direction ("BULL", "BEAR", "NEUTRAL").
            quantities: Optional per-pillar quantity overrides {pillar: qty}.
            expiration: Expiration date string.

        Returns:
            MultiPillarResult with all executed orders and aggregate risk.
        """
        result = MultiPillarResult(symbol=symbol)
        default_qty = 1

        for pillar in eligible_pillars:
            qty = (quantities or {}).get(pillar, default_qty)

            try:
                order = await self._build_for_pillar(
                    symbol=symbol,
                    pillar=pillar,
                    direction=direction,
                    chain=chain,
                    quantity=qty,
                    expiration=expiration,
                )

                if order:
                    result.orders.append(order)
                    result.pillars_executed.append(pillar)
                    result.total_risk += order.max_loss
                    if order.order_type == "credit":
                        result.total_credit += order.net_price * 100 * qty

                    logger.info(
                        "multi_pillar_order_built",
                        symbol=symbol,
                        pillar=pillar,
                        credit=order.net_price,
                        max_loss=order.max_loss,
                    )
            except Exception as e:
                logger.error(
                    "multi_pillar_build_failed",
                    symbol=symbol,
                    pillar=pillar,
                    error=str(e),
                )
                continue

        logger.info(
            "multi_pillar_result",
            symbol=symbol,
            pillars_executed=result.pillars_executed,
            total_risk=result.total_risk,
            total_credit=result.total_credit,
        )
        return result

    async def _build_for_pillar(
        self,
        symbol: str,
        pillar: int,
        direction: str,
        chain: list[OptionQuote],
        quantity: int,
        expiration: str,
        current_price: float = 0.0,
    ) -> SpreadOrder | None:
        """Build order for a specific pillar."""
        if pillar == 1:
            return await self.build_iron_condor(symbol, chain, quantity, expiration)
        elif pillar == 2:
            return await self.build_bear_call(symbol, chain, quantity, expiration)
        elif pillar == 3:
            return await self.build_bull_put(symbol, chain, quantity, expiration)
        elif pillar == 4:
            return await self.build_directional_scalp(symbol, chain, direction, quantity, expiration)
        elif pillar == 5:
            # Butterfly needs current_price; estimate from chain if not provided
            if current_price <= 0 and chain:
                # Use midpoint of ATM options as price estimate
                strikes = sorted(set(o.strike for o in chain))
                current_price = strikes[len(strikes) // 2] if strikes else 0.0
            return await self.build_butterfly(symbol, chain, direction, current_price, quantity, expiration)
        return None

    # ── Pyramid / Scale-In ───────────────────────────────────────

    async def scale_into_position(
        self,
        existing_order: SpreadOrder,
        current_value: float,
        additional_quantity: int,
        chain: list[OptionQuote],
    ) -> SpreadOrder | None:
        """Scale into an existing winning position by adding more contracts.

        Only scales in if the existing position is profitable. This is pyramiding —
        adding to winners, never to losers.

        For credit spreads (P1-P3): position is profitable if current_value < entry_credit
        For debit (P4): position is profitable if current_value > entry_debit

        The new contracts are added at current market prices (not the original entry).
        The PositionManager tracks the blended average cost.

        Args:
            existing_order: The original SpreadOrder for the position.
            current_value: Current market value of the existing spread.
            additional_quantity: Number of new contracts to add.
            chain: Current option chain for fresh pricing.

        Returns:
            A new SpreadOrder for the additional contracts, or None if scale-in is rejected.
        """
        is_credit = existing_order.order_type == "credit"

        # Check if position is profitable before scaling in
        if is_credit:
            # Credit spread: profitable when value decreased from entry
            pnl_pct = (existing_order.net_price - current_value) / existing_order.net_price if existing_order.net_price > 0 else 0
            if pnl_pct <= 0:
                logger.info(
                    "scale_in_rejected_losing",
                    symbol=existing_order.symbol,
                    pillar=existing_order.pillar,
                    entry=existing_order.net_price,
                    current=current_value,
                    pnl_pct=f"{pnl_pct:.1%}",
                )
                return None
        else:
            # Debit (P4): profitable when value increased from entry
            pnl_pct = (current_value - existing_order.net_price) / existing_order.net_price if existing_order.net_price > 0 else 0
            if pnl_pct <= 0:
                logger.info(
                    "scale_in_rejected_losing",
                    symbol=existing_order.symbol,
                    pillar=existing_order.pillar,
                    entry=existing_order.net_price,
                    current=current_value,
                    pnl_pct=f"{pnl_pct:.1%}",
                )
                return None

        # Minimum profitability threshold: at least 10% in the money before scaling
        if pnl_pct < 0.10:
            logger.info(
                "scale_in_rejected_insufficient_profit",
                symbol=existing_order.symbol,
                pnl_pct=f"{pnl_pct:.1%}",
                min_required="10%",
            )
            return None

        # Build a new order with the same parameters but at current prices
        direction = "BULL" if existing_order.pillar == 3 else "BEAR" if existing_order.pillar == 2 else "NEUTRAL"

        scale_order = await self._build_for_pillar(
            symbol=existing_order.symbol,
            pillar=existing_order.pillar,
            direction=direction,
            chain=chain,
            quantity=additional_quantity,
            expiration=existing_order.expiration,
        )

        if scale_order:
            logger.info(
                "scale_in_approved",
                symbol=existing_order.symbol,
                pillar=existing_order.pillar,
                existing_pnl=f"{pnl_pct:.1%}",
                additional_qty=additional_quantity,
                new_price=scale_order.net_price,
            )

        return scale_order

    # ── Order Submission ─────────────────────────────────────────

    async def submit_order(self, order: SpreadOrder) -> dict[str, Any]:
        """Submit a spread order to the broker.

        Handles both single-leg (P4) and multi-leg (P1-P3) orders.
        Automatically dispatches to Alpaca or Tradier format based on client type.

        Args:
            order: The SpreadOrder to submit.

        Returns:
            Broker API response with order ID.
        """
        if isinstance(self.client, AlpacaClient):
            result = await self._submit_alpaca(order)
        else:
            result = await self._submit_tradier(order)

        logger.info(
            "order_submitted",
            symbol=order.symbol,
            pillar=order.pillar,
            legs=len(order.legs),
            rung=order.rung_label or "single",
            result=result,
        )
        return result

    async def _submit_tradier(self, order: SpreadOrder) -> dict[str, Any]:
        """Submit order via Tradier API."""
        if len(order.legs) == 1:
            leg = order.legs[0]
            return await self.client.place_order(
                symbol=order.symbol,
                option_symbol=leg.option_symbol,
                side=leg.side.value,
                quantity=leg.quantity,
                order_type="limit" if order.net_price > 0 else "market",
                price=order.net_price if order.net_price > 0 else None,
                duration=order.time_in_force,
            )
        else:
            legs_data = [
                {
                    "option_symbol": leg.option_symbol,
                    "side": leg.side.value,
                    "quantity": leg.quantity,
                }
                for leg in order.legs
            ]
            return await self.client.place_multileg_order(
                symbol=order.symbol,
                legs=legs_data,
                order_type=order.order_type,
                price=order.net_price,
                duration=order.time_in_force,
            )

    async def _submit_alpaca(self, order: SpreadOrder) -> dict[str, Any]:
        """Submit order via Alpaca API."""
        # Map Tradier side names to Alpaca
        side_map = {
            "buy_to_open": "buy",
            "sell_to_open": "sell",
            "buy_to_close": "buy",
            "sell_to_close": "sell",
        }

        if len(order.legs) == 1:
            leg = order.legs[0]
            alpaca_order = await self.client.place_order(
                symbol=leg.option_symbol,
                side=side_map.get(leg.side.value, "buy"),
                qty=leg.quantity,
                order_type="limit" if order.net_price > 0 else "market",
                limit_price=order.net_price if order.net_price > 0 else None,
                time_in_force=order.time_in_force,
            )
            return {"order": {"id": alpaca_order.id, "status": alpaca_order.status}}
        else:
            legs_data = [
                {
                    "symbol": leg.option_symbol,
                    "side": side_map.get(leg.side.value, "buy"),
                    "qty": leg.quantity,
                }
                for leg in order.legs
            ]
            # Map Tradier order types to Alpaca
            alpaca_type = "limit"
            if order.order_type in ("credit", "debit"):
                alpaca_type = "limit"

            alpaca_order = await self.client.submit_multi_leg_order(
                symbol=order.symbol,
                legs=legs_data,
                order_type=alpaca_type,
                net_price=order.net_price,
                time_in_force=order.time_in_force,
            )
            return {"order": {"id": alpaca_order.id, "status": alpaca_order.status}}

    async def submit_ic_ladder(self, ladder: ICLadderOrder) -> list[dict[str, Any]]:
        """Submit all rungs of an IC Ladder.

        Each rung is submitted as a separate multi-leg order.
        If any rung fails, the others are still submitted (best effort).

        Args:
            ladder: The ICLadderOrder to submit.

        Returns:
            List of Tradier API responses, one per rung.
        """
        results = []
        for rung in ladder.rungs:
            try:
                result = await self.submit_order(rung)
                results.append(result)
            except Exception as e:
                logger.error(
                    "ic_ladder_rung_submit_failed",
                    symbol=ladder.symbol,
                    rung=rung.rung_label,
                    error=str(e),
                )
                results.append({"error": str(e), "rung": rung.rung_label})

        logger.info(
            "ic_ladder_submitted",
            symbol=ladder.symbol,
            rungs_submitted=len([r for r in results if "error" not in r]),
            rungs_failed=len([r for r in results if "error" in r]),
        )
        return results


#===============================================================================
# SECTION: EXECUTION - Position Manager
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/execution/position_manager.py (1105 lines)
#-------------------------------------------------------------------------------

"""Position Manager — Track, Monitor, and Manage Open Positions.

Handles all post-entry position management:
    - Track entry price, targets, and stops
    - P1-P3: profit target at 75% of credit (was 50%), tiered stop losses
    - P4: trailing stop (20% initial, tighten to 10% after 50% gain)
    - Power hour management: different rules after 3:00 PM
    - Time-based exits: close P1-P3 at 3:45 PM ET
    - Expire worthless tracking: flag near-expiry OTM spreads to let expire
    - Multi-day swing position tracking with overnight P&L
    - Scale-in tracking with average cost calculation
    - Real-time Greeks monitoring

Tiered Stop Loss System:
    Position is split into 3 tranches with different stop levels:
    - Tranche 1 (1/3): Tightest stop (e.g., $3.40) — first to exit on dips
    - Tranche 2 (1/3): Medium stop (e.g., $2.90) — exits on deeper moves
    - Tranche 3 (1/3): Widest stop (e.g., $2.30) — only exits on worst case
    This way a brief dip only stops out 1/3, not the whole position.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import TradierClient
from esther.data.alpaca import AlpacaClient
from esther.execution.pillars import SpreadOrder, OrderSide, check_expire_worthless, OptionType

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_PROFIT = "CLOSED_PROFIT"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_TIME = "CLOSED_TIME"
    CLOSED_FORCE = "CLOSED_FORCE"
    CLOSED_TRAIL = "CLOSED_TRAIL"
    EXPIRED_WORTHLESS = "EXPIRED_WORTHLESS"  # Let expire for max profit
    CLOSED_TIERED_STOP = "CLOSED_TIERED_STOP"  # Partial stop via tiered system


class TrancheStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"


class Tranche(BaseModel):
    """A single tranche within a tiered stop system.

    Each position is split into 3 tranches with different stop levels.
    When a tranche's stop is hit, only that portion of the position is closed.
    """

    id: int  # 1, 2, or 3
    quantity: int  # contracts in this tranche
    stop_price: float  # stop level for this tranche
    status: TrancheStatus = TrancheStatus.ACTIVE
    stopped_at: datetime | None = None
    stopped_value: float = 0.0


class ScaleInEntry(BaseModel):
    """Record of a scale-in (adding to an existing position)."""

    added_at: datetime = Field(default_factory=datetime.now)
    quantity: int
    price: float  # price at which the scale-in was made
    order_id: str = ""


class Position(BaseModel):
    """A tracked open position with tiered stops and scale-in tracking."""

    id: str  # unique position ID
    symbol: str
    pillar: int
    order_id: str = ""
    legs: list[dict[str, Any]] = []
    quantity: int = 1

    # Entry
    entry_price: float = 0.0  # credit received (P1-P3) or debit paid (P4)
    entry_time: datetime = Field(default_factory=datetime.now)
    expiration: str = ""

    # Targets — 75% profit target (was 50%)
    profit_target: float = 0.0  # price to close at for profit
    stop_loss: float = 0.0  # overall stop (worst case, all tranches)

    # Tiered stop system
    tranches: list[Tranche] = []
    active_quantity: int = 0  # contracts still open (not stopped out)

    # P4 trailing stop
    trail_pct: float = 0.0
    highest_value: float = 0.0  # high water mark for trailing stop
    trail_tightened: bool = False

    # Current state
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    close_time: datetime | None = None
    close_reason: str = ""

    # Expire worthless tracking
    expire_worthless_flagged: bool = False
    expire_worthless_checked_at: datetime | None = None

    # Scale-in tracking
    scale_ins: list[ScaleInEntry] = []
    average_entry_price: float = 0.0  # weighted average including scale-ins
    total_invested_quantity: int = 0  # total contracts including scale-ins

    # Multi-day swing tracking
    is_swing: bool = False  # True if this is a multi-day position
    overnight_pnl: float = 0.0  # P&L from overnight holds
    previous_close_value: float = 0.0  # value at previous day's close
    swing_days: int = 0  # number of days position has been open
    swing_thesis: str = ""  # reason for holding overnight

    # Runner mode (P4 directional — let 30% ride after first target)
    runner_active: bool = False
    runner_quantity: int = 0
    runner_trail_pct: float = 0.20  # wider trail for runners (2x normal)
    initial_quantity_closed: int = 0

    # Power hour flag
    is_power_hour_entry: bool = False

    # Greeks
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0

    # Metadata
    tier: str = ""
    direction: str = ""  # BULL or BEAR
    debate_confidence: int = 0


class PositionManager:
    """Manages all open positions with tiered stops, expire worthless, and swing tracking.

    Position Management Rules by Pillar:

    P1 (Iron Condors):
        - Profit: Close at 75% of credit received (was 50%)
        - Stop: Tiered — 3 tranches at different stop levels
        - Time: Force close at 3:45 PM ET
        - Expire worthless: If >80% OTM with <15 min, let expire

    P2 (Bear Call Spreads):
        - Same as P1

    P3 (Bull Put Spreads):
        - Same as P1

    P4 (0DTE Directional Scalps):
        - Trailing stop: starts at 20% from peak
        - Tightens to 10% after position gains 50%
        - Power hour mode: wider stops (25%), faster targets after 3:00 PM
        - No time exit (0DTE, expires end of day anyway)
    """

    # Tradier side → Alpaca side mapping
    _SIDE_MAP = {
        "buy_to_open": "buy",
        "sell_to_open": "sell",
        "buy_to_close": "buy",
        "sell_to_close": "sell",
    }

    def __init__(self, client: TradierClient | AlpacaClient):
        self.client = client
        self._cfg = config()
        self._positions: dict[str, Position] = {}
        self._closed_positions: list[Position] = []
        self._next_id = 1
        self._swing_positions: dict[str, Position] = {}  # Separate tracking for swings

    async def _close_leg(
        self, symbol: str, option_symbol: str, side: str, quantity: int
    ) -> None:
        """Close a single option leg via the active broker."""
        if isinstance(self.client, AlpacaClient):
            await self.client.place_order(
                symbol=option_symbol,
                side=self._SIDE_MAP.get(side, "buy"),
                qty=quantity,
                order_type="market",
            )
        else:
            await self.client.place_order(
                symbol=symbol,
                option_symbol=option_symbol,
                side=side,
                quantity=quantity,
                order_type="market",
            )

    @property
    def open_positions(self) -> list[Position]:
        """All currently open positions (including swings)."""
        return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]

    @property
    def open_day_positions(self) -> list[Position]:
        """Open positions that are NOT swings (0DTE only)."""
        return [p for p in self.open_positions if not p.is_swing]

    @property
    def open_swing_positions(self) -> list[Position]:
        """Open positions that ARE swings (multi-day)."""
        return [p for p in self.open_positions if p.is_swing]

    @property
    def closed_positions(self) -> list[Position]:
        """All closed positions (today)."""
        return list(self._closed_positions)

    def open_position(
        self,
        order: SpreadOrder,
        order_id: str = "",
        direction: str = "",
        tier: str = "",
        confidence: int = 0,
        is_swing: bool = False,
        swing_thesis: str = "",
    ) -> Position:
        """Register a new position after order fill with tiered stops.

        Args:
            order: The spread order that was filled.
            order_id: Tradier order ID.
            direction: BULL or BEAR.
            tier: Ticker tier (tier1, tier2, tier3).
            confidence: Debate confidence (0-100).
            is_swing: Whether this is a multi-day swing position.
            swing_thesis: Reason for holding overnight (if swing).

        Returns:
            The new Position object.
        """
        pos_id = f"pos_{self._next_id:04d}"
        self._next_id += 1

        entry_price = order.net_price
        pillar_cfg = self._get_pillar_config(order.pillar)
        is_power_hour = self._is_power_hour()

        # Set profit target and stop loss based on pillar
        # Profit target is now 75% (was 50%)
        if order.pillar in (1, 2, 3):
            # Credit spreads: profit when value decreases
            # 75% profit target: close when value drops to 25% of entry
            profit_target = round(entry_price * (1 - 0.75), 2)
            # Overall stop at 2x credit (worst case — all tranches stopped)
            stop_loss = round(entry_price * pillar_cfg.get("stop_loss_multiplier", 2.0), 2)
            trail_pct = 0.0

            # Build tiered stops
            tranches = self._build_tiered_stops(
                total_quantity=order.quantity,
                entry_price=entry_price,
                stop_multiplier=pillar_cfg.get("stop_loss_multiplier", 2.0),
            )
        else:
            # P4 directional: profit when value increases
            profit_target = 0.0  # No fixed target, trailing stop only
            stop_loss = 0.0  # Trailing stop handles this
            tranches = []

            # Power hour: wider initial trail for momentum capture
            if is_power_hour:
                trail_pct = 0.25  # 25% trail during power hour (vs normal 20%)
            else:
                trail_pct = self._cfg.pillars.p4.initial_trail_pct

        position = Position(
            id=pos_id,
            symbol=order.symbol,
            pillar=order.pillar,
            order_id=order_id,
            legs=[leg.model_dump() for leg in order.legs],
            quantity=order.quantity,
            entry_price=entry_price,
            expiration=order.expiration,
            profit_target=profit_target,
            stop_loss=stop_loss,
            trail_pct=trail_pct,
            highest_value=entry_price if order.pillar == 4 else 0.0,
            current_value=entry_price,
            tier=tier,
            direction=direction,
            debate_confidence=confidence,
            tranches=tranches,
            active_quantity=order.quantity,
            average_entry_price=entry_price,
            total_invested_quantity=order.quantity,
            is_swing=is_swing,
            swing_thesis=swing_thesis,
            is_power_hour_entry=is_power_hour,
        )

        self._positions[pos_id] = position

        logger.info(
            "position_opened",
            id=pos_id,
            symbol=order.symbol,
            pillar=order.pillar,
            entry=entry_price,
            target=profit_target,
            stop=stop_loss,
            tranches=len(tranches),
            is_swing=is_swing,
            power_hour=is_power_hour,
        )

        return position

    def _build_tiered_stops(
        self,
        total_quantity: int,
        entry_price: float,
        stop_multiplier: float,
    ) -> list[Tranche]:
        """Build 3 tranches with graduated stop levels.

        Tranche 1 (1/3 of position): Tightest stop — first line of defense
        Tranche 2 (1/3 of position): Medium stop — protects from moderate moves
        Tranche 3 (1/3 of position): Widest stop — only stops on worst case

        For a $1.70 credit with 2x stop multiplier ($3.40):
        - Tranche 1: $3.40 stop (tightest, 2.0x)
        - Tranche 2: $2.90 stop (medium, ~1.7x)
        - Tranche 3: $2.30 stop (widest, ~1.35x)

        Wait, for credit spreads the stop is when VALUE RISES above the stop.
        So tightest stop = lowest stop price (triggers first on any adverse move).

        Actually, let me re-think. For credit spreads:
        - Entry credit = $1.70
        - We lose when spread value INCREASES
        - Stop loss = 2x credit = $3.40 means we close when value reaches $3.40
        - Tightest stop = stops out first = LOWEST stop price
        - Widest stop = tolerates more pain = HIGHEST stop price

        Tranche 1 (tightest): stop at 1.7x entry (exits first)
        Tranche 2 (medium): stop at 2.0x entry
        Tranche 3 (widest): stop at 2.5x entry (most tolerance)

        Args:
            total_quantity: Total contracts to split across tranches.
            entry_price: Entry credit/debit price.
            stop_multiplier: Base stop loss multiplier from config.

        Returns:
            List of 3 Tranche objects.
        """
        if total_quantity < 3:
            # Can't split less than 3 contracts into 3 tranches
            # Use single tranche with standard stop
            return [
                Tranche(
                    id=1,
                    quantity=total_quantity,
                    stop_price=round(entry_price * stop_multiplier, 2),
                ),
            ]

        # Split into 3 roughly equal tranches
        base_qty = total_quantity // 3
        remainder = total_quantity % 3

        qty_1 = base_qty + (1 if remainder > 0 else 0)
        qty_2 = base_qty + (1 if remainder > 1 else 0)
        qty_3 = base_qty

        # Graduated stop levels
        # Tranche 1: tightest (exits first on adverse moves)
        stop_1 = round(entry_price * (stop_multiplier * 0.85), 2)  # ~1.7x for 2x base
        # Tranche 2: medium
        stop_2 = round(entry_price * stop_multiplier, 2)  # 2.0x (standard)
        # Tranche 3: widest (most tolerance)
        stop_3 = round(entry_price * (stop_multiplier * 1.25), 2)  # ~2.5x for 2x base

        return [
            Tranche(id=1, quantity=qty_1, stop_price=stop_1),
            Tranche(id=2, quantity=qty_2, stop_price=stop_2),
            Tranche(id=3, quantity=qty_3, stop_price=stop_3),
        ]

    def record_scale_in(
        self,
        position_id: str,
        additional_quantity: int,
        scale_in_price: float,
        order_id: str = "",
    ) -> Position | None:
        """Record a scale-in (adding to an existing position).

        Updates the average entry price and total quantity. Only call this
        AFTER the scale-in order has been filled.

        Args:
            position_id: ID of the existing position.
            additional_quantity: Number of new contracts added.
            scale_in_price: Price of the new contracts.
            order_id: Order ID for the scale-in fill.

        Returns:
            Updated Position, or None if position not found.
        """
        pos = self._positions.get(position_id)
        if not pos:
            logger.warning("scale_in_position_not_found", id=position_id)
            return None

        # Record the scale-in
        entry = ScaleInEntry(
            quantity=additional_quantity,
            price=scale_in_price,
            order_id=order_id,
        )
        pos.scale_ins.append(entry)

        # Update average entry price (weighted average)
        old_total = pos.average_entry_price * pos.total_invested_quantity
        new_total = scale_in_price * additional_quantity
        pos.total_invested_quantity += additional_quantity
        pos.average_entry_price = round(
            (old_total + new_total) / pos.total_invested_quantity, 4
        )

        # Update quantity
        pos.quantity += additional_quantity
        pos.active_quantity += additional_quantity

        # Rebuild tiered stops with new total quantity
        if pos.pillar in (1, 2, 3) and pos.active_quantity >= 3:
            pillar_cfg = self._get_pillar_config(pos.pillar)
            stop_mult = pillar_cfg.get("stop_loss_multiplier", 2.0)
            pos.tranches = self._build_tiered_stops(
                total_quantity=pos.active_quantity,
                entry_price=pos.average_entry_price,
                stop_multiplier=stop_mult,
            )

        logger.info(
            "scale_in_recorded",
            id=position_id,
            added_qty=additional_quantity,
            price=scale_in_price,
            new_avg_price=pos.average_entry_price,
            total_qty=pos.total_invested_quantity,
        )

        return pos

    async def update_positions(self) -> list[Position]:
        """Update all open positions with current prices and check exits.

        This is called every scan cycle. For each position:
        1. Fetch current option prices
        2. Update P&L and Greeks
        3. Check expire worthless eligibility
        4. Check tiered stops (per-tranche)
        5. Check profit target, trailing stop, and time exit
        6. Apply power hour management rules after 3:00 PM
        7. Close positions that hit exit conditions

        Returns:
            List of positions that were closed this cycle.
        """
        closed_this_cycle: list[Position] = []
        is_power_hour = self._is_power_hour()

        for pos in self.open_positions:
            try:
                # Update current value from Tradier
                await self._update_position_value(pos)

                # Check expire worthless FIRST (before any close logic)
                if pos.pillar in (1, 2, 3) and not pos.expire_worthless_flagged:
                    self._check_expire_worthless(pos)

                # If flagged for expire worthless, skip normal close logic
                if pos.expire_worthless_flagged:
                    if _minutes_to_close() <= 0:
                        # Market closed — position expired worthless!
                        pos.status = PositionStatus.EXPIRED_WORTHLESS
                        pos.close_time = datetime.now()
                        pos.close_reason = "EXPIRED_WORTHLESS: Let expire for max profit"
                        pos.unrealized_pnl = pos.entry_price * 100 * pos.quantity  # Full credit = profit
                        self._closed_positions.append(pos)
                        del self._positions[pos.id]
                        closed_this_cycle.append(pos)
                        logger.info(
                            "position_expired_worthless",
                            id=pos.id,
                            symbol=pos.symbol,
                            pnl=pos.unrealized_pnl,
                        )
                    continue

                # Check tiered stops for P1-P3
                if pos.pillar in (1, 2, 3) and pos.tranches:
                    tranche_closes = self._check_tiered_stops(pos)
                    if tranche_closes:
                        for tranche in tranche_closes:
                            await self._close_tranche(pos, tranche)

                        # If all tranches are stopped, close the whole position
                        if all(t.status == TrancheStatus.STOPPED for t in pos.tranches):
                            pos.status = PositionStatus.CLOSED_TIERED_STOP
                            pos.close_time = datetime.now()
                            pos.close_reason = "ALL_TRANCHES_STOPPED"
                            self._closed_positions.append(pos)
                            if pos.id in self._positions:
                                del self._positions[pos.id]
                            closed_this_cycle.append(pos)
                            continue

                # Apply power hour management if applicable
                if is_power_hour and pos.pillar == 4:
                    self._apply_power_hour_rules(pos)

                # Check exit conditions (standard)
                close_reason = self._check_exits(pos, is_power_hour)

                if close_reason:
                    await self._close_position(pos, close_reason)
                    closed_this_cycle.append(pos)

            except Exception as e:
                logger.error("position_update_failed", id=pos.id, error=str(e))

        # Update swing position day counts
        self._update_swing_tracking()

        return closed_this_cycle

    def _check_expire_worthless(self, pos: Position) -> None:
        """Check if a credit spread should be flagged to expire worthless.

        Conditions:
        - Spread is >80% OTM (>2 standard deviations from current price)
        - Less than 15 minutes to market close
        - Position is profitable

        This saves a day trade and captures maximum profit.
        """
        if pos.pillar not in (1, 2, 3):
            return

        minutes_left = _minutes_to_close()
        if minutes_left > 15:
            return

        # Find the short strike and determine option type
        short_legs = [
            leg for leg in pos.legs
            if leg.get("side") == OrderSide.SELL_TO_OPEN.value
        ]
        if not short_legs:
            return

        # Check if the spread value is near zero (>80% of credit has decayed)
        if pos.entry_price > 0 and pos.current_value > 0:
            decay_pct = 1 - (abs(pos.current_value) / pos.entry_price)
            if decay_pct >= 0.80:
                pos.expire_worthless_flagged = True
                pos.expire_worthless_checked_at = datetime.now()
                logger.info(
                    "expire_worthless_flagged",
                    id=pos.id,
                    symbol=pos.symbol,
                    decay_pct=f"{decay_pct:.1%}",
                    minutes_left=f"{minutes_left:.0f}",
                    current_value=pos.current_value,
                    entry_credit=pos.entry_price,
                )

    def _check_tiered_stops(self, pos: Position) -> list[Tranche]:
        """Check which tranches have hit their stop levels.

        For credit spreads, a stop is triggered when the spread value
        RISES above the tranche's stop price.

        Returns:
            List of tranches that need to be closed.
        """
        triggered = []
        for tranche in pos.tranches:
            if tranche.status != TrancheStatus.ACTIVE:
                continue

            if pos.current_value >= tranche.stop_price:
                triggered.append(tranche)
                logger.info(
                    "tranche_stop_triggered",
                    id=pos.id,
                    tranche=tranche.id,
                    stop=tranche.stop_price,
                    current=pos.current_value,
                    qty=tranche.quantity,
                )

        return triggered

    async def _close_tranche(self, pos: Position, tranche: Tranche) -> None:
        """Close a single tranche of a position.

        Only closes the quantity associated with this tranche, not the whole position.
        """
        tranche.status = TrancheStatus.STOPPED
        tranche.stopped_at = datetime.now()
        tranche.stopped_value = pos.current_value

        # Update active quantity
        pos.active_quantity -= tranche.quantity

        # Submit closing orders for this tranche's quantity
        for leg in pos.legs:
            close_side = (
                OrderSide.BUY_TO_CLOSE.value
                if leg.get("side") == OrderSide.SELL_TO_OPEN.value
                else OrderSide.SELL_TO_CLOSE.value
            )

            try:
                await self._close_leg(
                    symbol=pos.symbol,
                    option_symbol=leg["option_symbol"],
                    side=close_side,
                    quantity=tranche.quantity,
                )
            except Exception as e:
                logger.error(
                    "tranche_close_failed",
                    id=pos.id,
                    tranche=tranche.id,
                    leg=leg["option_symbol"],
                    error=str(e),
                )

        logger.info(
            "tranche_closed",
            id=pos.id,
            tranche=tranche.id,
            qty_closed=tranche.quantity,
            remaining_qty=pos.active_quantity,
            stop_price=tranche.stop_price,
        )

    def _apply_power_hour_rules(self, pos: Position) -> None:
        """Apply power hour management rules for P4 positions after 3:00 PM.

        Power hour changes:
        - Wider trailing stop (25% vs 20%) to let momentum plays breathe
        - Faster profit taking: if >30% profit, tighten trail to 15% (vs waiting for 50%)
        - More aggressive on high-delta moves
        """
        if pos.pillar != 4:
            return

        # Wider trailing stop during power hour
        if not pos.is_power_hour_entry and pos.trail_pct < 0.25:
            pos.trail_pct = 0.25
            logger.info("power_hour_widened_trail", id=pos.id, new_trail=0.25)

        # Faster profit taking: tighten trail after 30% gain (vs normal 50%)
        if pos.entry_price > 0 and pos.current_value > 0:
            gain_pct = (pos.current_value - pos.entry_price) / pos.entry_price
            if gain_pct >= 0.30 and not pos.trail_tightened:
                pos.trail_pct = 0.15  # Tighter trail to lock in power hour profits
                pos.trail_tightened = True
                logger.info(
                    "power_hour_tightened_trail",
                    id=pos.id,
                    gain_pct=f"{gain_pct:.1%}",
                    new_trail=0.15,
                )

    def _update_swing_tracking(self) -> None:
        """Update day counts and overnight P&L for swing positions."""
        now_et = datetime.now(ET)

        for pos in self.open_swing_positions:
            # Update swing days
            entry_date = pos.entry_time.date() if pos.entry_time.tzinfo is None else pos.entry_time.astimezone(ET).date()
            pos.swing_days = (now_et.date() - entry_date).days

            # Calculate overnight P&L if we have a previous close value
            if pos.previous_close_value > 0:
                if pos.pillar in (1, 2, 3):
                    pos.overnight_pnl = round(
                        (pos.previous_close_value - pos.current_value) * 100 * pos.active_quantity, 2
                    )
                else:
                    pos.overnight_pnl = round(
                        (pos.current_value - pos.previous_close_value) * 100 * pos.active_quantity, 2
                    )

    def record_day_close_values(self) -> None:
        """Record current values as previous close for overnight P&L tracking.

        Call this at EOD for swing positions that will be held overnight.
        """
        for pos in self.open_swing_positions:
            pos.previous_close_value = pos.current_value
            logger.info(
                "swing_close_value_recorded",
                id=pos.id,
                symbol=pos.symbol,
                close_value=pos.current_value,
                swing_days=pos.swing_days,
            )

    def get_overnight_pnl(self) -> float:
        """Get total overnight P&L across all swing positions."""
        return sum(pos.overnight_pnl for pos in self.open_swing_positions)

    async def _update_position_value(self, pos: Position) -> None:
        """Fetch current prices and update position value + Greeks."""
        # Get quotes for all legs
        leg_symbols = [leg["option_symbol"] for leg in pos.legs]

        try:
            quotes = await self.client.get_quotes(leg_symbols)
        except Exception:
            return  # Skip update if quotes fail

        total_value = 0.0
        total_delta = 0.0
        total_theta = 0.0
        total_gamma = 0.0
        total_vega = 0.0

        for leg in pos.legs:
            quote = next((q for q in quotes if q.symbol == leg["option_symbol"]), None)
            if not quote:
                continue

            mid = (quote.bid + quote.ask) / 2

            # Guard: Alpaca paper can return bid=0/ask=0 temporarily.
            # A zero mid on a position we just entered is bad data, not a real price.
            # Skip the update entirely to avoid false trailing-stop triggers.
            if mid <= 0 and pos.entry_price > 0:
                logger.warning(
                    "stale_quote_skipped",
                    id=pos.id,
                    symbol=leg["option_symbol"],
                    bid=quote.bid,
                    ask=quote.ask,
                )
                return  # Abort — keep previous current_value

            is_short = leg["side"] in ("sell_to_open", "sell_to_close")
            sign = -1 if is_short else 1

            total_value += mid * sign * leg["quantity"]

            # Aggregate Greeks if available
            if hasattr(quote, 'greeks') and quote.greeks:
                total_delta += quote.greeks.delta * sign * leg["quantity"]
                total_theta += quote.greeks.theta * sign * leg["quantity"]
                total_gamma += quote.greeks.gamma * sign * leg["quantity"]
                total_vega += quote.greeks.vega * sign * leg["quantity"]

        pos.current_value = round(total_value, 2)
        pos.delta = round(total_delta, 4)
        pos.theta = round(total_theta, 4)
        pos.gamma = round(total_gamma, 4)
        pos.vega = round(total_vega, 4)

        # Calculate unrealized P&L using active quantity
        active_qty = pos.active_quantity if pos.active_quantity > 0 else pos.quantity
        if pos.pillar in (1, 2, 3):
            # Credit spread: entered at credit, want value to decrease
            pos.unrealized_pnl = round(
                (pos.average_entry_price - pos.current_value) * 100 * active_qty, 2
            )
        else:
            # Debit (P4): entered at debit, want value to increase
            pos.unrealized_pnl = round(
                (pos.current_value - pos.average_entry_price) * 100 * active_qty, 2
            )

        # Add realized P&L from stopped tranches
        for tranche in pos.tranches:
            if tranche.status == TrancheStatus.STOPPED:
                tranche_pnl = (pos.average_entry_price - tranche.stopped_value) * 100 * tranche.quantity
                pos.unrealized_pnl += round(tranche_pnl, 2)

        # Update trailing stop high water mark for P4
        if pos.pillar == 4 and pos.current_value > pos.highest_value:
            pos.highest_value = pos.current_value

            # Check if we should tighten the trailing stop
            gain_pct = (pos.current_value - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            if gain_pct >= self._cfg.pillars.p4.tighten_after_gain_pct and not pos.trail_tightened:
                pos.trail_pct = self._cfg.pillars.p4.tight_trail_pct
                pos.trail_tightened = True
                logger.info("trailing_stop_tightened", id=pos.id, new_trail=pos.trail_pct)

    def _check_exits(self, pos: Position, is_power_hour: bool = False) -> str:
        """Check all exit conditions for a position.

        Args:
            pos: The position to check.
            is_power_hour: Whether we're in power hour (3:00-3:45 PM).

        Returns:
            Close reason string, or empty string if no exit triggered.
        """
        # Check P1-P3 profit target (75% of credit)
        if pos.pillar in (1, 2, 3):
            if pos.current_value <= pos.profit_target and pos.profit_target > 0:
                return f"PROFIT_TARGET_75PCT: Value {pos.current_value:.2f} <= target {pos.profit_target:.2f}"

            # Overall stop (worst case — should be caught by tiered stops first)
            if not pos.tranches and pos.current_value >= pos.stop_loss and pos.stop_loss > 0:
                return f"STOP_LOSS: Value {pos.current_value:.2f} >= stop {pos.stop_loss:.2f}"

        # Check P4 trailing stop
        if pos.pillar == 4 and pos.highest_value > 0 and pos.trail_pct > 0:
            trail_level = pos.highest_value * (1 - pos.trail_pct)
            if pos.current_value <= trail_level:
                return (
                    f"TRAILING_STOP: Value {pos.current_value:.2f} <= "
                    f"trail {trail_level:.2f} (high: {pos.highest_value:.2f}, "
                    f"trail%: {pos.trail_pct:.0%})"
                )

        # Time-based exit for P1-P3 (but NOT swing positions)
        if pos.pillar in (1, 2, 3) and not pos.is_swing:
            now = datetime.now(ET)
            eod_minutes = self._cfg.positions.eod_close_minutes_before if hasattr(self._cfg, 'positions') else 15
            eod_time = time(15, 45)
            if now.time() >= eod_time:
                return f"TIME_EXIT: Market closing in {eod_minutes} min"

        return ""

    async def _close_position(self, pos: Position, reason: str) -> None:
        """Close a position by submitting closing orders.

        Only closes remaining active quantity (accounts for stopped tranches).

        Args:
            pos: The position to close.
            reason: Why we're closing.
        """
        logger.info(
            "closing_position",
            id=pos.id,
            symbol=pos.symbol,
            reason=reason,
            pnl=pos.unrealized_pnl,
            active_qty=pos.active_quantity,
        )

        # Build closing order legs — only close active quantity
        close_qty = pos.active_quantity if pos.active_quantity > 0 else pos.quantity

        for leg in pos.legs:
            close_side = (
                OrderSide.BUY_TO_CLOSE.value
                if leg["side"] == OrderSide.SELL_TO_OPEN.value
                else OrderSide.SELL_TO_CLOSE.value
            )

            try:
                await self._close_leg(
                    symbol=pos.symbol,
                    option_symbol=leg["option_symbol"],
                    side=close_side,
                    quantity=close_qty,
                )
            except Exception as e:
                logger.error(
                    "close_order_failed",
                    id=pos.id,
                    leg=leg["option_symbol"],
                    error=str(e),
                )

        # Update position status
        if "PROFIT" in reason:
            pos.status = PositionStatus.CLOSED_PROFIT
        elif "STOP" in reason:
            pos.status = PositionStatus.CLOSED_STOP
        elif "TRAIL" in reason:
            pos.status = PositionStatus.CLOSED_TRAIL
        elif "TIME" in reason:
            pos.status = PositionStatus.CLOSED_TIME
        else:
            pos.status = PositionStatus.CLOSED_FORCE

        pos.close_time = datetime.now()
        pos.close_reason = reason

        # Move to closed list
        self._closed_positions.append(pos)
        if pos.id in self._positions:
            del self._positions[pos.id]

        logger.info(
            "position_closed",
            id=pos.id,
            symbol=pos.symbol,
            status=pos.status.value,
            pnl=pos.unrealized_pnl,
        )

    # ── Runner Mode (P4 Directional — Lever 2: Let Winners Run) ────

    def should_activate_runner(self, pos: Position, current_price: float) -> bool:
        """Check if a P4 position should activate runner mode.

        From @SuperLuckeee's Lever 2: "Don't sell too early. Let winners reach target."

        Runner mode activates when:
        - Position is P4 (directional scalp)
        - Runner is not already active
        - Position has gained 100%+ from entry (doubled)
        - At least 2 contracts remain

        When activated: close 70% at first target, let 30% ride with
        a wider trailing stop (2x normal) for potential 300-1000% returns.

        Args:
            pos: The position to check.
            current_price: Current option price.

        Returns:
            True if runner mode should be activated.
        """
        if pos.pillar != 4:
            return False
        if pos.runner_active:
            return False
        if pos.active_quantity < 2:
            return False  # Need at least 2 contracts to split

        # Check if position has doubled (100%+ gain)
        if pos.entry_price > 0:
            gain_pct = (current_price - pos.entry_price) / pos.entry_price
            return gain_pct >= 1.0  # 100% gain = doubled

        return False

    def activate_runner(self, position_id: str) -> bool:
        """Activate runner mode — close 70%, let 30% ride with wide trail.

        From @SuperLuckeee: "Added into strength and built this position uppp"
        — they scale OUT 70% at first target and let 30% run for the big move.

        This is how $1.50 → $28 (1,767%) happens. Without runners, you sell
        at $3 and miss the 10x.

        Args:
            position_id: The position to activate runner on.

        Returns:
            True if runner was activated, False if not eligible.
        """
        pos = self._positions.get(position_id)
        if pos is None:
            return False

        if pos.runner_active or pos.pillar != 4:
            return False

        total_qty = pos.active_quantity
        if total_qty < 2:
            return False

        # Close 70%, keep 30% as runner
        close_qty = max(1, int(total_qty * 0.70))
        runner_qty = total_qty - close_qty

        pos.initial_quantity_closed = close_qty
        pos.runner_active = True
        pos.runner_quantity = runner_qty
        pos.active_quantity = runner_qty  # Only runners remain

        # Widen the trailing stop for runners (2x normal)
        normal_trail = self._cfg.pillars.p4.tight_trail_pct if pos.trail_tightened else self._cfg.pillars.p4.initial_trail_pct
        pos.runner_trail_pct = normal_trail * 2.0
        pos.trail_pct = pos.runner_trail_pct  # Apply wider trail immediately

        logger.info(
            "runner_activated",
            position_id=position_id,
            symbol=pos.symbol,
            total_qty=total_qty,
            closed_qty=close_qty,
            runner_qty=runner_qty,
            runner_trail=f"{pos.runner_trail_pct:.0%}",
            entry_price=pos.entry_price,
            current_value=pos.current_value,
        )

        return True

    async def force_close_all(self, reason: str = "FORCE_CLOSE") -> list[Position]:
        """Emergency: close ALL open positions immediately.

        Used by Black Swan detector when RED is triggered,
        or by Risk Manager when daily loss cap is hit.
        Does NOT close positions flagged for expire worthless.

        Returns:
            List of all closed positions.
        """
        logger.warning("force_closing_all", count=len(self.open_positions), reason=reason)

        closed = []
        for pos in list(self.open_positions):
            # Don't force-close expire worthless positions (unless BLACK_SWAN)
            if pos.expire_worthless_flagged and "BLACK_SWAN" not in reason:
                logger.info(
                    "skipping_expire_worthless",
                    id=pos.id,
                    symbol=pos.symbol,
                )
                continue

            try:
                await self._close_position(pos, reason)
                closed.append(pos)
            except Exception as e:
                logger.error("force_close_failed", id=pos.id, error=str(e))

        return closed

    def get_position_count(self, tier: str | None = None) -> int:
        """Count open positions, optionally filtered by tier."""
        if tier:
            return len([p for p in self.open_positions if p.tier == tier])
        return len(self.open_positions)

    def get_daily_pnl(self) -> float:
        """Calculate total P&L for today (open + closed)."""
        closed_pnl = sum(p.unrealized_pnl for p in self._closed_positions)
        open_pnl = sum(p.unrealized_pnl for p in self.open_positions)
        return round(closed_pnl + open_pnl, 2)

    def get_position_by_symbol_pillar(
        self, symbol: str, pillar: int
    ) -> Position | None:
        """Find an open position by symbol and pillar (for scale-in checks)."""
        for pos in self.open_positions:
            if pos.symbol == symbol and pos.pillar == pillar:
                return pos
        return None

    def get_positions_for_symbol(self, symbol: str) -> list[Position]:
        """Get all open positions for a symbol (for multi-pillar tracking)."""
        return [p for p in self.open_positions if p.symbol == symbol]

    def _get_pillar_config(self, pillar: int) -> dict[str, Any]:
        """Get pillar-specific config as a dict."""
        pillar_map = {
            1: self._cfg.pillars.p1,
            2: self._cfg.pillars.p2,
            3: self._cfg.pillars.p3,
        }
        p = pillar_map.get(pillar)
        if p:
            return p.model_dump()
        return {}

    @staticmethod
    def _is_power_hour() -> bool:
        """Check if we're in power hour (3:00-3:45 PM ET)."""
        now_et = datetime.now(ET)
        return time(15, 0) <= now_et.time() <= time(15, 45)


def _minutes_to_close() -> float:
    """Calculate minutes until market close (4:00 PM ET)."""
    now_et = datetime.now(ET)
    close_dt = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = (close_dt - now_et).total_seconds() / 60
    return max(0, delta)


#===============================================================================
# SECTION: EXECUTION - LEAP Strategy
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/execution/leap.py (1021 lines)
#-------------------------------------------------------------------------------

"""LEAP Portfolio Manager — Long-Term Equity Options for Wealth Building.

LEAP (Long-term Equity Anticipation Securities) positions are 9-18 month
option contracts used for two distinct strategies:

1. **Deep ITM (delta > 0.80)** — Leveraged stock replacement. Moves nearly
   1:1 with the underlying but at a fraction of the capital. The "poor man's
   covered call" foundation.

2. **Speculative OTM (delta 0.20-0.40)** — Lottery tickets on high-conviction
   names. Small capital outlay, asymmetric upside. These are the moon shots.

Entry Discipline:
    - Only enter when RSI < 35 (oversold) OR price is at key Fibonacci support
    - Never chase — patience is the edge
    - Target January expirations (max theta runway)

Trim Schedule:
    - 25% off at +200% gain
    - Another 25% off at +400% gain
    - Let the remaining 50% ride with a trailing mental stop

Target Universe:
    NVDA, TSLA, AAPL, AMZN, META, PLTR, AVGO, CVNA, LLY, MSTR, GOOGL, APP
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from esther.data.tradier import TradierClient, OptionType, OptionQuote, Bar

logger = structlog.get_logger(__name__)

# Where we persist LEAP portfolio state
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEAP_PORTFOLIO_PATH = _PROJECT_ROOT / "data" / "leap_portfolio.json"

# Target symbols for LEAP scanning
LEAP_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMZN", "META", "PLTR",
    "AVGO", "CVNA", "LLY", "MSTR", "GOOGL", "APP",
]


# ── Enums ────────────────────────────────────────────────────────


class LeapStyle(str, Enum):
    """LEAP strategy style."""

    DEEP_ITM = "DEEP_ITM"          # delta > 0.80, leveraged stock replacement
    SPECULATIVE_OTM = "SPECULATIVE_OTM"  # delta 0.20-0.40, lottery tickets


class AlertType(str, Enum):
    """Types of LEAP alerts."""

    DELTA_EROSION = "DELTA_EROSION"       # Deep ITM losing delta edge
    TRIM_OPPORTUNITY = "TRIM_OPPORTUNITY"  # Position up enough to trim
    THESIS_REVIEW = "THESIS_REVIEW"        # Position down significantly
    EXPIRY_WARNING = "EXPIRY_WARNING"      # Approaching expiration


# ── Pydantic Models ──────────────────────────────────────────────


class TrimRecord(BaseModel):
    """Record of a partial exit (trim)."""

    date: str
    quantity_sold: int
    sell_price: float
    pnl: float
    pnl_pct: float
    reason: str


class LeapPosition(BaseModel):
    """A single LEAP position with full tracking."""

    id: str = Field(default_factory=lambda: f"leap_{uuid.uuid4().hex[:8]}")
    symbol: str                        # underlying ticker
    option_symbol: str = ""            # OCC option symbol from Tradier
    strike: float
    expiry: str                        # e.g. "2027-01-15"
    option_type: OptionType = OptionType.CALL
    style: LeapStyle
    quantity: int
    entry_price: float                 # per-contract premium at entry
    entry_date: str = Field(default_factory=lambda: date.today().isoformat())
    current_price: float = 0.0
    current_delta: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    trim_history: list[TrimRecord] = []
    active: bool = True


class LeapCandidate(BaseModel):
    """A potential LEAP entry identified by scanning."""

    symbol: str
    current_price: float
    rsi: float
    at_support: bool
    suggested_strike: float
    suggested_expiry: str
    style: LeapStyle
    estimated_cost: float              # per-contract cost (mid price * 100)
    rationale: str


class LeapAlert(BaseModel):
    """Alert generated during LEAP monitoring."""

    position_id: str
    symbol: str
    alert_type: AlertType
    message: str
    current_price: float = 0.0
    current_delta: float = 0.0
    pnl_pct: float = 0.0
    suggested_action: str = ""


class TrimResult(BaseModel):
    """Result of a partial position exit."""

    position_id: str
    symbol: str
    contracts_sold: int
    contracts_remaining: int
    sell_price: float
    realized_pnl: float
    realized_pnl_pct: float
    order_response: dict[str, Any] = {}


class LeapPortfolio(BaseModel):
    """Full LEAP portfolio summary."""

    positions: list[LeapPosition] = []
    total_cost: float = 0.0            # total capital deployed
    total_value: float = 0.0           # current market value
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    position_count: int = 0
    deep_itm_count: int = 0
    speculative_count: int = 0


# ── LEAP Manager ─────────────────────────────────────────────────


class LeapManager:
    """Manages a portfolio of LEAP option positions.

    Handles scanning for entries, executing purchases, monitoring positions,
    trimming winners, and persisting state to disk.

    Usage:
        async with TradierClient() as tradier:
            mgr = LeapManager(tradier)
            candidates = await mgr.get_leap_candidates(["NVDA", "TSLA"])
            for c in candidates:
                pos = await mgr.add_leap(c.symbol, c.suggested_strike,
                                          c.suggested_expiry, 5, c.style)
    """

    # Fibonacci retracement levels for support detection
    FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
    FIB_TOLERANCE = 0.015  # 1.5% proximity to fib level counts as "at support"

    # RSI entry threshold
    RSI_OVERSOLD = 35

    # Trim thresholds
    TRIM_1_GAIN_PCT = 2.0    # +200% → sell 25%
    TRIM_2_GAIN_PCT = 4.0    # +400% → sell another 25%
    TRIM_1_SIZE_PCT = 0.25
    TRIM_2_SIZE_PCT = 0.25

    # Alert thresholds
    DEEP_ITM_MIN_DELTA = 0.50    # alert if delta drops below this
    BIG_WINNER_PCT = 2.0          # +200% → consider trim
    BIG_LOSER_PCT = -0.30         # -30% → review thesis

    # Sizing
    MIN_CONTRACTS = 2
    MAX_CONTRACTS = 10

    # Minimum days to expiry for new entries
    MIN_DTE = 270  # ~9 months

    def __init__(self, tradier: TradierClient) -> None:
        self.tradier = tradier
        self._positions: dict[str, LeapPosition] = {}
        self._load_state()

    # ── Public Methods ───────────────────────────────────────────

    async def get_leap_candidates(
        self, symbols: list[str] | None = None
    ) -> list[LeapCandidate]:
        """Scan symbols for LEAP entry opportunities.

        Only suggests entries when:
        - RSI < 35 (oversold) OR price is at a Fibonacci support level
        - A valid January expiration exists 9+ months out
        - Option chain has liquid contracts at target delta

        Args:
            symbols: Tickers to scan. Defaults to LEAP_UNIVERSE.

        Returns:
            List of actionable LeapCandidate objects.
        """
        symbols = symbols or LEAP_UNIVERSE
        candidates: list[LeapCandidate] = []

        for symbol in symbols:
            try:
                candidate_pair = await self._evaluate_symbol(symbol)
                candidates.extend(candidate_pair)
            except Exception as e:
                logger.error("leap_scan_failed", symbol=symbol, error=str(e))

        logger.info("leap_scan_complete", candidates=len(candidates), scanned=len(symbols))
        return candidates

    async def add_leap(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        quantity: int,
        style: LeapStyle | str,
        option_type: OptionType = OptionType.CALL,
    ) -> LeapPosition:
        """Execute a LEAP purchase via Tradier and track it.

        Places a limit order at the mid price for the target contract.

        Args:
            symbol: Underlying ticker.
            strike: Strike price.
            expiry: Expiration date (YYYY-MM-DD).
            quantity: Number of contracts (clamped to 2-10).
            style: DEEP_ITM or SPECULATIVE_OTM.
            option_type: CALL or PUT.

        Returns:
            The new LeapPosition with order details.
        """
        if isinstance(style, str):
            style = LeapStyle(style)

        quantity = max(self.MIN_CONTRACTS, min(self.MAX_CONTRACTS, quantity))

        # Fetch the option chain to get current pricing
        chain = await self.tradier.get_option_chain(symbol, expiry, greeks=True)
        contract = self._find_contract(chain, strike, option_type)

        if not contract:
            raise ValueError(
                f"No contract found for {symbol} {strike} {option_type.value} exp {expiry}"
            )

        mid_price = contract.mid if contract.mid > 0 else round((contract.bid + contract.ask) / 2, 2)
        current_delta = contract.greeks.delta if contract.greeks else 0.0

        # Place limit order at mid
        order_resp = await self.tradier.place_order(
            symbol=symbol,
            option_symbol=contract.symbol,
            side="buy_to_open",
            quantity=quantity,
            order_type="limit",
            price=mid_price,
            duration="gtc",
        )

        position = LeapPosition(
            symbol=symbol,
            option_symbol=contract.symbol,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            style=style,
            quantity=quantity,
            entry_price=mid_price,
            current_price=mid_price,
            current_delta=current_delta,
        )

        self._positions[position.id] = position
        self._save_state()

        logger.info(
            "leap_opened",
            id=position.id,
            symbol=symbol,
            strike=strike,
            expiry=expiry,
            style=style.value,
            qty=quantity,
            price=mid_price,
            delta=current_delta,
            order=order_resp,
        )

        return position

    async def check_leaps(self) -> list[LeapAlert]:
        """Monitor all active LEAP positions and generate alerts.

        Checks for:
        - Delta erosion on Deep ITM positions (delta < 0.50)
        - Trim opportunities (position up 200%+)
        - Thesis review needed (position down 30%+)
        - Expiry warnings (< 90 days to expiration)

        Returns:
            List of LeapAlert objects requiring attention.
        """
        alerts: list[LeapAlert] = []
        active_positions = [p for p in self._positions.values() if p.active]

        if not active_positions:
            logger.info("leap_check_no_positions")
            return alerts

        for pos in active_positions:
            try:
                pos_alerts = await self._check_single_position(pos)
                alerts.extend(pos_alerts)
            except Exception as e:
                logger.error("leap_check_failed", id=pos.id, symbol=pos.symbol, error=str(e))

        self._save_state()
        logger.info("leap_check_complete", positions=len(active_positions), alerts=len(alerts))
        return alerts

    async def trim_position(self, position_id: str, pct: float | None = None) -> TrimResult:
        """Partially exit a LEAP position.

        If pct is not specified, uses the automatic trim schedule:
        - First trim: 25% at +200% gain
        - Second trim: 25% at +400% gain

        Args:
            position_id: ID of the position to trim.
            pct: Percentage of remaining position to sell (0.0-1.0).
                 If None, uses automatic trim rules.

        Returns:
            TrimResult with execution details.

        Raises:
            ValueError: If position not found or invalid trim.
        """
        pos = self._positions.get(position_id)
        if not pos or not pos.active:
            raise ValueError(f"Position {position_id} not found or inactive")

        # Determine trim percentage
        if pct is None:
            pct = self._calculate_auto_trim_pct(pos)
            if pct == 0:
                raise ValueError(
                    f"Position {position_id} doesn't meet auto-trim criteria "
                    f"(PnL: {pos.unrealized_pnl_pct:.0%})"
                )

        pct = max(0.01, min(1.0, pct))
        contracts_to_sell = max(1, int(pos.quantity * pct))

        if contracts_to_sell >= pos.quantity:
            contracts_to_sell = pos.quantity  # Full exit

        # Get current price for the sell
        current_price = await self._get_option_price(pos.option_symbol)
        if current_price is None:
            current_price = pos.current_price  # fallback

        # Place sell order
        order_resp = await self.tradier.place_order(
            symbol=pos.symbol,
            option_symbol=pos.option_symbol,
            side="sell_to_close",
            quantity=contracts_to_sell,
            order_type="limit",
            price=current_price,
            duration="gtc",
        )

        # Calculate realized P&L for this trim
        realized_pnl = round((current_price - pos.entry_price) * 100 * contracts_to_sell, 2)
        realized_pnl_pct = (
            (current_price - pos.entry_price) / pos.entry_price
            if pos.entry_price > 0 else 0.0
        )

        # Record the trim
        trim_record = TrimRecord(
            date=date.today().isoformat(),
            quantity_sold=contracts_to_sell,
            sell_price=current_price,
            pnl=realized_pnl,
            pnl_pct=round(realized_pnl_pct, 4),
            reason=f"Trim {pct:.0%} — PnL {pos.unrealized_pnl_pct:.0%}",
        )
        pos.trim_history.append(trim_record)

        # Update position
        pos.quantity -= contracts_to_sell
        if pos.quantity <= 0:
            pos.active = False
            logger.info("leap_fully_closed", id=pos.id, symbol=pos.symbol)

        self._save_state()

        result = TrimResult(
            position_id=pos.id,
            symbol=pos.symbol,
            contracts_sold=contracts_to_sell,
            contracts_remaining=pos.quantity,
            sell_price=current_price,
            realized_pnl=realized_pnl,
            realized_pnl_pct=round(realized_pnl_pct, 4),
            order_response=order_resp,
        )

        logger.info(
            "leap_trimmed",
            id=pos.id,
            symbol=pos.symbol,
            sold=contracts_to_sell,
            remaining=pos.quantity,
            price=current_price,
            realized_pnl=realized_pnl,
        )

        return result

    def get_leap_portfolio(self) -> LeapPortfolio:
        """Get full LEAP portfolio summary.

        Returns:
            LeapPortfolio with aggregated metrics across all active positions.
        """
        active = [p for p in self._positions.values() if p.active]

        total_cost = sum(p.entry_price * 100 * p.quantity for p in active)
        total_value = sum(p.current_price * 100 * p.quantity for p in active)
        total_pnl = total_value - total_cost
        total_pnl_pct = total_pnl / total_cost if total_cost > 0 else 0.0

        deep_itm = sum(1 for p in active if p.style == LeapStyle.DEEP_ITM)
        speculative = sum(1 for p in active if p.style == LeapStyle.SPECULATIVE_OTM)

        return LeapPortfolio(
            positions=active,
            total_cost=round(total_cost, 2),
            total_value=round(total_value, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            position_count=len(active),
            deep_itm_count=deep_itm,
            speculative_count=speculative,
        )

    # ── Private: Symbol Evaluation ───────────────────────────────

    async def _evaluate_symbol(self, symbol: str) -> list[LeapCandidate]:
        """Evaluate a single symbol for both LEAP styles.

        Returns 0-2 candidates (one per style if criteria are met).
        """
        candidates: list[LeapCandidate] = []

        # Fetch daily bars for RSI and Fibonacci levels
        end_date = date.today()
        start_date = end_date - timedelta(days=200)
        bars = await self.tradier.get_bars(
            symbol, interval="daily", start=start_date, end=end_date
        )

        if len(bars) < 30:
            logger.warning("leap_insufficient_bars", symbol=symbol, bars=len(bars))
            return candidates

        # Calculate RSI
        rsi = self._calculate_rsi(bars)

        # Check Fibonacci support
        at_support = self._check_fibonacci_support(bars)

        # Must meet entry criteria
        if rsi >= self.RSI_OVERSOLD and not at_support:
            logger.debug(
                "leap_no_entry_signal",
                symbol=symbol,
                rsi=round(rsi, 2),
                at_support=at_support,
            )
            return candidates

        current_price = bars[-1].close

        # Find the best January expiration
        expiry = await self._find_january_expiry(symbol)
        if not expiry:
            logger.warning("leap_no_valid_expiry", symbol=symbol)
            return candidates

        # Fetch option chain for that expiry
        chain = await self.tradier.get_option_chain(symbol, expiry, greeks=True)
        calls = [o for o in chain if o.option_type == OptionType.CALL]

        if not calls:
            return candidates

        # Build rationale
        entry_reasons: list[str] = []
        if rsi < self.RSI_OVERSOLD:
            entry_reasons.append(f"RSI oversold at {rsi:.1f}")
        if at_support:
            entry_reasons.append("Price at Fibonacci support")

        # Deep ITM candidate
        deep_strike = self._find_deep_itm_strike(calls)
        if deep_strike is not None:
            deep_contract = self._find_contract(calls, deep_strike, OptionType.CALL)
            if deep_contract:
                cost_per = round(deep_contract.mid * 100, 2) if deep_contract.mid > 0 else round(
                    (deep_contract.bid + deep_contract.ask) / 2 * 100, 2
                )
                candidates.append(LeapCandidate(
                    symbol=symbol,
                    current_price=current_price,
                    rsi=round(rsi, 2),
                    at_support=at_support,
                    suggested_strike=deep_strike,
                    suggested_expiry=expiry,
                    style=LeapStyle.DEEP_ITM,
                    estimated_cost=cost_per,
                    rationale=(
                        f"Deep ITM LEAP (stock replacement). "
                        f"{'; '.join(entry_reasons)}. "
                        f"Strike ${deep_strike} gives delta > 0.80 — "
                        f"moves nearly 1:1 with {symbol} at ~{cost_per / current_price / 100:.0%} "
                        f"of stock cost."
                    ),
                ))

        # Speculative OTM candidate
        spec_strike = self._find_speculative_strike(calls)
        if spec_strike is not None:
            spec_contract = self._find_contract(calls, spec_strike, OptionType.CALL)
            if spec_contract:
                cost_per = round(spec_contract.mid * 100, 2) if spec_contract.mid > 0 else round(
                    (spec_contract.bid + spec_contract.ask) / 2 * 100, 2
                )
                candidates.append(LeapCandidate(
                    symbol=symbol,
                    current_price=current_price,
                    rsi=round(rsi, 2),
                    at_support=at_support,
                    suggested_strike=spec_strike,
                    suggested_expiry=expiry,
                    style=LeapStyle.SPECULATIVE_OTM,
                    estimated_cost=cost_per,
                    rationale=(
                        f"Speculative OTM LEAP (lottery ticket). "
                        f"{'; '.join(entry_reasons)}. "
                        f"Strike ${spec_strike} with delta 0.20-0.40 — "
                        f"asymmetric upside if {symbol} rips. "
                        f"Max risk: ${cost_per} per contract."
                    ),
                ))

        return candidates

    # ── Private: Position Monitoring ─────────────────────────────

    async def _check_single_position(self, pos: LeapPosition) -> list[LeapAlert]:
        """Check a single position and update its market data."""
        alerts: list[LeapAlert] = []

        # Fetch current option data
        chain = await self.tradier.get_option_chain(pos.symbol, pos.expiry, greeks=True)
        contract = self._find_contract(chain, pos.strike, pos.option_type)

        if not contract:
            # Option may have been delisted or expired
            logger.warning("leap_contract_not_found", id=pos.id, symbol=pos.symbol)
            return alerts

        # Update position market data
        mid = contract.mid if contract.mid > 0 else round((contract.bid + contract.ask) / 2, 2)
        pos.current_price = mid
        pos.current_delta = contract.greeks.delta if contract.greeks else 0.0

        # Calculate unrealized P&L
        cost_basis = pos.entry_price * 100 * pos.quantity
        current_value = pos.current_price * 100 * pos.quantity
        pos.unrealized_pnl = round(current_value - cost_basis, 2)
        pos.unrealized_pnl_pct = round(
            pos.unrealized_pnl / cost_basis if cost_basis > 0 else 0.0, 4
        )

        # Check delta erosion on Deep ITM
        if pos.style == LeapStyle.DEEP_ITM and pos.current_delta < self.DEEP_ITM_MIN_DELTA:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.DELTA_EROSION,
                message=(
                    f"⚠️ {pos.symbol} Deep ITM LEAP delta dropped to {pos.current_delta:.2f} "
                    f"(below {self.DEEP_ITM_MIN_DELTA}). Losing stock-replacement edge."
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    "Roll down to a lower strike to restore delta > 0.80, "
                    "or close if thesis is broken."
                ),
            ))

        # Check for trim opportunity (+200%+)
        if pos.unrealized_pnl_pct >= self.BIG_WINNER_PCT:
            auto_trim_pct = self._calculate_auto_trim_pct(pos)
            if auto_trim_pct > 0:
                alerts.append(LeapAlert(
                    position_id=pos.id,
                    symbol=pos.symbol,
                    alert_type=AlertType.TRIM_OPPORTUNITY,
                    message=(
                        f"🎯 {pos.symbol} LEAP up {pos.unrealized_pnl_pct:.0%}! "
                        f"Consider trimming {auto_trim_pct:.0%} ({int(pos.quantity * auto_trim_pct)} contracts). "
                        f"Entry: ${pos.entry_price:.2f} → Now: ${pos.current_price:.2f}"
                    ),
                    current_price=pos.current_price,
                    current_delta=pos.current_delta,
                    pnl_pct=pos.unrealized_pnl_pct,
                    suggested_action=f"Trim {auto_trim_pct:.0%} to lock in gains, let the rest ride.",
                ))

        # Check for thesis review (-30%+)
        if pos.unrealized_pnl_pct <= self.BIG_LOSER_PCT:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.THESIS_REVIEW,
                message=(
                    f"🔴 {pos.symbol} LEAP down {pos.unrealized_pnl_pct:.0%}. "
                    f"Entry: ${pos.entry_price:.2f} → Now: ${pos.current_price:.2f}. "
                    f"Review thesis — is the setup still valid?"
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    "Re-evaluate: Has the fundamental thesis changed? "
                    "If yes, cut the loss. If no, consider adding at lower prices."
                ),
            ))

        # Check expiry proximity
        days_to_expiry = (date.fromisoformat(pos.expiry) - date.today()).days
        if 0 < days_to_expiry <= 90:
            alerts.append(LeapAlert(
                position_id=pos.id,
                symbol=pos.symbol,
                alert_type=AlertType.EXPIRY_WARNING,
                message=(
                    f"⏰ {pos.symbol} LEAP expires in {days_to_expiry} days ({pos.expiry}). "
                    f"Theta decay accelerating — roll or close."
                ),
                current_price=pos.current_price,
                current_delta=pos.current_delta,
                pnl_pct=pos.unrealized_pnl_pct,
                suggested_action=(
                    f"Roll to next January expiry to maintain time value, "
                    f"or close if P&L is acceptable ({pos.unrealized_pnl_pct:.0%})."
                ),
            ))
        elif days_to_expiry <= 0:
            pos.active = False
            logger.warning("leap_expired", id=pos.id, symbol=pos.symbol, expiry=pos.expiry)

        return alerts

    # ── Private: RSI Calculation ─────────────────────────────────

    @staticmethod
    def _calculate_rsi(bars: list[Bar], period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index) from price bars.

        Uses the Wilder smoothing method (exponential moving average of
        gains and losses).

        Args:
            bars: List of OHLCV bars (must have at least period+1 bars).
            period: RSI lookback period. Default 14.

        Returns:
            RSI value between 0 and 100.
        """
        if len(bars) < period + 1:
            return 50.0  # Not enough data, return neutral

        closes = [b.close for b in bars]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Initial average gain/loss over first `period` changes
        gains = [d if d > 0 else 0.0 for d in deltas[:period]]
        losses = [-d if d < 0 else 0.0 for d in deltas[:period]]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        # Wilder smoothing for remaining periods
        for i in range(period, len(deltas)):
            d = deltas[i]
            current_gain = d if d > 0 else 0.0
            current_loss = -d if d < 0 else 0.0

            avg_gain = (avg_gain * (period - 1) + current_gain) / period
            avg_loss = (avg_loss * (period - 1) + current_loss) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return round(rsi, 2)

    # ── Private: Fibonacci Support Detection ─────────────────────

    def _check_fibonacci_support(self, bars: list[Bar]) -> bool:
        """Check if the current price is near a Fibonacci retracement support level.

        Uses the highest high and lowest low of the lookback period to
        calculate Fibonacci retracement levels, then checks if the current
        price is within FIB_TOLERANCE of any of them.

        Args:
            bars: Daily OHLCV bars (uses last 100 bars for swing range).

        Returns:
            True if price is at or near a Fibonacci support level.
        """
        lookback = bars[-100:] if len(bars) >= 100 else bars
        current_price = bars[-1].close

        swing_high = max(b.high for b in lookback)
        swing_low = min(b.low for b in lookback)
        swing_range = swing_high - swing_low

        if swing_range <= 0:
            return False

        for fib in self.FIB_LEVELS:
            # Retracement level = high - (range * fib)
            fib_price = swing_high - (swing_range * fib)
            distance_pct = abs(current_price - fib_price) / current_price

            if distance_pct <= self.FIB_TOLERANCE:
                logger.debug(
                    "leap_fib_support_found",
                    fib_level=fib,
                    fib_price=round(fib_price, 2),
                    current_price=current_price,
                    distance_pct=round(distance_pct, 4),
                )
                return True

        return False

    # ── Private: Strike Selection ────────────────────────────────

    @staticmethod
    def _find_deep_itm_strike(
        chain: list[OptionQuote], target_delta: float = 0.80
    ) -> float | None:
        """Find the best deep ITM call strike with delta >= target.

        Picks the strike closest to the target delta from above — we want
        at least target_delta but not so deep that we're paying pure
        intrinsic with no leverage benefit.

        Args:
            chain: List of call OptionQuotes with greeks.
            target_delta: Minimum delta threshold.

        Returns:
            Strike price, or None if no suitable strike found.
        """
        candidates = []
        for opt in chain:
            if opt.option_type != OptionType.CALL:
                continue
            if not opt.greeks or opt.greeks.delta <= 0:
                continue
            if opt.greeks.delta >= target_delta:
                candidates.append(opt)

        if not candidates:
            return None

        # Sort by delta ascending — pick the one closest to target_delta
        # (least deep ITM that still meets the threshold)
        candidates.sort(key=lambda o: o.greeks.delta)  # type: ignore[union-attr]
        best = candidates[0]

        # Ensure there's actual liquidity
        if best.bid <= 0 or best.open_interest < 10:
            # Try the next candidate
            for c in candidates[1:]:
                if c.bid > 0 and c.open_interest >= 10:
                    return c.strike
            return None

        return best.strike

    @staticmethod
    def _find_speculative_strike(
        chain: list[OptionQuote],
        min_delta: float = 0.20,
        max_delta: float = 0.40,
    ) -> float | None:
        """Find the best speculative OTM call strike with delta in range.

        Picks the strike closest to the midpoint of the delta range for
        the best risk/reward balance.

        Args:
            chain: List of call OptionQuotes with greeks.
            min_delta: Minimum delta for the range.
            max_delta: Maximum delta for the range.

        Returns:
            Strike price, or None if no suitable strike found.
        """
        mid_delta = (min_delta + max_delta) / 2
        candidates = []

        for opt in chain:
            if opt.option_type != OptionType.CALL:
                continue
            if not opt.greeks or opt.greeks.delta <= 0:
                continue
            if min_delta <= opt.greeks.delta <= max_delta:
                candidates.append(opt)

        if not candidates:
            return None

        # Sort by distance from midpoint delta
        candidates.sort(key=lambda o: abs(o.greeks.delta - mid_delta))  # type: ignore[union-attr]

        # Prefer liquid contracts
        for c in candidates:
            if c.bid > 0 and c.open_interest >= 5:
                return c.strike

        # Fallback: take best delta match even if less liquid
        return candidates[0].strike if candidates else None

    # ── Private: Expiry Selection ────────────────────────────────

    async def _find_january_expiry(self, symbol: str) -> str | None:
        """Find the next January expiration that's at least 9 months out.

        Prefers January expirations for maximum liquidity and standard
        LEAP dates. Falls back to the furthest available expiry if no
        January date qualifies.

        Args:
            symbol: Underlying ticker.

        Returns:
            Expiration date string (YYYY-MM-DD), or None.
        """
        expirations = await self.tradier.get_option_expirations(symbol)
        if not expirations:
            return None

        today = date.today()
        min_expiry_date = today + timedelta(days=self.MIN_DTE)

        # Look for January expirations first
        january_candidates: list[str] = []
        all_valid: list[str] = []

        for exp_str in expirations:
            try:
                exp_date = date.fromisoformat(exp_str)
            except ValueError:
                continue

            if exp_date < min_expiry_date:
                continue

            all_valid.append(exp_str)

            # January = month 1, typically the 15th or 17th (3rd Friday)
            if exp_date.month == 1:
                january_candidates.append(exp_str)

        if january_candidates:
            # Pick the nearest January that qualifies
            january_candidates.sort()
            return january_candidates[0]

        if all_valid:
            # No January — pick the furthest out expiry
            all_valid.sort(reverse=True)
            return all_valid[0]

        return None

    # ── Private: Helpers ─────────────────────────────────────────

    @staticmethod
    def _find_contract(
        chain: list[OptionQuote], strike: float, option_type: OptionType
    ) -> OptionQuote | None:
        """Find a specific contract in an option chain by strike and type."""
        for opt in chain:
            if opt.strike == strike and opt.option_type == option_type:
                return opt
        return None

    def _calculate_auto_trim_pct(self, pos: LeapPosition) -> float:
        """Determine auto-trim percentage based on position P&L and trim history.

        Trim schedule:
        - +200%: trim 25% (first trim)
        - +400%: trim 25% (second trim)
        - Remaining 50%: let it ride

        Returns:
            Trim percentage (0.0 if no trim warranted).
        """
        trims_done = len(pos.trim_history)

        if pos.unrealized_pnl_pct >= self.TRIM_2_GAIN_PCT and trims_done < 2:
            return self.TRIM_2_SIZE_PCT

        if pos.unrealized_pnl_pct >= self.TRIM_1_GAIN_PCT and trims_done < 1:
            return self.TRIM_1_SIZE_PCT

        return 0.0

    async def _get_option_price(self, option_symbol: str) -> float | None:
        """Fetch current mid price for an option contract."""
        try:
            quotes = await self.tradier.get_quotes([option_symbol])
            if quotes:
                q = quotes[0]
                mid = round((q.bid + q.ask) / 2, 2)
                return mid if mid > 0 else q.last
        except Exception as e:
            logger.error("leap_price_fetch_failed", option=option_symbol, error=str(e))
        return None

    # ── State Persistence ────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist all LEAP positions to JSON file."""
        LEAP_PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "updated_at": datetime.now().isoformat(),
            "positions": {
                pid: pos.model_dump(mode="json")
                for pid, pos in self._positions.items()
            },
        }

        with open(LEAP_PORTFOLIO_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.debug("leap_state_saved", positions=len(self._positions), path=str(LEAP_PORTFOLIO_PATH))

    def _load_state(self) -> None:
        """Load LEAP positions from JSON file if it exists."""
        if not LEAP_PORTFOLIO_PATH.exists():
            logger.info("leap_no_saved_state", path=str(LEAP_PORTFOLIO_PATH))
            return

        try:
            with open(LEAP_PORTFOLIO_PATH) as f:
                data = json.load(f)

            positions_data = data.get("positions", {})
            for pid, pos_dict in positions_data.items():
                self._positions[pid] = LeapPosition.model_validate(pos_dict)

            logger.info(
                "leap_state_loaded",
                positions=len(self._positions),
                active=sum(1 for p in self._positions.values() if p.active),
            )
        except Exception as e:
            logger.error("leap_state_load_failed", error=str(e), path=str(LEAP_PORTFOLIO_PATH))


#===============================================================================
# SECTION: EXECUTION - Swing Strategy
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/execution/swing.py (600 lines)
#-------------------------------------------------------------------------------

"""Swing Position Manager — Multi-Day Positions Held Overnight or Over Weekends.

Handles positions that span multiple trading days, unlike the core 0DTE focus.
Swing positions are used for:
    - Multi-day directional plays based on strong bias + flow confirmation
    - FOMC/CPI positioning (enter before, hold through event)
    - Death cross bearish plays that need time to develop
    - Friday→Monday weekend swings when conviction is high

Risk Budget:
    - Max 10% of account in overnight positions
    - Each swing has explicit thesis, target, and stop
    - Separate tracking from intraday 0DTE positions

Weekend Swing Logic:
    - Only hold over weekend if Friday bias is STRONG (>70 confidence)
    - Flow must confirm direction
    - Use smaller size (50% of normal swing size)
    - Always use defined risk (spreads, not naked)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, time
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from esther.core.config import config
from esther.data.tradier import TradierClient

logger = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class SwingStatus(str, Enum):
    """Status of a swing position."""

    OPEN = "OPEN"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_EXPIRY = "CLOSED_EXPIRY"
    CLOSED_THESIS_BROKEN = "CLOSED_THESIS_BROKEN"


class SwingSide(str, Enum):
    """Direction of the swing trade."""

    LONG = "LONG"
    SHORT = "SHORT"


class SwingPosition(BaseModel):
    """A multi-day swing position with thesis tracking.

    Unlike 0DTE positions, swings have:
    - A thesis (why we're holding overnight)
    - Overnight P&L tracking
    - Multi-day duration tracking
    - Explicit expiration (the option expiry, not just today)
    """

    id: str = Field(default_factory=lambda: f"swing_{uuid.uuid4().hex[:8]}")
    symbol: str  # underlying (e.g., "SPX", "AAPL")
    option_symbol: str  # specific option contract
    side: SwingSide
    quantity: int
    thesis: str  # why we're holding (e.g., "FOMC positioning", "death cross bearish")

    # Entry
    entry_date: date = Field(default_factory=date.today)
    entry_price: float = 0.0
    entry_time: datetime = Field(default_factory=datetime.now)

    # Current state
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    pnl_pct: float = 0.0

    # Targets and stops
    target: float = 0.0  # price target
    stop: float = 0.0  # stop loss price
    expiration: date | None = None  # option expiration date

    # Tracking
    status: SwingStatus = SwingStatus.OPEN
    close_date: date | None = None
    close_price: float = 0.0
    close_reason: str = ""

    # Overnight tracking
    daily_closes: list[float] = []  # closing price each day
    overnight_pnl: float = 0.0  # P&L from overnight gap
    total_days_held: int = 0

    # Weekend swing specific
    is_weekend_swing: bool = False
    weekend_direction: str = ""  # BULL or BEAR
    weekend_confidence: float = 0.0

    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    tags: list[str] = []  # e.g., ["fomc", "death_cross", "weekend"]


class SwingPortfolio(BaseModel):
    """Summary of all swing positions."""

    total_positions: int = 0
    total_exposure: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_overnight_pnl: float = 0.0
    positions: list[SwingPosition] = []
    weekend_swings: int = 0
    avg_days_held: float = 0.0
    account_pct_used: float = 0.0  # % of account in swings


class SwingManager:
    """Manages multi-day swing positions.

    Swing positions are held overnight or over multiple days, unlike
    the core 0DTE strategy. They have their own risk budget (max 10%
    of account) and explicit thesis tracking.

    Key methods:
        open_swing() — open a new swing position
        check_swings() — check all swings against targets/stops
        close_swing() — close a specific swing
        get_overnight_pnl() — calculate P/L from overnight holds
        weekend_swing() — open a Friday→Monday swing
    """

    MAX_SWING_PCT = 0.10  # Max 10% of account in swings
    WEEKEND_SIZE_MULTIPLIER = 0.50  # Weekend swings are 50% of normal size
    MIN_WEEKEND_CONFIDENCE = 70  # Minimum confidence for weekend swings

    def __init__(self, client: TradierClient, account_balance: float):
        self.client = client
        self.account_balance = account_balance
        self._swings: dict[str, SwingPosition] = {}
        self._closed_swings: list[SwingPosition] = []

    @property
    def open_swings(self) -> list[SwingPosition]:
        """All currently open swing positions."""
        return [s for s in self._swings.values() if s.status == SwingStatus.OPEN]

    @property
    def total_exposure(self) -> float:
        """Total dollar exposure in swing positions."""
        return sum(
            s.entry_price * 100 * s.quantity
            for s in self.open_swings
        )

    @property
    def exposure_pct(self) -> float:
        """Swing exposure as percentage of account."""
        return self.total_exposure / self.account_balance if self.account_balance > 0 else 0.0

    def open_swing(
        self,
        symbol: str,
        option_symbol: str,
        side: SwingSide | str,
        quantity: int,
        thesis: str,
        target: float,
        stop: float,
        expiration: date | str,
        entry_price: float | None = None,
        tags: list[str] | None = None,
    ) -> SwingPosition | None:
        """Open a new swing position.

        Checks swing risk budget before opening. Max 10% of account.

        Args:
            symbol: Underlying symbol (e.g., "SPX").
            option_symbol: Specific option contract symbol.
            side: LONG or SHORT.
            quantity: Number of contracts.
            thesis: Why we're taking this swing (e.g., "FOMC positioning").
            target: Price target for the option.
            stop: Stop loss price for the option.
            expiration: Option expiration date.
            entry_price: Entry price (if None, will fetch from market).
            tags: Optional tags for categorization.

        Returns:
            SwingPosition if opened, None if rejected by risk check.
        """
        if isinstance(side, str):
            side = SwingSide(side.upper())
        if isinstance(expiration, str):
            expiration = date.fromisoformat(expiration)

        # Risk check: would this exceed 10% swing budget?
        estimated_exposure = (entry_price or 0) * 100 * quantity
        if self.total_exposure + estimated_exposure > self.account_balance * self.MAX_SWING_PCT:
            logger.warning(
                "swing_rejected_risk_budget",
                symbol=symbol,
                current_exposure=self.total_exposure,
                new_exposure=estimated_exposure,
                max_allowed=self.account_balance * self.MAX_SWING_PCT,
            )
            return None

        swing = SwingPosition(
            symbol=symbol,
            option_symbol=option_symbol,
            side=side,
            quantity=quantity,
            thesis=thesis,
            entry_price=entry_price or 0.0,
            current_price=entry_price or 0.0,
            target=target,
            stop=stop,
            expiration=expiration,
            tags=tags or [],
        )

        self._swings[swing.id] = swing

        logger.info(
            "swing_opened",
            id=swing.id,
            symbol=symbol,
            option=option_symbol,
            side=side.value,
            qty=quantity,
            thesis=thesis,
            target=target,
            stop=stop,
            expiration=expiration.isoformat(),
        )

        return swing

    async def check_swings(self) -> list[SwingPosition]:
        """Check all open swings against targets, stops, and expiration.

        For each open swing:
        1. Fetch current price
        2. Update P&L
        3. Check if target hit → close for profit
        4. Check if stop hit → close for loss
        5. Check if approaching expiration → alert or close
        6. Check if thesis is still valid

        Returns:
            List of swings that were closed this check.
        """
        closed_this_check: list[SwingPosition] = []

        for swing in list(self.open_swings):
            try:
                # Fetch current price
                current_price = await self._get_current_price(swing.option_symbol)
                if current_price is None:
                    continue

                # Update state
                swing.current_price = current_price
                swing.total_days_held = (date.today() - swing.entry_date).days

                # Calculate P&L
                if swing.side == SwingSide.LONG:
                    swing.unrealized_pnl = round(
                        (current_price - swing.entry_price) * 100 * swing.quantity, 2
                    )
                else:
                    swing.unrealized_pnl = round(
                        (swing.entry_price - current_price) * 100 * swing.quantity, 2
                    )

                swing.pnl_pct = (
                    swing.unrealized_pnl / (swing.entry_price * 100 * swing.quantity)
                    if swing.entry_price > 0 else 0.0
                )

                # Check target
                if swing.side == SwingSide.LONG and current_price >= swing.target:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_TARGET,
                        f"TARGET_HIT: Price {current_price:.2f} >= target {swing.target:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                if swing.side == SwingSide.SHORT and current_price <= swing.target:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_TARGET,
                        f"TARGET_HIT: Price {current_price:.2f} <= target {swing.target:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                # Check stop
                if swing.side == SwingSide.LONG and current_price <= swing.stop:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_STOP,
                        f"STOP_HIT: Price {current_price:.2f} <= stop {swing.stop:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                if swing.side == SwingSide.SHORT and current_price >= swing.stop:
                    await self._close_swing(
                        swing,
                        SwingStatus.CLOSED_STOP,
                        f"STOP_HIT: Price {current_price:.2f} >= stop {swing.stop:.2f}",
                    )
                    closed_this_check.append(swing)
                    continue

                # Check expiration proximity
                if swing.expiration:
                    days_to_expiry = (swing.expiration - date.today()).days
                    if days_to_expiry <= 0:
                        await self._close_swing(
                            swing,
                            SwingStatus.CLOSED_EXPIRY,
                            f"EXPIRY: Option expires today ({swing.expiration})",
                        )
                        closed_this_check.append(swing)
                        continue

                    if days_to_expiry == 1:
                        logger.warning(
                            "swing_expiring_tomorrow",
                            id=swing.id,
                            symbol=swing.symbol,
                            pnl=swing.unrealized_pnl,
                        )

            except Exception as e:
                logger.error(
                    "swing_check_failed",
                    id=swing.id,
                    symbol=swing.symbol,
                    error=str(e),
                )

        return closed_this_check

    async def close_swing(self, swing_id: str, reason: str = "MANUAL") -> SwingPosition | None:
        """Close a specific swing position.

        Args:
            swing_id: ID of the swing to close.
            reason: Why we're closing.

        Returns:
            The closed SwingPosition, or None if not found.
        """
        swing = self._swings.get(swing_id)
        if not swing or swing.status != SwingStatus.OPEN:
            logger.warning("swing_not_found_or_closed", id=swing_id)
            return None

        await self._close_swing(swing, SwingStatus.CLOSED_MANUAL, reason)
        return swing

    async def _close_swing(
        self,
        swing: SwingPosition,
        status: SwingStatus,
        reason: str,
    ) -> None:
        """Internal method to close a swing position.

        Submits the closing order to Tradier and updates tracking.
        """
        # Submit closing order
        close_side = "sell_to_close" if swing.side == SwingSide.LONG else "buy_to_close"

        try:
            await self.client.place_order(
                symbol=swing.symbol,
                option_symbol=swing.option_symbol,
                side=close_side,
                quantity=swing.quantity,
                order_type="market",
            )
        except Exception as e:
            logger.error(
                "swing_close_order_failed",
                id=swing.id,
                error=str(e),
            )

        swing.status = status
        swing.close_date = date.today()
        swing.close_price = swing.current_price
        swing.close_reason = reason

        # Move to closed list
        self._closed_swings.append(swing)
        if swing.id in self._swings:
            del self._swings[swing.id]

        logger.info(
            "swing_closed",
            id=swing.id,
            symbol=swing.symbol,
            status=status.value,
            pnl=swing.unrealized_pnl,
            days_held=swing.total_days_held,
            thesis=swing.thesis,
            reason=reason,
        )

    def get_overnight_pnl(self) -> float:
        """Calculate total P/L from overnight holds.

        Compares current price to previous day's close for each swing.

        Returns:
            Total overnight P&L in dollars.
        """
        total_overnight = 0.0
        for swing in self.open_swings:
            if swing.daily_closes:
                prev_close = swing.daily_closes[-1]
                if swing.side == SwingSide.LONG:
                    overnight = (swing.current_price - prev_close) * 100 * swing.quantity
                else:
                    overnight = (prev_close - swing.current_price) * 100 * swing.quantity
                swing.overnight_pnl = round(overnight, 2)
                total_overnight += overnight

        return round(total_overnight, 2)

    def record_daily_close(self) -> None:
        """Record today's closing prices for all open swings.

        Call this at EOD to track overnight gaps.
        """
        for swing in self.open_swings:
            swing.daily_closes.append(swing.current_price)
            logger.info(
                "swing_daily_close_recorded",
                id=swing.id,
                symbol=swing.symbol,
                close_price=swing.current_price,
                days_held=swing.total_days_held,
            )

    async def weekend_swing(
        self,
        symbol: str,
        option_symbol: str,
        direction: str,
        confidence: float,
        quantity: int,
        entry_price: float,
        target: float,
        stop: float,
        expiration: date | str,
        thesis: str = "",
    ) -> SwingPosition | None:
        """Open a Friday→Monday weekend swing position.

        Weekend swings have stricter requirements:
        - Confidence must be >= 70 (MIN_WEEKEND_CONFIDENCE)
        - Size is reduced by 50%
        - Only uses defined-risk strategies
        - Must have strong flow confirmation (passed in as confidence)

        Args:
            symbol: Underlying symbol.
            option_symbol: Option contract.
            direction: "BULL" or "BEAR".
            confidence: Confidence level (0-100). Must be >= 70.
            quantity: Base quantity (will be halved for weekend sizing).
            entry_price: Entry price per contract.
            target: Price target.
            stop: Stop loss.
            expiration: Option expiration.
            thesis: Thesis for the weekend hold.

        Returns:
            SwingPosition if opened, None if rejected.
        """
        # Check it's actually Friday
        now_et = datetime.now(ET)
        if now_et.weekday() != 4:  # 4 = Friday
            logger.warning(
                "weekend_swing_not_friday",
                day=now_et.strftime("%A"),
            )
            # Allow it but log warning — could be pre-positioning on Thursday

        # Confidence check
        if confidence < self.MIN_WEEKEND_CONFIDENCE:
            logger.info(
                "weekend_swing_rejected_confidence",
                symbol=symbol,
                confidence=confidence,
                min_required=self.MIN_WEEKEND_CONFIDENCE,
            )
            return None

        # Reduce size for weekend risk
        weekend_qty = max(1, int(quantity * self.WEEKEND_SIZE_MULTIPLIER))

        if not thesis:
            thesis = f"Weekend {direction} swing — confidence {confidence:.0f}%"

        side = SwingSide.LONG if direction == "BULL" else SwingSide.SHORT

        swing = self.open_swing(
            symbol=symbol,
            option_symbol=option_symbol,
            side=side,
            quantity=weekend_qty,
            thesis=thesis,
            target=target,
            stop=stop,
            expiration=expiration,
            entry_price=entry_price,
            tags=["weekend", direction.lower()],
        )

        if swing:
            swing.is_weekend_swing = True
            swing.weekend_direction = direction
            swing.weekend_confidence = confidence

            logger.info(
                "weekend_swing_opened",
                id=swing.id,
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                original_qty=quantity,
                weekend_qty=weekend_qty,
                thesis=thesis,
            )

        return swing

    def get_swing_portfolio(self) -> SwingPortfolio:
        """Get a summary of all swing positions.

        Returns:
            SwingPortfolio with aggregated metrics.
        """
        positions = self.open_swings
        total_unrealized = sum(s.unrealized_pnl for s in positions)
        total_overnight = sum(s.overnight_pnl for s in positions)
        total_exposure = self.total_exposure
        weekend_count = sum(1 for s in positions if s.is_weekend_swing)
        avg_days = (
            sum(s.total_days_held for s in positions) / len(positions)
            if positions else 0.0
        )

        return SwingPortfolio(
            total_positions=len(positions),
            total_exposure=round(total_exposure, 2),
            total_unrealized_pnl=round(total_unrealized, 2),
            total_overnight_pnl=round(total_overnight, 2),
            positions=positions,
            weekend_swings=weekend_count,
            avg_days_held=round(avg_days, 1),
            account_pct_used=round(self.exposure_pct * 100, 2),
        )

    async def _get_current_price(self, option_symbol: str) -> float | None:
        """Fetch current mid price for an option contract."""
        try:
            quotes = await self.client.get_quotes([option_symbol])
            if quotes:
                q = quotes[0]
                return round((q.bid + q.ask) / 2, 2)
        except Exception as e:
            logger.error(
                "swing_price_fetch_failed",
                option=option_symbol,
                error=str(e),
            )
        return None

    def update_account_balance(self, new_balance: float) -> None:
        """Update the account balance for risk calculations."""
        self.account_balance = new_balance


#===============================================================================
# SECTION: RISK - Risk Manager
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/risk/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: esther/risk/risk_manager.py (934 lines)
#-------------------------------------------------------------------------------

"""Risk Manager — Position Limits, Daily Loss Caps, PDT Mode, and Advanced Risk Rules.

The last line of defense before capital destruction. Enforces:

    - Per-tier position limits (T1: 5, T2: 3, T3: 3)
    - Linear scaling by account size (10→25→50→100 spreads max)
    - Daily loss cap: 5% of account value → shut down for the day
    - Cooldown: 2 consecutive losses on same ticker → 30 min pause
    - PDT Mode: Under $25K, limit to 3 day trades per 5 rolling days
    - Event day sizing: Reduce by 50% on FOMC/CPI/PPI days
    - Multi-pillar risk: Aggregate risk per ticker, max 3% combined
    - Tiered stop risk calculation
    - Swing position risk: Max 10% of account in overnight positions
    - Daily stats tracking (win rate, avg win/loss, bad trade %)
    - Force-close: triggered by Black Swan RED or daily cap hit

Every trade request passes through the Risk Manager before execution.
If the Risk Manager says no, the trade doesn't happen. Period.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field

from esther.core.config import config
from esther.execution.position_manager import Position, PositionManager, PositionStatus

logger = structlog.get_logger(__name__)


# ── Economic Calendar Events ────────────────────────────────────

# Major economic events that warrant reduced sizing
EVENT_KEYWORDS = {"FOMC", "CPI", "PPI", "NFP", "JOBS", "GDP", "PCE", "RETAIL_SALES"}


class DayTrade(BaseModel):
    """Record of a single day trade for PDT tracking."""

    symbol: str
    date: date
    was_credit_spread_expiry: bool = False  # Credit spreads that expire don't count


class DailyStats(BaseModel):
    """Daily trading statistics — the 4 key metrics from @SuperLuckeee.

    Lever 1: Win Rate (increase by taking A+ setups only)
    Lever 2: Average Win (increase by letting winners run)
    Lever 3: Average Loss (decrease with hard stops)
    Lever 4: Bad Trade % (decrease by eliminating rule violations)
    """

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_losses: float = 0.0
    max_loss_trades: int = 0  # Trades that hit max loss
    rule_violations: int = 0  # Trades that violated trading rules
    bad_trades_detail: list[str] = []  # WHY each trade was bad

    @property
    def win_rate(self) -> float:
        """Win Rate = Wins / Total Trades."""
        return self.winning_trades / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def average_win(self) -> float:
        """Average Win = Total Profit / Winning Trades."""
        return self.total_profit / self.winning_trades if self.winning_trades > 0 else 0.0

    @property
    def average_loss(self) -> float:
        """Average Loss = Total Losses / Losing Trades."""
        return self.total_losses / self.losing_trades if self.losing_trades > 0 else 0.0

    @property
    def bad_trade_pct(self) -> float:
        """Bad Trade % = Trades that hit max loss / Total Trades."""
        return self.max_loss_trades / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        """Profit Factor = Total Profit / |Total Losses|."""
        return abs(self.total_profit / self.total_losses) if self.total_losses != 0 else float("inf")


class AccountTier(BaseModel):
    """Account tier for linear scaling rules."""

    tier_name: str
    min_balance: float
    max_spreads: int


class RiskCheck(BaseModel):
    """Result of a risk assessment."""

    approved: bool
    reason: str = ""
    current_positions: int = 0
    max_positions: int = 0
    daily_pnl: float = 0.0
    daily_loss_cap: float = 0.0
    cooldown_active: bool = False
    cooldown_until: datetime | None = None
    pdt_trades_remaining: int = -1  # -1 means PDT not active
    event_day_reduction: bool = False
    account_tier: str = ""


class DailyRiskReport(BaseModel):
    """End-of-day risk summary with expanded stats."""

    date: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    bad_trade_pct: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_positions: int = 0
    risk_events: list[str] = []
    shutdown_triggered: bool = False
    force_closes: int = 0
    day_trades_used: int = 0
    swing_positions_held: int = 0
    overnight_exposure: float = 0.0
    account_tier: str = ""
    max_spreads_allowed: int = 0


class RiskManager:
    """Enforces all risk limits and tracks daily risk metrics.

    Pre-trade checks (can_open_position):
    1. Is the daily loss cap hit? → Reject all trades
    2. PDT check: Under $25K and 3+ day trades in 5 days? → Reject
    3. Is this ticker on cooldown? → Reject
    4. Is the tier at max positions? → Reject
    5. Linear scaling: within account tier limits? → Reject
    6. Multi-pillar risk: combined risk per ticker under 3%? → Reject
    7. Event day: is it FOMC/CPI/PPI? → Reduce size by 50%
    8. Swing risk: overnight exposure under 10%? → Reject swing trades
    9. Is the total risk within bounds? → Reject

    Post-trade tracking:
    - Update consecutive loss counters
    - Track daily P&L watermark
    - Track the 4 key stats
    - Generate end-of-day risk reports
    """

    # Account tier thresholds for linear scaling
    # Configured for $10K/day target with compounding
    # $80K start → medium tier → scale to large as profits compound
    ACCOUNT_TIERS = [
        AccountTier(tier_name="micro", min_balance=0, max_spreads=10),
        AccountTier(tier_name="small", min_balance=10_000, max_spreads=25),
        AccountTier(tier_name="medium", min_balance=50_000, max_spreads=60),   # bumped from 50
        AccountTier(tier_name="large", min_balance=150_000, max_spreads=100),  # lowered from 200K
        AccountTier(tier_name="whale", min_balance=500_000, max_spreads=200),  # new tier
    ]

    def __init__(self, position_manager: PositionManager, account_balance: float, risk_cfg=None):
        self.pm = position_manager
        self.account_balance = account_balance
        self._cfg = risk_cfg if risk_cfg is not None else config().risk

        # Daily state
        self._daily_pnl_peak: float = 0.0
        self._daily_max_drawdown: float = 0.0
        self._shutdown: bool = False
        self._shutdown_reason: str = ""

        # Cooldown tracking: {symbol: (consecutive_losses, cooldown_until)}
        self._cooldowns: dict[str, tuple[int, datetime | None]] = {}

        # Recent loser tracking: {symbol: last_loss_time} — no re-entry for 30 min
        self._recent_losers: dict[str, datetime] = {}

        # PDT tracking: rolling 5-day window of day trades
        self._day_trades: list[DayTrade] = []

        # Event day state
        self._is_event_day: bool = False
        self._event_name: str = ""

        # Daily stats tracking
        self._daily_stats = DailyStats()

        # Metrics
        self._risk_events: list[str] = []
        self._peak_positions: int = 0

    @property
    def is_shutdown(self) -> bool:
        """Whether trading is shut down for the day."""
        return self._shutdown

    @property
    def daily_loss_cap(self) -> float:
        """Maximum allowable daily loss in dollars."""
        return self.account_balance * self._cfg.daily_loss_cap_pct

    @property
    def daily_stats(self) -> DailyStats:
        """Current daily statistics."""
        return self._daily_stats

    def get_account_tier(self) -> AccountTier:
        """Determine current account tier for linear scaling.

        Returns the tier matching the current account balance.
        Tiers:
            Under $10K: 10 spreads max
            $10K-$50K: 25 spreads max
            $50K-$200K: 50 spreads max
            Over $200K: 100 spreads max
        """
        current_tier = self.ACCOUNT_TIERS[0]
        for tier in self.ACCOUNT_TIERS:
            if self.account_balance >= tier.min_balance:
                current_tier = tier
        return current_tier

    def get_max_spreads(self) -> int:
        """Get maximum number of spreads allowed for current account size."""
        return self.get_account_tier().max_spreads

    # ── PDT Tracking ─────────────────────────────────────────────

    def is_pdt_restricted(self) -> bool:
        """Check if the account is subject to PDT rules (under $25K)."""
        return self.account_balance < 25_000

    def get_pdt_trades_remaining(self) -> int:
        """Get number of day trades remaining in the 5-day window.

        Returns -1 if PDT rules don't apply (account >= $25K).
        """
        if not self.is_pdt_restricted():
            return -1

        # Count day trades in the last 5 rolling business days
        cutoff = date.today() - timedelta(days=5)
        recent_trades = [
            dt for dt in self._day_trades
            if dt.date >= cutoff and not dt.was_credit_spread_expiry
        ]
        return max(0, 3 - len(recent_trades))

    def record_day_trade(
        self, symbol: str, was_credit_spread_expiry: bool = False
    ) -> None:
        """Record a day trade for PDT tracking.

        Credit spreads that expire worthless do NOT count as day trades.

        Args:
            symbol: The traded symbol.
            was_credit_spread_expiry: True if this was a credit spread that expired.
        """
        self._day_trades.append(
            DayTrade(
                symbol=symbol,
                date=date.today(),
                was_credit_spread_expiry=was_credit_spread_expiry,
            )
        )

        # Clean up old trades (older than 5 days)
        cutoff = date.today() - timedelta(days=5)
        self._day_trades = [dt for dt in self._day_trades if dt.date >= cutoff]

        if not was_credit_spread_expiry:
            remaining = self.get_pdt_trades_remaining()
            logger.info(
                "day_trade_recorded",
                symbol=symbol,
                pdt_remaining=remaining,
                is_pdt=self.is_pdt_restricted(),
            )

    # ── Event Day Management ─────────────────────────────────────

    def set_event_day(self, event_name: str) -> None:
        """Mark today as an economic event day (FOMC, CPI, PPI, etc.).

        This reduces position sizing by 50% for all new trades.

        Args:
            event_name: Name of the event (e.g., "FOMC", "CPI").
        """
        self._is_event_day = True
        self._event_name = event_name
        self._risk_events.append(f"EVENT_DAY: {event_name} — sizing reduced 50%")
        logger.warning("event_day_set", event=event_name)

    def get_event_day_multiplier(self) -> float:
        """Get the sizing multiplier for event days.

        Returns 0.5 on event days, 1.0 otherwise.
        """
        return 0.5 if self._is_event_day else 1.0

    # ── Multi-Pillar Risk ────────────────────────────────────────

    def get_ticker_total_risk(self, symbol: str) -> float:
        """Get the total risk exposure for a single ticker across all pillars.

        Used to enforce the 3% max combined risk per ticker when running
        multiple pillars simultaneously.

        Args:
            symbol: Ticker symbol.

        Returns:
            Total risk in dollars across all open positions for this ticker.
        """
        positions = self.pm.get_positions_for_symbol(symbol)
        total_risk = 0.0
        for pos in positions:
            # For credit spreads, risk = (stop_loss - entry) * 100 * quantity
            if pos.pillar in (1, 2, 3):
                # Use the worst-case stop (widest tranche stop)
                active_tranches = [t for t in pos.tranches if t.status.value == "ACTIVE"]
                if active_tranches:
                    worst_stop = max(t.stop_price for t in active_tranches)
                else:
                    worst_stop = pos.stop_loss
                risk = (worst_stop - pos.average_entry_price) * 100 * pos.active_quantity
            else:
                # P4: risk = premium paid * 100 * quantity
                risk = pos.average_entry_price * 100 * pos.active_quantity
            total_risk += abs(risk)
        return round(total_risk, 2)

    def get_max_risk_per_ticker(self) -> float:
        """Maximum combined risk per ticker (configurable, default 3%)."""
        return self.account_balance * self._cfg.max_risk_per_ticker_pct

    # ── Tiered Stop Risk Calculation ─────────────────────────────

    def calculate_tiered_stop_risk(self, position: Position) -> float:
        """Calculate max risk accounting for tiered stops.

        Worst case = all tranches stopped out. But since tranches have
        different stop levels, the actual max risk is the weighted sum.

        Args:
            position: Position with tiered stops.

        Returns:
            Maximum possible loss in dollars.
        """
        if not position.tranches:
            # No tiered stops — use standard calculation
            if position.pillar in (1, 2, 3):
                return abs(
                    (position.stop_loss - position.entry_price) * 100 * position.quantity
                )
            else:
                return position.entry_price * 100 * position.quantity

        total_risk = 0.0
        for tranche in position.tranches:
            # Risk per tranche = (stop_price - entry) * 100 * tranche_quantity
            tranche_risk = (tranche.stop_price - position.average_entry_price) * 100 * tranche.quantity
            total_risk += abs(tranche_risk)

        return round(total_risk, 2)

    # ── Swing Position Risk ──────────────────────────────────────

    def get_swing_exposure(self) -> float:
        """Get total dollar exposure in swing (overnight) positions."""
        swing_positions = self.pm.open_swing_positions
        total_exposure = 0.0
        for pos in swing_positions:
            if pos.pillar in (1, 2, 3):
                # Credit spread: exposure = wing_width * 100 * quantity
                # Approximate wing width from stop loss
                exposure = abs(pos.stop_loss) * 100 * pos.active_quantity
            else:
                exposure = pos.average_entry_price * 100 * pos.active_quantity
            total_exposure += exposure
        return round(total_exposure, 2)

    def get_max_swing_exposure(self) -> float:
        """Maximum allowed exposure in swing positions = 10% of account."""
        return self.account_balance * 0.10

    def can_open_swing(self, additional_risk: float) -> bool:
        """Check if we can open a new swing position within risk budget.

        Args:
            additional_risk: Risk of the proposed new swing position.

        Returns:
            True if the new swing would be within the 10% budget.
        """
        current_exposure = self.get_swing_exposure()
        max_exposure = self.get_max_swing_exposure()
        return (current_exposure + additional_risk) <= max_exposure

    # ── Main Risk Check ──────────────────────────────────────────

    def can_open_position(
        self,
        symbol: str,
        tier: str,
        max_risk: float,
        is_swing: bool = False,
        is_scale_in: bool = False,
    ) -> RiskCheck:
        """Pre-trade risk check. Must pass before any order is submitted.

        Runs through all risk checks in order of severity:
        1. Daily shutdown
        2. Daily loss cap
        3. PDT check (if under $25K)
        4. Cooldown
        5. Position limit for tier
        6. Linear scaling (account tier max spreads)
        7. Multi-pillar risk (max 3% per ticker)
        8. Swing position budget (if swing trade)
        9. Total risk check

        Args:
            symbol: Ticker symbol.
            tier: Ticker tier ("tier1", "tier2", "tier3").
            max_risk: Maximum possible loss for this trade in dollars.
            is_swing: Whether this is a swing (overnight) trade.
            is_scale_in: Whether this is adding to an existing position.

        Returns:
            RiskCheck with approval status and details.
        """
        account_tier = self.get_account_tier()

        # Check 1: Daily shutdown
        if self._shutdown:
            return RiskCheck(
                approved=False,
                reason=f"DAILY_SHUTDOWN: {self._shutdown_reason}",
                daily_pnl=self.pm.get_daily_pnl(),
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 2: Daily loss cap
        daily_pnl = self.pm.get_daily_pnl()
        if daily_pnl <= -self.daily_loss_cap:
            self._trigger_shutdown(
                f"Daily loss cap hit: ${daily_pnl:,.2f} <= -${self.daily_loss_cap:,.2f}"
            )
            return RiskCheck(
                approved=False,
                reason=f"DAILY_LOSS_CAP: P&L ${daily_pnl:,.2f} exceeds cap -${self.daily_loss_cap:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 3: PDT (Pattern Day Trader) restriction
        if self.is_pdt_restricted() and not is_swing:
            remaining = self.get_pdt_trades_remaining()
            if remaining <= 0:
                return RiskCheck(
                    approved=False,
                    reason=f"PDT_LIMIT: 0 day trades remaining (account ${self.account_balance:,.0f} < $25K)",
                    pdt_trades_remaining=0,
                    daily_pnl=daily_pnl,
                    daily_loss_cap=self.daily_loss_cap,
                    account_tier=account_tier.tier_name,
                )

        # Check 3b: Recent loser — handled by ReentryGuard in engine (candle-based, not time-based)
        # Kept as fallback: block if loss was < 5 min ago (absolute minimum)
        if symbol in self._recent_losers:
            loss_time = self._recent_losers[symbol]
            elapsed = (datetime.now() - loss_time).total_seconds() / 60
            if elapsed < 5:
                return RiskCheck(
                    approved=False,
                    reason=f"RECENT_LOSER: {symbol} lost {5 - elapsed:.0f}m ago, waiting for candle confirmation",
                    daily_pnl=daily_pnl,
                    daily_loss_cap=self.daily_loss_cap,
                    account_tier=account_tier.tier_name,
                )
            elif elapsed > 60:
                # Auto-expire after 1 hour regardless
                del self._recent_losers[symbol]

        # Check 4: Cooldown (skip for scale-ins)
        if not is_scale_in:
            cooldown_info = self._cooldowns.get(symbol)
            if cooldown_info:
                consecutive_losses, cooldown_until = cooldown_info
                if cooldown_until and datetime.now() < cooldown_until:
                    remaining = (cooldown_until - datetime.now()).total_seconds() / 60
                    return RiskCheck(
                        approved=False,
                        reason=f"COOLDOWN: {symbol} on cooldown for {remaining:.0f} more minutes "
                               f"({consecutive_losses} consecutive losses)",
                        cooldown_active=True,
                        cooldown_until=cooldown_until,
                        account_tier=account_tier.tier_name,
                    )

        # Check 5: Position limit for this tier
        max_pos = self._cfg.max_positions.get(tier, 3)
        current_pos = self.pm.get_position_count(tier)

        if current_pos >= max_pos and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"POSITION_LIMIT: {tier} has {current_pos}/{max_pos} positions",
                current_positions=current_pos,
                max_positions=max_pos,
                account_tier=account_tier.tier_name,
            )

        # Check 5b: Per-ticker position limit
        ticker_positions_list = self.pm.get_positions_for_symbol(symbol)
        ticker_positions = len(ticker_positions_list)
        max_per_ticker = self._cfg.max_positions_per_ticker
        logger.info(
            "ticker_limit_check",
            symbol=symbol,
            ticker_positions=ticker_positions,
            max_per_ticker=max_per_ticker,
            position_ids=[p.id for p in ticker_positions_list],
        )
        if ticker_positions >= max_per_ticker and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"TICKER_LIMIT: {symbol} already has {ticker_positions}/{max_per_ticker} positions",
                current_positions=ticker_positions,
                max_positions=max_per_ticker,
                account_tier=account_tier.tier_name,
            )

        # Check 6: Linear scaling — total spreads across all tiers
        total_positions = self.pm.get_position_count()
        max_spreads = account_tier.max_spreads

        if total_positions >= max_spreads and not is_scale_in:
            return RiskCheck(
                approved=False,
                reason=f"ACCOUNT_TIER_LIMIT: {account_tier.tier_name} tier allows max {max_spreads} spreads, "
                       f"currently at {total_positions}",
                current_positions=total_positions,
                max_positions=max_spreads,
                account_tier=account_tier.tier_name,
            )

        # Check 7: Multi-pillar risk — max 3% per ticker
        ticker_risk = self.get_ticker_total_risk(symbol)
        max_ticker_risk = self.get_max_risk_per_ticker()

        if ticker_risk + max_risk > max_ticker_risk:
            return RiskCheck(
                approved=False,
                reason=f"MULTI_PILLAR_RISK: {symbol} combined risk ${ticker_risk + max_risk:,.2f} "
                       f"would exceed 3% cap ${max_ticker_risk:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # Check 8: Swing position budget
        if is_swing:
            if not self.can_open_swing(max_risk):
                current_swing = self.get_swing_exposure()
                max_swing = self.get_max_swing_exposure()
                return RiskCheck(
                    approved=False,
                    reason=f"SWING_RISK_BUDGET: Current swing exposure ${current_swing:,.2f} + "
                           f"new risk ${max_risk:,.2f} would exceed 10% budget ${max_swing:,.2f}",
                    daily_pnl=daily_pnl,
                    daily_loss_cap=self.daily_loss_cap,
                    account_tier=account_tier.tier_name,
                )

        # Check 9: Would this trade push us past the daily loss cap?
        if daily_pnl - max_risk <= -self.daily_loss_cap:
            return RiskCheck(
                approved=False,
                reason=f"RISK_TOO_HIGH: Current P&L ${daily_pnl:,.2f} - max risk ${max_risk:,.2f} "
                       f"would exceed cap -${self.daily_loss_cap:,.2f}",
                daily_pnl=daily_pnl,
                daily_loss_cap=self.daily_loss_cap,
                account_tier=account_tier.tier_name,
            )

        # All checks passed
        pdt_remaining = self.get_pdt_trades_remaining()

        logger.info(
            "risk_approved",
            symbol=symbol,
            tier=tier,
            positions=f"{current_pos}/{max_pos}",
            daily_pnl=daily_pnl,
            account_tier=account_tier.tier_name,
            max_spreads=max_spreads,
            event_day=self._is_event_day,
            pdt_remaining=pdt_remaining,
            is_swing=is_swing,
            is_scale_in=is_scale_in,
        )

        return RiskCheck(
            approved=True,
            current_positions=current_pos,
            max_positions=max_pos,
            daily_pnl=daily_pnl,
            daily_loss_cap=self.daily_loss_cap,
            pdt_trades_remaining=pdt_remaining,
            event_day_reduction=self._is_event_day,
            account_tier=account_tier.tier_name,
        )

    def adjust_size_for_events(self, base_quantity: int) -> int:
        """Adjust position size for event days.

        On FOMC/CPI/PPI days, reduce position size by 50%.

        Args:
            base_quantity: The originally calculated quantity.

        Returns:
            Adjusted quantity (at least 1).
        """
        multiplier = self.get_event_day_multiplier()
        adjusted = max(1, int(base_quantity * multiplier))

        if multiplier < 1.0:
            logger.info(
                "event_day_size_reduction",
                event=self._event_name,
                original_qty=base_quantity,
                adjusted_qty=adjusted,
                multiplier=multiplier,
            )

        return adjusted

    def record_trade_result(
        self,
        position: Position,
        trade_time: datetime | None = None,
        ai_confidence: float = 1.0,
        flow_aligned: bool = True,
        level_confirmed: bool = True,
    ) -> None:
        """Record a completed trade for risk tracking and stats.

        Enhanced with bad trade classification from @SuperLuckeee's Lever 4:
        "Your expectancy gets damaged most by extra trades, boredom trades,
        revenge trades, trades outside your window."

        A trade is classified as BAD if ANY of these are true:
        - Taken before 10:00 AM ET
        - AI confidence below 70%
        - Outside key levels (no level confirmation)
        - Against flow direction
        - More than 2 trades already taken today

        Updates:
        - Daily stats (win rate, avg win/loss, bad trade %)
        - Bad trade classification with reasons
        - Consecutive loss counter for cooldowns
        - PDT tracking
        - Peak position count
        - Daily P&L watermark and drawdown

        Args:
            position: The closed position.
            trade_time: When the trade was taken (for time-of-day check).
            ai_confidence: Kage's verdict confidence (0-1).
            flow_aligned: Whether flow agreed with trade direction.
            level_confirmed: Whether price was at a key level.
        """
        won = position.unrealized_pnl > 0
        pnl = position.unrealized_pnl

        # ── Bad Trade Classification (Lever 4) ───────────────
        bad_reasons: list[str] = []

        # Check 1: Taken before 10:00 AM ET
        if trade_time:
            from zoneinfo import ZoneInfo
            trade_et = trade_time.astimezone(ZoneInfo("America/New_York"))
            if trade_et.hour < 10:
                bad_reasons.append(f"BEFORE_10AM: trade at {trade_et.strftime('%H:%M')} ET")

        # Check 2: AI confidence below 70%
        if ai_confidence < 0.70:
            bad_reasons.append(f"LOW_AI_CONFIDENCE: {ai_confidence:.0%} < 70%")

        # Check 3: Outside key levels
        if not level_confirmed:
            bad_reasons.append("NO_LEVEL_CONFIRMATION: trade not at key S/R")

        # Check 4: Against flow direction
        if not flow_aligned:
            bad_reasons.append("FLOW_MISALIGNED: trade against institutional flow")

        # Check 5: Overtrading — use max_trades_per_day from engine config
        max_daily = config().engine.max_trades_per_day if hasattr(config().engine, 'max_trades_per_day') else 50
        if self._daily_stats.total_trades >= max_daily:
            bad_reasons.append(f"OVERTRADE: trade #{self._daily_stats.total_trades + 1} (max {max_daily}/day)")

        if bad_reasons:
            self._daily_stats.rule_violations += 1
            for reason in bad_reasons:
                self._daily_stats.bad_trades_detail.append(
                    f"{position.symbol} P{position.pillar}: {reason}"
                )
            logger.warning(
                "bad_trade_detected",
                symbol=position.symbol,
                pillar=position.pillar,
                reasons=bad_reasons,
                total_violations=self._daily_stats.rule_violations,
            )

        # Update daily stats
        self._daily_stats.total_trades += 1
        if won:
            self._daily_stats.winning_trades += 1
            self._daily_stats.total_profit += pnl
        else:
            self._daily_stats.losing_trades += 1
            self._daily_stats.total_losses += pnl  # pnl is negative for losses

            # Check if this was a max loss trade
            # Max loss = position was stopped out at worst level
            if position.status in (PositionStatus.CLOSED_STOP, PositionStatus.CLOSED_TIERED_STOP):
                # Check if ALL tranches were stopped (worst case)
                if position.tranches and all(
                    t.status.value == "STOPPED" for t in position.tranches
                ):
                    self._daily_stats.max_loss_trades += 1
                elif not position.tranches:
                    self._daily_stats.max_loss_trades += 1

        # PDT tracking — record day trade
        is_expired = position.status == PositionStatus.EXPIRED_WORTHLESS
        is_credit_spread = position.pillar in (1, 2, 3)
        self.record_day_trade(
            symbol=position.symbol,
            was_credit_spread_expiry=is_credit_spread and is_expired,
        )

        # Update cooldown tracking
        symbol = position.symbol
        if symbol not in self._cooldowns:
            self._cooldowns[symbol] = (0, None)

        consecutive_losses, _ = self._cooldowns[symbol]

        if won:
            # Win resets the counter
            self._cooldowns[symbol] = (0, None)
        else:
            consecutive_losses += 1
            cooldown_until = None

            if consecutive_losses >= self._cfg.cooldown_consecutive_losses:
                cooldown_until = datetime.now() + timedelta(minutes=self._cfg.cooldown_minutes)
                event = (
                    f"COOLDOWN_TRIGGERED: {symbol} ({consecutive_losses} consecutive losses, "
                    f"paused until {cooldown_until.strftime('%H:%M')})"
                )
                self._risk_events.append(event)
                logger.warning("cooldown_triggered", symbol=symbol, until=cooldown_until)

            self._cooldowns[symbol] = (consecutive_losses, cooldown_until)

            # Track as recent loser — prevent same-symbol re-entry for 30 min
            self._recent_losers[symbol] = datetime.now()
            logger.info("recent_loser_tracked", symbol=symbol)

        # Update P&L watermark
        daily_pnl = self.pm.get_daily_pnl()
        if daily_pnl > self._daily_pnl_peak:
            self._daily_pnl_peak = daily_pnl

        drawdown = self._daily_pnl_peak - daily_pnl
        if drawdown > self._daily_max_drawdown:
            self._daily_max_drawdown = drawdown

        # Update peak positions
        current_count = len(self.pm.open_positions)
        if current_count > self._peak_positions:
            self._peak_positions = current_count

        # Check if we've hit the loss cap
        if daily_pnl <= -self.daily_loss_cap:
            self._trigger_shutdown(f"Daily loss cap hit after trade: ${daily_pnl:,.2f}")

        logger.info(
            "trade_recorded",
            symbol=symbol,
            won=won,
            pnl=pnl,
            daily_pnl=daily_pnl,
            win_rate=f"{self._daily_stats.win_rate:.0%}",
            avg_win=f"${self._daily_stats.average_win:.2f}",
            avg_loss=f"${self._daily_stats.average_loss:.2f}",
            bad_trade_pct=f"{self._daily_stats.bad_trade_pct:.0%}",
        )

    def _trigger_shutdown(self, reason: str) -> None:
        """Shut down trading for the rest of the day."""
        self._shutdown = True
        self._shutdown_reason = reason
        self._risk_events.append(f"SHUTDOWN: {reason}")
        logger.error("daily_shutdown", reason=reason)

    def trigger_force_close(self, reason: str = "BLACK_SWAN_RED") -> None:
        """Signal that all positions should be force-closed.

        Called by the Black Swan detector or when daily cap is hit.
        The actual closing is done by PositionManager.force_close_all().
        """
        self._risk_events.append(f"FORCE_CLOSE: {reason}")
        self._trigger_shutdown(reason)
        logger.error("force_close_triggered", reason=reason)

    def generate_daily_report(self) -> DailyRiskReport:
        """Generate end-of-day risk report with full stats.

        Includes the 4 key daily stats:
        1. Win Rate = Wins / Total Trades
        2. Average Win = Total Profit / Winning Trades
        3. Average Loss = Total Losses / Losing Trades
        4. Bad Trade % = Trades that hit max loss / Total Trades

        Returns:
            DailyRiskReport with all metrics for the day.
        """
        stats = self._daily_stats
        account_tier = self.get_account_tier()

        report = DailyRiskReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_trades=stats.total_trades,
            winning_trades=stats.winning_trades,
            losing_trades=stats.losing_trades,
            win_rate=round(stats.win_rate, 4),
            average_win=round(stats.average_win, 2),
            average_loss=round(stats.average_loss, 2),
            bad_trade_pct=round(stats.bad_trade_pct, 4),
            profit_factor=round(stats.profit_factor, 2) if stats.profit_factor != float("inf") else 0.0,
            total_pnl=self.pm.get_daily_pnl(),
            max_drawdown=round(self._daily_max_drawdown, 2),
            peak_positions=self._peak_positions,
            risk_events=self._risk_events,
            shutdown_triggered=self._shutdown,
            force_closes=len([e for e in self._risk_events if "FORCE_CLOSE" in e]),
            day_trades_used=len([
                dt for dt in self._day_trades
                if dt.date == date.today() and not dt.was_credit_spread_expiry
            ]),
            swing_positions_held=len(self.pm.open_swing_positions),
            overnight_exposure=self.get_swing_exposure(),
            account_tier=account_tier.tier_name,
            max_spreads_allowed=account_tier.max_spreads,
        )

        logger.info(
            "daily_risk_report",
            trades=stats.total_trades,
            win_rate=f"{stats.win_rate:.0%}",
            avg_win=f"${stats.average_win:.2f}",
            avg_loss=f"${stats.average_loss:.2f}",
            bad_trade_pct=f"{stats.bad_trade_pct:.0%}",
            pnl=report.total_pnl,
            max_drawdown=report.max_drawdown,
            events=len(report.risk_events),
            account_tier=account_tier.tier_name,
            max_spreads=account_tier.max_spreads,
        )

        return report

    def reset_daily(self, new_balance: float | None = None) -> None:
        """Reset all daily state for a new trading day.

        Args:
            new_balance: Updated account balance. If None, keeps current.
        """
        if new_balance is not None:
            self.account_balance = new_balance

        self._daily_pnl_peak = 0.0
        self._daily_max_drawdown = 0.0
        self._shutdown = False
        self._shutdown_reason = ""
        self._cooldowns.clear()
        self._risk_events.clear()
        self._peak_positions = 0
        self._is_event_day = False
        self._event_name = ""
        self._daily_stats = DailyStats()

        # Don't clear day trades — they use a rolling 5-day window
        # Clean up old ones instead
        cutoff = date.today() - timedelta(days=5)
        self._day_trades = [dt for dt in self._day_trades if dt.date >= cutoff]

        logger.info(
            "risk_manager_reset",
            balance=self.account_balance,
            account_tier=self.get_account_tier().tier_name,
            max_spreads=self.get_max_spreads(),
            pdt_restricted=self.is_pdt_restricted(),
            pdt_remaining=self.get_pdt_trades_remaining(),
        )


#===============================================================================
# SECTION: RISK - Trade Journal
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: esther/risk/journal.py (420 lines)
#-------------------------------------------------------------------------------

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

    def get_lessons(self) -> str:
        """Summarize patterns from recent trades for in-session learning.

        Reads today's + recent entries and builds a brief text highlighting:
        - Repeated losses on the same symbol/direction
        - Streaks (winning or losing)
        - Direction accuracy issues
        - Any recurring bad-trade patterns

        Returns:
            A brief text suitable for including in debate context.
        """
        entries = self._load_recent_days(3)
        if not entries:
            return ""

        lines: list[str] = []

        # Group by symbol+direction for pattern detection
        from collections import Counter, defaultdict
        sym_dir_results: dict[str, list[bool]] = defaultdict(list)
        sym_dir_pnl: dict[str, float] = defaultdict(float)
        for e in entries:
            key = f"{e.symbol} {e.direction}"
            sym_dir_results[key].append(e.won)
            sym_dir_pnl[key] += e.pnl

        # Flag losing streaks (3+ consecutive losses same symbol+direction)
        for key, results in sym_dir_results.items():
            consecutive_losses = 0
            max_streak = 0
            for won in results:
                if not won:
                    consecutive_losses += 1
                    max_streak = max(max_streak, consecutive_losses)
                else:
                    consecutive_losses = 0
            if max_streak >= 3:
                lines.append(f"⚠️ {key}: {max_streak} consecutive losses — consider avoiding or inverting")
            elif max_streak >= 2:
                total_losses = sum(1 for w in results if not w)
                if total_losses >= 3:
                    lines.append(f"⚠️ {key}: lost {total_losses}/{len(results)} recent trades (P&L: ${sym_dir_pnl[key]:+,.0f})")

        # Overall direction accuracy today
        today_entries = [e for e in entries if e.date == date.today().isoformat()]
        if len(today_entries) >= 3:
            today_wins = sum(1 for e in today_entries if e.won)
            today_wr = today_wins / len(today_entries) * 100
            if today_wr < 30:
                lines.append(f"🔴 Today's win rate: {today_wr:.0f}% ({today_wins}/{len(today_entries)}) — direction reads are off")
            elif today_wr < 50:
                lines.append(f"🟡 Today's win rate: {today_wr:.0f}% ({today_wins}/{len(today_entries)}) — be selective")

        # Bad trade patterns
        bad_reasons: list[str] = []
        for e in entries:
            if e.is_bad_trade:
                bad_reasons.extend(e.bad_reasons)
        if bad_reasons:
            reason_counts = Counter(bad_reasons).most_common(3)
            for reason, count in reason_counts:
                if count >= 2:
                    lines.append(f"🔁 Recurring mistake: {reason} ({count}x)")

        # Flow alignment check
        flow_against_losses = [
            e for e in entries
            if not e.won and (
                (e.direction == "BULL" and e.flow_bias < -15)
                or (e.direction == "BEAR" and e.flow_bias > 15)
            )
        ]
        if len(flow_against_losses) >= 2:
            lines.append(f"📊 {len(flow_against_losses)} losses from trading AGAINST flow — respect the flow direction")

        if not lines:
            return ""

        return "\n".join(lines)

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


#===============================================================================
# SECTION: SCRIPTS - Live Runner
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: scripts/run_live.py (183 lines)
#-------------------------------------------------------------------------------

#!/usr/bin/env python3
"""Entry point to start Esther in live trading mode.

Usage:
    python scripts/run_live.py                    # Production mode
    python scripts/run_live.py --sandbox          # Sandbox mode (paper trading)
    python scripts/run_live.py --config my.yaml   # Custom config file
    python scripts/run_live.py --sandbox --log-level DEBUG

Environment variables required:
    TRADIER_API_KEY      — Tradier API key
    TRADIER_ACCOUNT_ID   — Tradier account ID
    ANTHROPIC_API_KEY    — Anthropic API key for Claude
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import structlog


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Set up structlog with JSON output for production, pretty console for dev."""
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if sys.stdout.isatty():
        # Pretty console output for interactive use
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        # JSON output for production/log aggregation
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also set up file logging if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # File logging handled by structlog via PrintLogger to stderr/file


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Esther Trading Bot — Autonomous Options Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --sandbox                  Paper trade with Tradier sandbox
    %(prog)s --config prod.yaml         Use production config
    %(prog)s --sandbox --log-level DEBUG Verbose sandbox mode
        """,
    )

    parser.add_argument(
        "--sandbox",
        action="store_true",
        default=False,
        help="Use Tradier sandbox (paper trading). Highly recommended for testing.",
    )

    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml. Defaults to project root config.yaml.",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )

    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file. If not set, logs only to stdout.",
    )

    parser.add_argument(
        "--broker",
        type=str,
        default="alpaca",
        choices=["alpaca", "tradier"],
        help="Broker to use (default: alpaca). Alpaca uses paper2 account.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the pipeline without submitting orders (for validation).",
    )

    return parser.parse_args()


async def main() -> None:
    """Main entry point — set up and run the engine."""
    args = parse_args()

    # Configure logging
    log_file = args.log_file or "logs/esther.log"
    configure_logging(level=args.log_level, log_file=log_file)

    logger = structlog.get_logger("esther.main")

    # Banner
    logger.info(
        "esther_starting",
        mode="SANDBOX" if args.sandbox else "LIVE",
        broker=args.broker,
        config=args.config or "default",
        log_level=args.log_level,
    )

    if not args.sandbox and args.broker == "tradier":
        logger.warning(
            "⚠️  LIVE MODE — Real money is at risk. "
            "Use --sandbox for paper trading."
        )

    # Import engine (after logging is configured)
    from esther.core.engine import EstherEngine

    engine = EstherEngine(
        config_path=args.config,
        sandbox=args.sandbox,
        broker=args.broker,
    )

    # Set up graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_signal(sig: signal.Signals) -> None:
        logger.info("shutdown_signal_received", signal=sig.name)
        shutdown_event.set()
        asyncio.ensure_future(engine.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal, sig)

    # Run the engine
    try:
        logger.info("engine_launching")
        await engine.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception as e:
        logger.error("engine_crashed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        logger.info("esther_stopped")


if __name__ == "__main__":
    asyncio.run(main())


#===============================================================================
# SECTION: SCRIPTS - Backtest Runner
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: scripts/run_backtest.py (616 lines)
#-------------------------------------------------------------------------------

#!/usr/bin/env python3
"""Backtesting scaffold for Esther Trading.

Loads historical data, simulates the trading pipeline, and generates
performance reports. This is the foundation — extend as needed.

Usage:
    python scripts/run_backtest.py --start 2024-01-01 --end 2024-03-01
    python scripts/run_backtest.py --symbol SPY --start 2024-01-01
    python scripts/run_backtest.py --start 2024-01-01 --output results/

Architecture:
    The backtester replaces TradierClient with a HistoricalDataProvider that
    feeds saved market data to the same signal pipeline used in live trading.
    This ensures the backtest logic matches live behavior exactly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(structlog.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("esther.backtest")


# ── Historical Data Provider ─────────────────────────────────────


class HistoricalDataProvider:
    """Replaces TradierClient for backtesting.

    Loads historical bars from CSV/JSON and replays them chronologically.
    Option chains are synthesized from underlying price + Black-Scholes approximation.
    """

    def __init__(self, data_dir: str = "data/historical"):
        self.data_dir = Path(data_dir)
        self._bars: dict[str, list[dict[str, Any]]] = {}  # symbol → bars
        self._current_date: date | None = None
        self._date_index: int = 0

    def load_bars(self, symbol: str, bars_data: list[dict[str, Any]]) -> None:
        """Load historical bar data for a symbol.

        Args:
            symbol: Ticker symbol.
            bars_data: List of dicts with keys: date, open, high, low, close, volume.
        """
        self._bars[symbol] = sorted(bars_data, key=lambda b: b["date"])
        logger.info("bars_loaded", symbol=symbol, count=len(bars_data))

    def get_bars_up_to(self, symbol: str, current_date: date, lookback: int = 30) -> list[dict[str, Any]]:
        """Get historical bars up to (inclusive) the current date.

        Args:
            symbol: Ticker symbol.
            current_date: Simulation date.
            lookback: Number of bars to return.

        Returns:
            List of bar dicts.
        """
        if symbol not in self._bars:
            return []

        eligible = [
            b for b in self._bars[symbol]
            if date.fromisoformat(b["date"]) <= current_date
        ]

        return eligible[-lookback:]

    def get_quote(self, symbol: str, current_date: date) -> dict[str, Any] | None:
        """Get the quote (last bar) for a symbol on a date.

        Returns:
            Bar dict for the date, or None.
        """
        if symbol not in self._bars:
            return None

        for bar in self._bars[symbol]:
            if bar["date"] == current_date.isoformat():
                return bar

        return None

    def get_trading_dates(self, start: date, end: date) -> list[date]:
        """Get all trading dates in the range (dates with data).

        Uses the longest symbol's date range.
        """
        all_dates: set[date] = set()
        for symbol_bars in self._bars.values():
            for bar in symbol_bars:
                d = date.fromisoformat(bar["date"])
                if start <= d <= end:
                    all_dates.add(d)

        return sorted(all_dates)


# ── Synthetic Option Chain ───────────────────────────────────────


def synthesize_option_chain(
    underlying_price: float,
    expiration_days: int = 0,
    num_strikes: int = 20,
    strike_width: float = 5.0,
    base_iv: float = 0.25,
) -> list[dict[str, Any]]:
    """Generate a synthetic option chain for backtesting.

    Uses a simplified Black-Scholes approximation to create realistic-looking
    option prices with greeks. Not meant to be perfectly accurate — just good
    enough for strategy validation.

    Args:
        underlying_price: Current price of the underlying.
        expiration_days: Days to expiration.
        num_strikes: Number of strikes on each side of ATM.
        strike_width: Dollar distance between strikes.
        base_iv: Base implied volatility.

    Returns:
        List of option dicts mimicking OptionQuote structure.
    """
    chain = []
    dte = max(expiration_days, 1) / 365.0
    sqrt_dte = np.sqrt(dte)

    for i in range(-num_strikes, num_strikes + 1):
        strike = round(underlying_price + i * strike_width, 2)
        moneyness = (underlying_price - strike) / underlying_price

        # Simplified delta approximation
        d1 = moneyness / (base_iv * sqrt_dte) if base_iv * sqrt_dte > 0 else 0
        call_delta = min(0.99, max(0.01, 0.5 + d1 * 0.3))
        put_delta = call_delta - 1.0

        # IV smile: higher IV for OTM options
        iv = base_iv * (1 + abs(moneyness) * 2)

        # Simplified pricing
        time_value = underlying_price * iv * sqrt_dte * 0.4
        call_intrinsic = max(0, underlying_price - strike)
        put_intrinsic = max(0, strike - underlying_price)

        call_price = round(call_intrinsic + time_value * call_delta, 2)
        put_price = round(put_intrinsic + time_value * abs(put_delta), 2)

        for opt_type, price, delta in [
            ("call", max(0.05, call_price), call_delta),
            ("put", max(0.05, put_price), put_delta),
        ]:
            spread = max(0.05, price * 0.03)  # 3% bid-ask
            chain.append({
                "symbol": f"SYN_{strike}_{opt_type[0].upper()}",
                "option_type": opt_type,
                "strike": strike,
                "bid": round(price - spread / 2, 2),
                "ask": round(price + spread / 2, 2),
                "mid": round(price, 2),
                "volume": int(1000 * call_delta) if opt_type == "call" else int(1000 * abs(put_delta)),
                "open_interest": 5000,
                "delta": round(delta, 4),
                "gamma": round(0.02 * (1 - abs(2 * delta - 1)), 4),
                "theta": round(-price * 0.01, 4),
                "vega": round(underlying_price * sqrt_dte * 0.01, 4),
                "iv": round(iv, 4),
            })

    return chain


# ── Backtest Engine ──────────────────────────────────────────────


class BacktestResult:
    """Container for backtest results."""

    def __init__(self):
        self.trades: list[dict[str, Any]] = []
        self.daily_pnl: list[dict[str, Any]] = []
        self.total_pnl: float = 0.0
        self.max_drawdown: float = 0.0
        self.win_rate: float = 0.0
        self.sharpe_ratio: float = 0.0
        self.total_trades: int = 0

    def add_trade(self, trade: dict[str, Any]) -> None:
        """Record a simulated trade."""
        self.trades.append(trade)
        self.total_trades += 1

    def compute_stats(self) -> None:
        """Compute aggregate statistics from recorded trades."""
        if not self.trades:
            return

        pnls = [t.get("pnl", 0) for t in self.trades]
        self.total_pnl = sum(pnls)
        self.win_rate = len([p for p in pnls if p > 0]) / len(pnls)

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = peak - cumulative
        self.max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Sharpe ratio (annualized, assuming daily)
        if len(pnls) > 1:
            daily_returns = np.array(pnls)
            mean_return = np.mean(daily_returns)
            std_return = np.std(daily_returns)
            if std_return > 0:
                self.sharpe_ratio = float(mean_return / std_return * np.sqrt(252))

    def summary(self) -> dict[str, Any]:
        """Generate a summary dict."""
        self.compute_stats()
        return {
            "total_trades": self.total_trades,
            "total_pnl": round(self.total_pnl, 2),
            "win_rate": round(self.win_rate, 4),
            "max_drawdown": round(self.max_drawdown, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "avg_pnl_per_trade": round(self.total_pnl / self.total_trades, 2) if self.total_trades > 0 else 0,
        }


class BacktestEngine:
    """Simulates the Esther trading pipeline on historical data.

    Replays each trading day chronologically:
    1. Load historical bars up to the current date
    2. Run bias engine on the bars
    3. Synthesize an option chain
    4. Determine eligible pillars
    5. Simulate trade entry and exit

    NOTE: This is a scaffold. The full implementation would:
    - Properly track position lifecycle (entry, management, exit)
    - Simulate fills with realistic slippage
    - Handle multi-day positions for weeklies
    - Integrate all signal modules (quality filter, AI debate, etc.)
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        initial_capital: float = 100_000.0,
    ):
        self.symbols = symbols or ["SPY"]
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date.today()
        self.initial_capital = initial_capital
        self.capital = initial_capital

        self.data_provider = HistoricalDataProvider()
        self.result = BacktestResult()

    async def load_data(self) -> None:
        """Load historical data for all symbols.

        In a full implementation, this would:
        - Load from CSV files in data/historical/
        - Or fetch from Tradier historical API
        - Or load from a local database

        For now, generates synthetic data.
        """
        logger.info(
            "loading_data",
            symbols=self.symbols,
            start=self.start_date.isoformat(),
            end=self.end_date.isoformat(),
        )

        for symbol in self.symbols:
            bars = self._generate_synthetic_bars(symbol)
            self.data_provider.load_bars(symbol, bars)

    def _generate_synthetic_bars(self, symbol: str) -> list[dict[str, Any]]:
        """Generate synthetic daily bars for backtesting.

        TODO: Replace with real historical data loading.
        """
        bars = []
        current = self.start_date
        price = 500.0  # starting price
        np.random.seed(hash(symbol) % 2**31)

        while current <= self.end_date:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            # Random walk with slight upward drift
            daily_return = np.random.normal(0.0003, 0.012)
            price *= (1 + daily_return)

            high = price * (1 + abs(np.random.normal(0, 0.005)))
            low = price * (1 - abs(np.random.normal(0, 0.005)))

            bars.append({
                "date": current.isoformat(),
                "open": round(price * (1 + np.random.normal(0, 0.001)), 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(price, 2),
                "volume": int(np.random.normal(50_000_000, 10_000_000)),
            })

            current += timedelta(days=1)

        return bars

    async def run(self) -> BacktestResult:
        """Run the full backtest simulation.

        Returns:
            BacktestResult with all trades and statistics.
        """
        logger.info("backtest_starting", capital=self.initial_capital)

        await self.load_data()

        trading_dates = self.data_provider.get_trading_dates(self.start_date, self.end_date)
        logger.info("trading_dates", count=len(trading_dates))

        for sim_date in trading_dates:
            await self._simulate_day(sim_date)

        self.result.compute_stats()

        logger.info(
            "backtest_complete",
            **self.result.summary(),
        )

        return self.result

    async def _simulate_day(self, sim_date: date) -> None:
        """Simulate one trading day.

        For each symbol:
        1. Get bars up to this date
        2. Compute bias
        3. Determine if we'd trade
        4. Simulate entry and exit

        TODO: Full implementation should use the actual BiasEngine,
        QualityFilter, and pillar logic.
        """
        from esther.signals.bias_engine import BiasEngine
        from esther.data.tradier import Bar

        bias_engine = BiasEngine()
        daily_pnl = 0.0

        for symbol in self.symbols:
            bars_data = self.data_provider.get_bars_up_to(symbol, sim_date, lookback=30)
            if len(bars_data) < 25:
                continue

            # Convert to Bar objects
            bars = [
                Bar(
                    timestamp=datetime.fromisoformat(b["date"]),
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                )
                for b in bars_data
            ]

            current_price = bars[-1].close

            # Compute bias (use VIX=18 as default for backtest)
            try:
                bias = bias_engine.compute_bias(
                    symbol=symbol,
                    bars=bars,
                    vix_level=18.0,
                    current_price=current_price,
                )
            except Exception as e:
                logger.debug("bias_compute_failed", symbol=symbol, error=str(e))
                continue

            # Simple simulation: if bias is strong enough, simulate a trade
            if abs(bias.score) < 30:
                continue  # Skip weak signals

            # Synthesize an option chain
            chain = synthesize_option_chain(
                underlying_price=current_price,
                expiration_days=0,
                base_iv=0.25,
            )

            # Simulate a simple credit spread
            trade_pnl = self._simulate_trade(
                symbol=symbol,
                bias_score=bias.score,
                price=current_price,
                date=sim_date,
            )

            daily_pnl += trade_pnl

        self.capital += daily_pnl
        self.result.daily_pnl.append({
            "date": sim_date.isoformat(),
            "pnl": round(daily_pnl, 2),
            "capital": round(self.capital, 2),
        })

    def _simulate_trade(
        self,
        symbol: str,
        bias_score: float,
        price: float,
        date: date,
    ) -> float:
        """Simulate a single trade and return P&L.

        Simplified: assumes a credit spread with 65% win rate and
        fixed risk/reward based on the pillar.

        TODO: Replace with actual pillar execution simulation.
        """
        # Determine pillar
        if abs(bias_score) >= 60:
            pillar = 4  # directional
            win_prob = 0.45
            win_amount = 300.0
            loss_amount = 200.0
        elif bias_score >= 40:
            pillar = 3  # bull put
            win_prob = 0.65
            win_amount = 150.0
            loss_amount = 350.0
        elif bias_score <= -40:
            pillar = 2  # bear call
            win_prob = 0.65
            win_amount = 150.0
            loss_amount = 350.0
        else:
            return 0.0  # No trade

        # Coin flip with probability
        won = np.random.random() < win_prob
        pnl = win_amount if won else -loss_amount

        self.result.add_trade({
            "date": date.isoformat(),
            "symbol": symbol,
            "pillar": pillar,
            "bias_score": round(bias_score, 2),
            "price": round(price, 2),
            "won": won,
            "pnl": round(pnl, 2),
        })

        return pnl


# ── CLI ──────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Esther Backtesting Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--start",
        type=str,
        default="2024-01-01",
        help="Start date (YYYY-MM-DD). Default: 2024-01-01",
    )

    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD). Default: today.",
    )

    parser.add_argument(
        "--symbol",
        type=str,
        nargs="+",
        default=["SPY"],
        help="Symbols to backtest (e.g., SPY QQQ). Default: SPY",
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Starting capital. Default: $100,000",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory for results. If set, saves JSON report.",
    )

    return parser.parse_args()


async def main() -> None:
    """Run the backtest."""
    args = parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end) if args.end else date.today()

    logger.info(
        "backtest_config",
        symbols=args.symbol,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        capital=args.capital,
    )

    engine = BacktestEngine(
        symbols=args.symbol,
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.capital,
    )

    result = await engine.run()

    # Print summary
    summary = result.summary()
    print("\n" + "=" * 60)
    print("  ESTHER BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Period:         {start_date} → {end_date}")
    print(f"  Symbols:        {', '.join(args.symbol)}")
    print(f"  Starting Cap:   ${args.capital:,.2f}")
    print(f"  Ending Cap:     ${engine.capital:,.2f}")
    print(f"  Total P&L:      ${summary['total_pnl']:,.2f}")
    print(f"  Total Trades:   {summary['total_trades']}")
    print(f"  Win Rate:       {summary['win_rate']:.1%}")
    print(f"  Max Drawdown:   ${summary['max_drawdown']:,.2f}")
    print(f"  Sharpe Ratio:   {summary['sharpe_ratio']:.2f}")
    print(f"  Avg P&L/Trade:  ${summary['avg_pnl_per_trade']:,.2f}")
    print("=" * 60 + "\n")

    # Save results if output dir specified
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

        report_path = output_dir / f"backtest_{start_date}_{end_date}.json"
        report = {
            "config": {
                "symbols": args.symbol,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "initial_capital": args.capital,
            },
            "summary": summary,
            "daily_pnl": result.daily_pnl,
            "trades": result.trades,
        }

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        logger.info("results_saved", path=str(report_path))


if __name__ == "__main__":
    asyncio.run(main())


#===============================================================================
# SECTION: SCRIPTS - Cron Launcher
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: scripts/start_esther.sh (25 lines)
#-------------------------------------------------------------------------------

#!/bin/bash
# Start Esther trading bot — called by cron at market open
# Logs to logs/esther_cron.log

cd /Users/shawnkatyal/esther-trading

# Don't start if already running
if pgrep -f "run_live.py" > /dev/null; then
    echo "$(date): Esther already running, skipping." >> logs/esther_cron.log
    exit 0
fi

echo "$(date): Starting Esther..." >> logs/esther_cron.log

# Activate venv and run in sandbox mode with Alpaca (paper trading)
/Users/shawnkatyal/esther-trading/.venv/bin/python \
    /Users/shawnkatyal/esther-trading/scripts/run_live.py \
    --sandbox \
    --broker tradier \
    --config config-tradier.yaml \
    --log-file logs/esther-tradier.log \
    >> logs/esther_cron.log 2>&1 &

echo "$(date): Esther started (PID $!)" >> logs/esther_cron.log


#===============================================================================
# SECTION: TESTS
#===============================================================================

#-------------------------------------------------------------------------------
# FILE: tests/__init__.py (1 lines)
#-------------------------------------------------------------------------------



#-------------------------------------------------------------------------------
# FILE: tests/test_bias_engine.py (416 lines)
#-------------------------------------------------------------------------------

"""Tests for the Bias Engine — directional bias scoring and pillar eligibility."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pytest

from esther.signals.bias_engine import BiasEngine, BiasScore, Pillar
from esther.data.tradier import Bar


# ── Fixtures ─────────────────────────────────────────────────────


def _make_bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> list[Bar]:
    """Generate a list of Bar objects from close prices.

    If highs/lows/volumes are not provided, they are derived from closes.
    """
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n

    return [
        Bar(
            timestamp=datetime(2024, 3, i + 1) if i < 28 else datetime(2024, 4, i - 27),
            open=closes[i],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
        )
        for i in range(n)
    ]


def _uptrend_bars(n: int = 30, start: float = 500.0, step: float = 1.5) -> list[Bar]:
    """Generate an uptrending series of bars."""
    closes = [start + i * step for i in range(n)]
    highs = [c + 2.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return _make_bars(closes, highs, lows)


def _downtrend_bars(n: int = 30, start: float = 500.0, step: float = 1.5) -> list[Bar]:
    """Generate a downtrending series of bars."""
    closes = [start - i * step for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    return _make_bars(closes, highs, lows)


def _flat_bars(n: int = 30, price: float = 500.0) -> list[Bar]:
    """Generate a flat/sideways series of bars."""
    # Small random-ish oscillation around the price
    closes = [price + (i % 3 - 1) * 0.5 for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return _make_bars(closes, highs, lows)


@pytest.fixture
def engine():
    """Create a BiasEngine with default config."""
    with patch("esther.signals.bias_engine.config") as mock_config:
        from esther.core.config import BiasConfig
        mock_config.return_value.bias = BiasConfig()
        return BiasEngine()


# ── Bias Calculation Tests ───────────────────────────────────────


class TestBiasComputation:
    """Test bias score calculation with various market conditions."""

    def test_uptrend_produces_positive_bias(self, engine):
        """An uptrending market should produce a bullish (positive) bias."""
        bars = _uptrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=18.0)

        assert result.score > 0, f"Expected positive bias for uptrend, got {result.score}"
        assert result.direction == "BULL"
        assert result.symbol == "SPY"

    def test_downtrend_produces_negative_bias(self, engine):
        """A downtrending market should produce a bearish (negative) bias."""
        bars = _downtrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=22.0)

        assert result.score < 0, f"Expected negative bias for downtrend, got {result.score}"
        assert result.direction == "BEAR"

    def test_flat_market_produces_neutral_bias(self, engine):
        """A flat/sideways market should produce a near-neutral bias."""
        bars = _flat_bars(30)
        result = engine.compute_bias("QQQ", bars, vix_level=17.0)

        assert -30 <= result.score <= 30, f"Expected neutral-ish bias for flat market, got {result.score}"

    def test_score_clamped_to_range(self, engine):
        """Bias score should always be between -100 and +100."""
        # Extreme uptrend
        bars = _uptrend_bars(30, step=10.0)
        result = engine.compute_bias("SPX", bars, vix_level=12.0)
        assert -100 <= result.score <= 100

        # Extreme downtrend
        bars = _downtrend_bars(30, start=500.0, step=10.0)
        result = engine.compute_bias("SPX", bars, vix_level=35.0)
        assert -100 <= result.score <= 100

    def test_insufficient_bars_returns_neutral(self, engine):
        """With fewer than 25 bars, should return neutral score of 0."""
        bars = _flat_bars(10)
        result = engine.compute_bias("SPY", bars, vix_level=20.0)

        assert result.score == 0.0
        assert 1 in result.active_pillars  # defaults to P1

    def test_components_are_populated(self, engine):
        """The components dict should have all 8 indicator scores (incl. flow, regime, levels)."""
        bars = _uptrend_bars(30)
        result = engine.compute_bias("SPY", bars, vix_level=18.0)

        expected_keys = {"vwap", "ema_cross", "rsi", "price_action", "vix", "flow", "regime", "levels", "macro"}
        assert set(result.components.keys()) == expected_keys

        # Each component should be numeric and bounded
        for key, value in result.components.items():
            assert isinstance(value, float), f"{key} is not float"
            assert -100 <= value <= 100, f"{key}={value} out of range"

    def test_current_price_override(self, engine):
        """Passing a current_price should use it instead of last close."""
        bars = _flat_bars(30, price=500.0)

        # Override with a much higher price (above VWAP)
        result_high = engine.compute_bias("SPY", bars, vix_level=18.0, current_price=520.0)
        result_low = engine.compute_bias("SPY", bars, vix_level=18.0, current_price=480.0)

        # Higher price should produce higher (more bullish) bias
        assert result_high.score > result_low.score


class TestVixScore:
    """Test VIX contribution to bias."""

    def test_panic_vix_is_bearish(self, engine):
        """VIX > 35 = capitulation zone, VIX 30-35 = IC sweet spot (mildly bearish, not panic).

        SuperLuckeee: "used when IV is high (VIX at 30)" — IC premium is fat.
        VIX 35 is the transition to capitulation (-60), not shutdown (-80).
        """
        score = engine._vix_score(36.0)
        assert score == -60.0  # Capitulation zone
        score_30 = engine._vix_score(32.0)
        assert score_30 == -30.0  # IC sweet spot — mildly bearish, not panic

    def test_elevated_vix_is_mildly_bearish(self, engine):
        """VIX 25-30 should be moderately bearish."""
        score = engine._vix_score(27.0)
        assert score == -40.0

    def test_above_average_vix_is_contrarian_bullish(self, engine):
        """VIX 20-25 is contrarian bullish (fear = opportunity)."""
        score = engine._vix_score(22.0)
        assert score == 20.0

    def test_normal_vix_is_neutral(self, engine):
        """VIX 15-20 should be neutral."""
        score = engine._vix_score(17.0)
        assert score == 0.0

    def test_low_vix_is_slightly_bearish(self, engine):
        """VIX < 15 = complacency, slight correction risk."""
        score = engine._vix_score(12.0)
        assert score == -15.0


class TestRSIScore:
    """Test RSI contribution to bias."""

    def test_overbought_rsi_is_bearish(self, engine):
        """RSI > 70 should produce a bearish score (mean reversion)."""
        # Need enough bars for RSI calc
        # Build a consistently up series that pushes RSI high
        closes = np.array([100.0 + i * 0.8 for i in range(30)])
        score = engine._rsi_score(closes)
        # RSI should be high, score should be negative
        assert score <= 0

    def test_oversold_rsi_is_bullish(self, engine):
        """RSI < 30 should produce a bullish score (mean reversion)."""
        closes = np.array([100.0 - i * 0.8 for i in range(30)])
        score = engine._rsi_score(closes)
        # RSI should be low, score should be positive
        assert score >= 0

    def test_neutral_rsi(self, engine):
        """RSI around 50 should produce a near-zero score."""
        # Alternating up/down to keep RSI near 50
        closes = np.array([100.0 + (i % 2) * 0.3 - 0.15 for i in range(30)])
        score = engine._rsi_score(closes)
        assert -25 <= score <= 25


# ── Pillar Eligibility Tests ────────────────────────────────────


class TestPillarEligibility:
    """Test that bias scores correctly map to active pillars."""

    def test_neutral_zone_activates_p1(self, engine):
        """Score in [-20, +20] should activate P1 (Iron Condors)."""
        pillars = engine._determine_pillars(0.0)
        assert 1 in pillars

        pillars = engine._determine_pillars(15.0)
        assert 1 in pillars

        pillars = engine._determine_pillars(-15.0)
        assert 1 in pillars

    def test_strong_bearish_activates_p2(self, engine):
        """Score <= -60 should activate P2 (Bear Call Spreads)."""
        pillars = engine._determine_pillars(-65.0)
        assert 2 in pillars

        pillars = engine._determine_pillars(-100.0)
        assert 2 in pillars

    def test_strong_bullish_activates_p3(self, engine):
        """Score >= +60 should activate P3 (Bull Put Spreads)."""
        pillars = engine._determine_pillars(65.0)
        assert 3 in pillars

        pillars = engine._determine_pillars(100.0)
        assert 3 in pillars

    def test_high_conviction_activates_p4(self, engine):
        """Score with |score| >= 40 should activate P4 (Directional Scalps)."""
        pillars = engine._determine_pillars(50.0)
        assert 4 in pillars

        pillars = engine._determine_pillars(-50.0)
        assert 4 in pillars

    def test_overlap_zone_p3_and_p4(self, engine):
        """Score of +65 should activate both P3 and P4."""
        pillars = engine._determine_pillars(65.0)
        assert 3 in pillars
        assert 4 in pillars

    def test_overlap_zone_p2_and_p4(self, engine):
        """Score of -65 should activate both P2 and P4."""
        pillars = engine._determine_pillars(-65.0)
        assert 2 in pillars
        assert 4 in pillars

    def test_no_p1_outside_neutral(self, engine):
        """Score outside [-20, +20] should NOT activate P1."""
        pillars = engine._determine_pillars(50.0)
        assert 1 not in pillars

        pillars = engine._determine_pillars(-50.0)
        assert 1 not in pillars

    def test_p4_not_activated_below_threshold(self, engine):
        """Score < 40 magnitude should NOT activate P4."""
        pillars = engine._determine_pillars(30.0)
        assert 4 not in pillars

        pillars = engine._determine_pillars(-30.0)
        assert 4 not in pillars

    def test_default_to_p1_when_nothing_matches(self, engine):
        """Scores in the gap zones (e.g., +30) should default to P1."""
        # Score of +30: outside P1 range (>20), below P3 (60), below P4 (40)
        pillars = engine._determine_pillars(30.0)
        # With default config, 30 > p1_high(20), < p3(60), < p4(40) → empty → defaults to P1
        assert 1 in pillars

    def test_pillars_always_sorted(self, engine):
        """Active pillars list should always be sorted."""
        for score in [-80, -50, -10, 0, 10, 50, 80]:
            pillars = engine._determine_pillars(float(score))
            assert pillars == sorted(pillars)


# ── EMA/RSI Computation Tests ───────────────────────────────────


class TestTechnicalHelpers:
    """Test the internal EMA and RSI computation methods."""

    def test_ema_follows_data(self):
        """EMA should trend with the data direction."""
        data = np.array([10.0 + i for i in range(20)])
        ema = BiasEngine._compute_ema(data, 9)

        # EMA should be increasing
        for i in range(5, len(ema)):
            assert ema[i] > ema[i - 1]

        # EMA should lag below the price in an uptrend
        assert ema[-1] < data[-1]

    def test_ema_first_value_equals_first_data(self):
        """EMA[0] should equal data[0]."""
        data = np.array([50.0, 55.0, 48.0, 52.0, 51.0])
        ema = BiasEngine._compute_ema(data, 3)
        assert ema[0] == 50.0

    def test_rsi_bounds(self):
        """RSI should always be between 0 and 100."""
        # All up
        closes_up = np.array([100.0 + i for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes_up, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100

        # All down
        closes_down = np.array([100.0 - i * 0.5 for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes_down, 14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_rsi_none_with_insufficient_data(self):
        """RSI returns None if not enough data for the period."""
        closes = np.array([100.0, 101.0, 99.0])
        rsi = BiasEngine._compute_rsi(closes, 14)
        assert rsi is None

    def test_rsi_100_for_all_gains(self):
        """RSI should be 100 if there are only gains (no losses)."""
        closes = np.array([100.0 + i * 2.0 for i in range(20)])
        rsi = BiasEngine._compute_rsi(closes, 14)
        assert rsi == 100.0


# ── Edge Cases ───────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_extreme_positive_score(self, engine):
        """Extreme uptrend + low VIX should push score bullish.

        Note: With flow/regime/levels at 0 (no data), the score is more moderate.
        Core technicals only contribute ~45% of total weight (flow=25%, regime=10%, levels=10% = 0).
        Score of +20 is a clear bullish signal from technicals alone.
        """
        bars = _uptrend_bars(30, step=8.0)
        result = engine.compute_bias("SPY", bars, vix_level=12.0)
        assert result.score > 15, f"Expected bullish, got {result.score}"

    def test_extreme_negative_score(self, engine):
        """Extreme downtrend + high VIX should push score bearish.

        With flow/regime/levels at 0, score is moderated. -25 is a clear bearish signal.
        """
        bars = _downtrend_bars(30, start=500.0, step=8.0)
        result = engine.compute_bias("SPY", bars, vix_level=35.0)
        assert result.score < -25, f"Expected bearish, got {result.score}"

    def test_zero_volume_bars(self, engine):
        """Bars with zero volume should not crash the VWAP calculation."""
        closes = [500.0 + i * 0.5 for i in range(30)]
        bars = _make_bars(closes, volumes=[0] * 30)
        # Should not raise
        result = engine.compute_bias("SPY", bars, vix_level=18.0)
        assert isinstance(result, BiasScore)

    def test_identical_bars(self, engine):
        """All identical bars should produce a neutral-ish score."""
        bars = _make_bars([500.0] * 30)
        result = engine.compute_bias("SPY", bars, vix_level=17.0)
        # With zero movement, most components should be near zero
        assert -30 <= result.score <= 30

    def test_single_spike_bar(self, engine):
        """A big spike on the last bar should affect bias."""
        closes = [500.0] * 29 + [520.0]  # 4% spike on last bar
        highs = [501.0] * 29 + [521.0]
        lows = [499.0] * 29 + [500.0]
        bars = _make_bars(closes, highs, lows)

        result = engine.compute_bias("SPY", bars, vix_level=18.0)
        # Spike should produce a bullish signal
        assert result.score > 0

    def test_bias_score_model_properties(self, engine):
        """Test the BiasScore model's direction property."""
        bullish = BiasScore(symbol="SPY", score=50.0, active_pillars=[3, 4], components={})
        assert bullish.direction == "BULL"

        bearish = BiasScore(symbol="SPY", score=-50.0, active_pillars=[2, 4], components={})
        assert bearish.direction == "BEAR"

        neutral = BiasScore(symbol="SPY", score=10.0, active_pillars=[1], components={})
        assert neutral.direction == "NEUTRAL"

#-------------------------------------------------------------------------------
# FILE: tests/test_pillars.py (508 lines)
#-------------------------------------------------------------------------------

"""Tests for the 4 Pillars Execution — order construction and strike selection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from esther.execution.pillars import (
    PillarExecutor,
    SpreadOrder,
    OrderLeg,
    OrderSide,
    StrikeSelection,
    find_closest_delta,
    find_wing,
)
from esther.data.tradier import OptionQuote, OptionType, OptionGreeks, TradierClient


# ── Fixtures ─────────────────────────────────────────────────────


def _make_chain(
    base_price: float = 500.0,
    num_strikes: int = 20,
    strike_width: float = 5.0,
    expiration: str = "2024-03-29",
) -> list[OptionQuote]:
    """Generate a realistic option chain for testing.

    Creates both calls and puts around the base_price with synthetic greeks.
    """
    chain: list[OptionQuote] = []
    start_strike = base_price - (num_strikes // 2) * strike_width

    for i in range(num_strikes):
        strike = start_strike + i * strike_width
        distance = (strike - base_price) / base_price

        # Synthetic delta: linear approximation
        call_delta = max(0.01, min(0.99, 0.50 - distance * 5))
        put_delta = call_delta - 1.0  # put delta is negative

        # Synthetic pricing: rough Black-Scholes-ish
        itm_call = max(0, base_price - strike)
        itm_put = max(0, strike - base_price)
        time_value = max(0.20, 3.0 - abs(distance) * 30)

        call_mid = itm_call + time_value
        put_mid = itm_put + time_value

        # Add call
        call_bid = round(max(0.01, call_mid - 0.10), 2)
        call_ask = round(call_mid + 0.10, 2)
        chain.append(OptionQuote(
            symbol=f"SPY{expiration.replace('-','')}C{int(strike*1000):08d}",
            option_type=OptionType.CALL,
            strike=strike,
            expiration=expiration,
            bid=call_bid,
            ask=call_ask,
            mid=round(call_mid, 2),
            last=round(call_mid, 2),
            volume=1000 + int(abs(call_delta) * 2000),
            open_interest=5000,
            greeks=OptionGreeks(
                delta=round(call_delta, 4),
                gamma=0.02,
                theta=-0.10,
                vega=0.15,
                rho=0.01,
                smv_vol=0.25,
            ),
        ))

        # Add put
        put_bid = round(max(0.01, put_mid - 0.10), 2)
        put_ask = round(put_mid + 0.10, 2)
        chain.append(OptionQuote(
            symbol=f"SPY{expiration.replace('-','')}P{int(strike*1000):08d}",
            option_type=OptionType.PUT,
            strike=strike,
            expiration=expiration,
            bid=put_bid,
            ask=put_ask,
            mid=round(put_mid, 2),
            last=round(put_mid, 2),
            volume=800 + int(abs(put_delta) * 2000),
            open_interest=4000,
            greeks=OptionGreeks(
                delta=round(put_delta, 4),
                gamma=0.02,
                theta=-0.10,
                vega=0.15,
                rho=-0.01,
                smv_vol=0.25,
            ),
        ))

    return chain


@pytest.fixture
def chain():
    """A standard option chain around SPY $500."""
    return _make_chain(base_price=500.0)


@pytest.fixture
def executor():
    """Create a PillarExecutor with a mocked TradierClient."""
    with patch("esther.execution.pillars.config") as mock_config:
        from esther.core.config import PillarsConfig
        mock_config.return_value.pillars = PillarsConfig()

        mock_client = AsyncMock(spec=TradierClient)
        mock_client.place_order = AsyncMock(return_value={"order": {"id": "12345", "status": "ok"}})
        mock_client.place_multileg_order = AsyncMock(return_value={"order": {"id": "12345", "status": "ok"}})

        return PillarExecutor(mock_client)


# ── Strike Selection Tests ───────────────────────────────────────


class TestFindClosestDelta:
    """Test delta-based strike selection."""

    def test_find_put_at_target_delta(self, chain):
        """Should find the put closest to target delta 0.16."""
        result = find_closest_delta(chain, 0.16, OptionType.PUT)

        assert result is not None
        assert result.option_type == OptionType.PUT
        assert result.greeks is not None
        # Put deltas are negative, we match abs(delta) to target
        assert abs(abs(result.greeks.delta) - 0.16) < 0.15

    def test_find_call_at_target_delta(self, chain):
        """Should find the call closest to target delta 0.25."""
        result = find_closest_delta(chain, 0.25, OptionType.CALL)

        assert result is not None
        assert result.option_type == OptionType.CALL
        assert result.greeks is not None
        assert abs(abs(result.greeks.delta) - 0.25) < 0.15

    def test_find_atm_delta(self, chain):
        """Finding delta ~0.50 should return near-ATM option."""
        result = find_closest_delta(chain, 0.50, OptionType.CALL)

        assert result is not None
        # ATM strike should be near 500
        assert abs(result.strike - 500.0) <= 10

    def test_returns_none_for_empty_chain(self):
        """Empty chain should return None."""
        result = find_closest_delta([], 0.16, OptionType.PUT)
        assert result is None

    def test_skips_options_without_greeks(self):
        """Options without greeks should be skipped."""
        chain = [
            OptionQuote(
                symbol="SPY_NO_GREEKS",
                option_type=OptionType.CALL,
                strike=500.0,
                expiration="2024-03-29",
                bid=5.0,
                ask=5.20,
                mid=5.10,
                last=5.10,
                volume=1000,
                open_interest=5000,
                greeks=None,
            ),
        ]
        result = find_closest_delta(chain, 0.50, OptionType.CALL)
        assert result is None

    def test_skips_zero_bid_options(self):
        """Options with zero bid should be skipped (no market)."""
        chain = [
            OptionQuote(
                symbol="SPY_ZERO_BID",
                option_type=OptionType.CALL,
                strike=500.0,
                expiration="2024-03-29",
                bid=0.0,
                ask=5.20,
                mid=2.60,
                last=2.60,
                volume=1000,
                open_interest=5000,
                greeks=OptionGreeks(delta=0.50, gamma=0.02, theta=-0.1, vega=0.15, rho=0.01, smv_vol=0.25),
            ),
        ]
        result = find_closest_delta(chain, 0.50, OptionType.CALL)
        assert result is None


class TestFindWing:
    """Test wing strike selection for spreads."""

    def test_find_put_wing(self, chain):
        """Put wing should be below the short strike by wing_width."""
        # Short put at 495
        wing = find_wing(chain, 495.0, 5.0, OptionType.PUT)

        assert wing is not None
        assert wing.option_type == OptionType.PUT
        assert wing.strike == 490.0

    def test_find_call_wing(self, chain):
        """Call wing should be above the short strike by wing_width."""
        wing = find_wing(chain, 505.0, 5.0, OptionType.CALL)

        assert wing is not None
        assert wing.option_type == OptionType.CALL
        assert wing.strike == 510.0

    def test_wing_fallback_to_closest(self, chain):
        """If exact wing strike not available, find closest."""
        # Request a strike that might not exist exactly
        wing = find_wing(chain, 497.0, 5.0, OptionType.PUT)
        assert wing is not None
        assert wing.option_type == OptionType.PUT


# ── Iron Condor Tests ────────────────────────────────────────────


class TestIronCondorBuilder:
    """Test P1 Iron Condor order construction."""

    @pytest.mark.asyncio
    async def test_builds_4_legs(self, executor, chain):
        """Iron condor should have exactly 4 legs."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert len(order.legs) == 4
        assert order.pillar == 1
        assert order.symbol == "SPY"

    @pytest.mark.asyncio
    async def test_correct_leg_sides(self, executor, chain):
        """Should have 2 sells and 2 buys."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        sells = [l for l in order.legs if l.side == OrderSide.SELL_TO_OPEN]
        buys = [l for l in order.legs if l.side == OrderSide.BUY_TO_OPEN]
        assert len(sells) == 2
        assert len(buys) == 2

    @pytest.mark.asyncio
    async def test_correct_option_types(self, executor, chain):
        """Should have puts on one side and calls on the other."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        puts = [l for l in order.legs if l.option_type == OptionType.PUT]
        calls = [l for l in order.legs if l.option_type == OptionType.CALL]
        assert len(puts) == 2
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_positive_credit(self, executor, chain):
        """Iron condor should collect a positive net credit."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert order.net_price > 0, f"Expected positive credit, got {order.net_price}"
        assert order.order_type == "credit"

    @pytest.mark.asyncio
    async def test_max_loss_calculated(self, executor, chain):
        """Max loss should be wing_width - credit."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert order.max_loss > 0
        assert order.max_profit > 0

    @pytest.mark.asyncio
    async def test_quantity_propagated(self, executor, chain):
        """Quantity should be set on all legs."""
        order = await executor.build_iron_condor("SPY", chain, quantity=5, expiration="2024-03-29")

        assert order is not None
        assert order.quantity == 5
        for leg in order.legs:
            assert leg.quantity == 5


# ── Bear Call Spread Tests ───────────────────────────────────────


class TestBearCallBuilder:
    """Test P2 Bear Call Spread order construction."""

    @pytest.mark.asyncio
    async def test_builds_2_legs(self, executor, chain):
        """Bear call should have exactly 2 legs."""
        order = await executor.build_bear_call("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert len(order.legs) == 2
        assert order.pillar == 2

    @pytest.mark.asyncio
    async def test_short_call_and_long_call(self, executor, chain):
        """Should sell one call and buy one further OTM call."""
        order = await executor.build_bear_call("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        short_leg = next(l for l in order.legs if l.side == OrderSide.SELL_TO_OPEN)
        long_leg = next(l for l in order.legs if l.side == OrderSide.BUY_TO_OPEN)

        assert short_leg.option_type == OptionType.CALL
        assert long_leg.option_type == OptionType.CALL
        # Long call should be at a higher strike
        assert long_leg.strike > short_leg.strike

    @pytest.mark.asyncio
    async def test_credit_order(self, executor, chain):
        """Bear call should be a credit spread."""
        order = await executor.build_bear_call("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert order.order_type == "credit"
        assert order.net_price > 0


# ── Bull Put Spread Tests ────────────────────────────────────────


class TestBullPutBuilder:
    """Test P3 Bull Put Spread order construction."""

    @pytest.mark.asyncio
    async def test_builds_2_legs(self, executor, chain):
        """Bull put should have exactly 2 legs."""
        order = await executor.build_bull_put("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert len(order.legs) == 2
        assert order.pillar == 3

    @pytest.mark.asyncio
    async def test_short_put_and_long_put(self, executor, chain):
        """Should sell one put and buy one further OTM put."""
        order = await executor.build_bull_put("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        short_leg = next(l for l in order.legs if l.side == OrderSide.SELL_TO_OPEN)
        long_leg = next(l for l in order.legs if l.side == OrderSide.BUY_TO_OPEN)

        assert short_leg.option_type == OptionType.PUT
        assert long_leg.option_type == OptionType.PUT
        # Long put should be at a lower strike (further OTM)
        assert long_leg.strike < short_leg.strike

    @pytest.mark.asyncio
    async def test_credit_order(self, executor, chain):
        """Bull put should be a credit spread."""
        order = await executor.build_bull_put("SPY", chain, quantity=1, expiration="2024-03-29")

        assert order is not None
        assert order.order_type == "credit"
        assert order.net_price > 0


# ── Directional Scalp Tests ─────────────────────────────────────


class TestDirectionalScalpBuilder:
    """Test P4 Directional Scalp order construction."""

    @pytest.mark.asyncio
    async def test_bull_scalp_buys_call(self, executor, chain):
        """Bull scalp should buy a call."""
        order = await executor.build_directional_scalp(
            "SPY", chain, direction="BULL", quantity=1, expiration="2024-03-29"
        )

        assert order is not None
        assert len(order.legs) == 1
        assert order.legs[0].side == OrderSide.BUY_TO_OPEN
        assert order.legs[0].option_type == OptionType.CALL
        assert order.pillar == 4

    @pytest.mark.asyncio
    async def test_bear_scalp_buys_put(self, executor, chain):
        """Bear scalp should buy a put."""
        order = await executor.build_directional_scalp(
            "SPY", chain, direction="BEAR", quantity=1, expiration="2024-03-29"
        )

        assert order is not None
        assert len(order.legs) == 1
        assert order.legs[0].side == OrderSide.BUY_TO_OPEN
        assert order.legs[0].option_type == OptionType.PUT

    @pytest.mark.asyncio
    async def test_debit_order(self, executor, chain):
        """Directional scalp should be a debit (we pay for it)."""
        order = await executor.build_directional_scalp(
            "SPY", chain, direction="BULL", quantity=1, expiration="2024-03-29"
        )

        assert order is not None
        assert order.order_type == "debit"
        assert order.net_price > 0

    @pytest.mark.asyncio
    async def test_atm_ish_strike(self, executor, chain):
        """Should select a strike with delta in the 0.40-0.55 range."""
        order = await executor.build_directional_scalp(
            "SPY", chain, direction="BULL", quantity=1, expiration="2024-03-29"
        )

        assert order is not None
        # Strike should be near ATM (500)
        assert abs(order.legs[0].strike - 500.0) <= 20


# ── Order Submission Tests ───────────────────────────────────────


class TestOrderSubmission:
    """Test order submission routing."""

    @pytest.mark.asyncio
    async def test_single_leg_uses_place_order(self, executor, chain):
        """Single-leg orders (P4) should use place_order."""
        order = await executor.build_directional_scalp(
            "SPY", chain, direction="BULL", quantity=1, expiration="2024-03-29"
        )
        assert order is not None

        await executor.submit_order(order)
        executor.client.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_leg_uses_multileg_order(self, executor, chain):
        """Multi-leg orders (P1-P3) should use place_multileg_order."""
        order = await executor.build_iron_condor("SPY", chain, quantity=1, expiration="2024-03-29")
        assert order is not None

        await executor.submit_order(order)
        executor.client.place_multileg_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_multileg_passes_correct_legs(self, executor, chain):
        """Multileg order should pass all leg data to the API."""
        order = await executor.build_bear_call("SPY", chain, quantity=2, expiration="2024-03-29")
        assert order is not None

        await executor.submit_order(order)

        call_args = executor.client.place_multileg_order.call_args
        assert call_args.kwargs["symbol"] == "SPY"
        assert len(call_args.kwargs["legs"]) == 2
        assert call_args.kwargs["order_type"] == "credit"


# ── Edge Cases ───────────────────────────────────────────────────


class TestPillarEdgeCases:
    """Test edge cases in order construction."""

    @pytest.mark.asyncio
    async def test_empty_chain_returns_none(self, executor):
        """Empty option chain should return None for all pillars."""
        assert await executor.build_iron_condor("SPY", [], quantity=1) is None
        assert await executor.build_bear_call("SPY", [], quantity=1) is None
        assert await executor.build_bull_put("SPY", [], quantity=1) is None
        assert await executor.build_directional_scalp("SPY", [], "BULL", quantity=1) is None

    @pytest.mark.asyncio
    async def test_chain_with_no_greeks_returns_none(self, executor):
        """Chain with no greeks should return None."""
        chain = [
            OptionQuote(
                symbol="SPY_NO_GREEKS",
                option_type=OptionType.CALL,
                strike=500.0,
                expiration="2024-03-29",
                bid=5.0, ask=5.20, mid=5.10, last=5.10,
                volume=1000, open_interest=5000,
                greeks=None,
            ),
            OptionQuote(
                symbol="SPY_NO_GREEKS_P",
                option_type=OptionType.PUT,
                strike=500.0,
                expiration="2024-03-29",
                bid=5.0, ask=5.20, mid=5.10, last=5.10,
                volume=1000, open_interest=5000,
                greeks=None,
            ),
        ]
        assert await executor.build_iron_condor("SPY", chain, quantity=1) is None

#-------------------------------------------------------------------------------
# FILE: tests/test_premarket.py (394 lines)
#-------------------------------------------------------------------------------

"""Tests for the PreMarketResearcher and PreMarketReport."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from esther.signals.premarket import PreMarketReport, PreMarketResearcher

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# PreMarketReport model tests
# ---------------------------------------------------------------------------


class TestPreMarketReport:
    def test_default_report(self):
        report = PreMarketReport(generated_at=datetime.now(ET))
        assert report.spy_price == 0.0
        assert report.flow_direction == ""
        assert report.confidence == 0.0
        assert report.sizing_modifier == 1.0
        assert report.chop_warning is False
        assert report.key_levels == {}
        assert report.top_flow_alerts == []
        assert report.watchlist_in_zone == []

    def test_report_with_values(self):
        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=634.09,
            spx_price=6368.0,
            vix_level=31.05,
            futures_direction="DOWN",
            flow_direction="BEARISH",
            flow_bias_score=-14.1,
            confidence=0.72,
            recommended_pillars=[1],
            vix_regime="IC_SWEET_SPOT",
        )
        assert report.spy_price == 634.09
        assert report.flow_direction == "BEARISH"
        assert report.recommended_pillars == [1]


# ---------------------------------------------------------------------------
# PreMarketResearcher tests
# ---------------------------------------------------------------------------


@pytest.fixture
def researcher():
    """Create a PreMarketResearcher with mocked dependencies."""
    flow = MagicMock()
    levels = MagicMock()
    regime = MagicMock()
    calendar = MagicMock()
    watchlist = MagicMock()

    r = PreMarketResearcher(
        symbols=["SPY", "SPX"],
        flow_analyzer=flow,
        level_tracker=levels,
        regime_detector=regime,
        calendar_module=calendar,
        watchlist_monitor=watchlist,
    )
    return r


class TestAnalyzeFlowDirection:
    @pytest.mark.asyncio
    async def test_neutral_when_no_entries(self, researcher):
        researcher._flow.get_flow = AsyncMock(return_value=[])

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "NEUTRAL"
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_bullish_direction(self, researcher):
        mock_summary = MagicMock()
        mock_summary.flow_bias_score = 35.0
        mock_summary.total_call_premium = 1_000_000
        mock_summary.total_put_premium = 200_000

        researcher._flow.get_flow = AsyncMock(return_value=[MagicMock()])
        researcher._flow.analyze_flow = MagicMock(return_value=mock_summary)

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "BULLISH"
        assert score == 35.0

    @pytest.mark.asyncio
    async def test_bearish_direction(self, researcher):
        mock_summary = MagicMock()
        mock_summary.flow_bias_score = -30.0
        mock_summary.total_call_premium = 200_000
        mock_summary.total_put_premium = 1_000_000

        researcher._flow.get_flow = AsyncMock(return_value=[MagicMock()])
        researcher._flow.analyze_flow = MagicMock(return_value=mock_summary)

        direction, score = await researcher.analyze_flow_direction()
        assert direction == "BEARISH"
        assert score == -30.0


class TestCheckEconomicCalendar:
    @pytest.mark.asyncio
    async def test_no_events(self, researcher):
        researcher._calendar.get_events_today = MagicMock(return_value=[])
        researcher._calendar.is_event_day = MagicMock(return_value=False)
        researcher._calendar.should_reduce_size = MagicMock(
            return_value=(False, 1.0)
        )

        result = await researcher.check_economic_calendar()
        assert result["is_event_day"] is False
        assert result["event_name"] == ""

    @pytest.mark.asyncio
    async def test_cpi_event_day(self, researcher):
        mock_event = MagicMock()
        mock_event.name = "CPI Release"
        mock_event.impact = MagicMock()
        mock_event.impact.value = "HIGH"
        # Make it match EventImpact.HIGH comparison
        from esther.signals.calendar import EventImpact

        mock_event.impact = EventImpact.HIGH

        researcher._calendar.get_events_today = MagicMock(
            return_value=[mock_event]
        )
        researcher._calendar.is_event_day = MagicMock(return_value=True)
        researcher._calendar.should_reduce_size = MagicMock(
            return_value=(True, 0.5)
        )

        result = await researcher.check_economic_calendar()
        assert result["is_event_day"] is True
        assert result["event_name"] == "CPI Release"
        assert result["sizing_modifier"] == 0.5


class TestGenerateTradePlan:
    @pytest.mark.asyncio
    async def test_ic_sweet_spot(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=634.0,
            spx_price=6340.0,
            vix_level=22.0,
            flow_bias_score=5.0,
            regime_state="BULLISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "IC_SWEET_SPOT"
        assert 1 in result.recommended_pillars
        assert result.recommended_direction == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_bearish_plan(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=620.0,
            spx_price=6200.0,
            vix_level=32.0,
            flow_bias_score=-55.0,
            regime_state="BEARISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "ELEVATED"
        assert result.recommended_direction == "BEAR"
        assert 2 in result.recommended_pillars
        assert 4 in result.recommended_pillars  # strong bear momentum

    @pytest.mark.asyncio
    async def test_panic_sizing(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=580.0,
            spx_price=5800.0,
            vix_level=42.0,
            flow_bias_score=-10.0,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.vix_regime == "PANIC"
        assert result.sizing_modifier == 0.25

    @pytest.mark.asyncio
    async def test_event_day_sizing(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=0.65
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            flow_bias_score=0.0,
            is_event_day=True,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.sizing_modifier == 0.5

    @pytest.mark.asyncio
    async def test_chop_warning(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            flow_bias_score=3.0,
            overnight_range_pct=0.3,
            regime_state="BULLISH",
        )
        result = await researcher.generate_trade_plan(report)
        assert result.chop_warning is True
        # Confidence should be reduced by chop penalty
        assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_ic_strikes_generated(self, researcher):
        researcher._calendar.get_confidence_adjustment = MagicMock(
            return_value=1.0
        )

        report = PreMarketReport(
            generated_at=datetime.now(ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=22.0,
            flow_bias_score=0.0,
        )
        result = await researcher.generate_trade_plan(report)
        assert result.ic_strikes != ""
        assert "calls" in result.ic_strikes
        assert "puts" in result.ic_strikes


class TestFormatTelegramReport:
    def test_basic_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=634.09,
            spx_price=6368.0,
            vix_level=31.05,
            futures_direction="DOWN",
            overnight_range_pct=0.8,
            key_levels={
                "SPY": {
                    "pm_high": 636.50,
                    "pm_low": 632.10,
                    "prev_close": 645.09,
                    "prev_high": 646.0,
                    "prev_low": 641.0,
                    "sma_200": 661.0,
                    "sma_50": 650.0,
                }
            },
            flow_direction="BEARISH",
            flow_bias_score=-14.1,
            put_call_ratio=1.5,
            top_flow_alerts=[
                {
                    "symbol": "SPX",
                    "strike": 6480,
                    "type": "call",
                    "premium": 112000,
                    "side": "buy",
                    "has_sweep": False,
                },
            ],
            dark_pool_summary="NVDA selling at $166.60 (50,000 shares)",
            max_pain={"SPY": 653.0},
            regime_state="BEARISH",
            sma_20=640.0,
            sma_50=650.0,
            sma_200=661.0,
            is_event_day=False,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            ic_strikes="IC: 6440/6450 calls, 6300/6290 puts",
            confidence=0.72,
            sizing_modifier=1.0,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)

        assert "ESTHER PRE-MARKET REPORT" in msg
        assert "Tue Mar 31" in msg
        assert "$634.09" in msg
        assert "6,368" in msg
        assert "31.05" in msg
        assert "🔴" in msg  # VIX >= 30
        assert "BEARISH" in msg
        assert "PM High: $636.50" in msg
        assert "Max Pain: $653" in msg
        assert "SPX 6480C $112K" in msg
        assert "NVDA selling" in msg
        assert "IC: " in msg or "IC Zone:" in msg
        assert "72%" in msg

    def test_event_day_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=25.0,
            futures_direction="FLAT",
            is_event_day=True,
            event_name="CPI Release",
            expected_move=45.0,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.5,
            sizing_modifier=0.5,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "CPI Release" in msg
        assert "±45 pts" in msg
        assert "half (event day)" in msg

    def test_leap_watchlist_formatting(self, researcher):
        # Mock the watchlist entry lookup
        mock_entry = MagicMock()
        mock_entry.symbol = "NVDA"
        mock_entry.current_price = 166.0
        mock_entry.buy_zone_low = 155.0
        mock_entry.buy_zone_high = 165.0
        researcher._watchlist.watchlist = [mock_entry]

        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            futures_direction="UP",
            watchlist_approaching=["NVDA"],
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.6,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "LEAP WATCH" in msg
        assert "NVDA" in msg
        assert "approaching" in msg

    def test_chop_warning_formatting(self, researcher):
        report = PreMarketReport(
            generated_at=datetime(2026, 3, 31, 9, 15, tzinfo=ET),
            spy_price=640.0,
            spx_price=6400.0,
            vix_level=20.0,
            futures_direction="FLAT",
            chop_warning=True,
            recommended_pillars=[1],
            recommended_direction="NEUTRAL",
            confidence=0.3,
            vix_regime="IC_SWEET_SPOT",
        )

        msg = researcher.format_telegram_report(report)
        assert "CHOP WARNING" in msg

#-------------------------------------------------------------------------------
# FILE: tests/test_quality_filter.py (315 lines)
#-------------------------------------------------------------------------------

"""Tests for the Quality Filter — option trade quality gate."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from esther.signals.quality_filter import QualityFilter, QualityCheck, FilterResult
from esther.data.tradier import OptionQuote, OptionType, OptionGreeks


# ── Fixtures ─────────────────────────────────────────────────────


def _make_option(
    bid: float = 5.0,
    ask: float = 5.20,
    volume: int = 1000,
    strike: float = 500.0,
    option_type: OptionType = OptionType.CALL,
    delta: float = 0.25,
    mid_iv: float = 0.35,
) -> OptionQuote:
    """Create an OptionQuote for testing."""
    return OptionQuote(
        symbol=f"SPY240329C{int(strike):05d}000",
        option_type=option_type,
        strike=strike,
        expiration="2024-03-29",
        bid=bid,
        ask=ask,
        mid=round((bid + ask) / 2, 2),
        last=round((bid + ask) / 2, 2),
        volume=volume,
        open_interest=5000,
        greeks=OptionGreeks(
            delta=delta,
            gamma=0.05,
            theta=-0.10,
            vega=0.15,
            rho=0.01,
            smv_vol=mid_iv,
        ),
    )


@pytest.fixture
def quality_filter():
    """Create a QualityFilter with default config."""
    with patch("esther.signals.quality_filter.config") as mock_config:
        from esther.core.config import QualityConfig
        mock_config.return_value.quality = QualityConfig()
        return QualityFilter()


# ── Spread Width Tests ───────────────────────────────────────────


class TestSpreadWidthFiltering:
    """Test bid-ask spread filtering."""

    def test_tight_spread_passes(self, quality_filter):
        """A tight bid-ask spread (<20% of mid) should pass."""
        option = _make_option(bid=5.00, ask=5.10)  # ~2% spread
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed
        assert result.spread_pct < 0.20

    def test_wide_spread_rejected(self, quality_filter):
        """A wide bid-ask spread (>20% of mid) should be rejected."""
        option = _make_option(bid=1.00, ask=1.50)  # 40% spread
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert any("WIDE_SPREAD" in r for r in result.reasons)

    def test_borderline_spread(self, quality_filter):
        """A spread exactly at 20% should still pass (equal, not greater)."""
        # mid = 5.0, spread = 1.0, spread_pct = 0.20
        option = _make_option(bid=4.50, ask=5.50)  # exactly 20%
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        # 20% is exactly the threshold — should pass (not strictly >)
        assert result.passed

    def test_zero_bid_gets_max_penalty(self, quality_filter):
        """Zero bid means no market, should get 100% spread penalty."""
        option = _make_option(bid=0.0, ask=5.00)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert result.spread_pct == 1.0

    def test_zero_ask_gets_max_penalty(self, quality_filter):
        """Zero ask means no market, should get 100% spread penalty."""
        option = _make_option(bid=0.0, ask=0.0)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert result.spread_pct == 1.0

    def test_spread_pct_calculation(self, quality_filter):
        """Verify the spread percentage is calculated correctly."""
        # bid=4.0, ask=5.0 → mid=4.5, spread=1.0, pct = 1.0/4.5 ≈ 0.2222
        option = _make_option(bid=4.0, ask=5.0)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        expected_pct = 1.0 / 4.5
        assert abs(result.spread_pct - expected_pct) < 0.01


# ── Volume Threshold Tests ───────────────────────────────────────


class TestVolumeThresholds:
    """Test per-tier volume filtering."""

    def test_tier1_high_volume_passes(self, quality_filter):
        """Tier 1 with volume > 500 should pass."""
        option = _make_option(volume=1000)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed

    def test_tier1_low_volume_rejected(self, quality_filter):
        """Tier 1 with volume < 500 should be rejected."""
        option = _make_option(volume=100)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert not result.passed
        assert any("LOW_VOLUME" in r for r in result.reasons)

    def test_tier2_volume_threshold(self, quality_filter):
        """Tier 2 has a lower volume threshold (100)."""
        # Passes at 100
        option = _make_option(volume=150)
        result = quality_filter.check(option, tier="tier2", pillar=2, iv_rank=50.0)
        assert result.passed

        # Fails below 100
        option = _make_option(volume=50)
        result = quality_filter.check(option, tier="tier2", pillar=2, iv_rank=50.0)
        assert not result.passed

    def test_tier3_volume_threshold(self, quality_filter):
        """Tier 3 has threshold of 200."""
        option = _make_option(volume=250)
        result = quality_filter.check(option, tier="tier3", pillar=2, iv_rank=50.0)
        assert result.passed

        option = _make_option(volume=100)
        result = quality_filter.check(option, tier="tier3", pillar=2, iv_rank=50.0)
        assert not result.passed

    def test_volume_bonus_for_high_activity(self, quality_filter):
        """Very high volume should give a quality score bonus."""
        low_vol = _make_option(volume=600)
        high_vol = _make_option(volume=5000)

        result_low = quality_filter.check(low_vol, tier="tier1", pillar=2, iv_rank=50.0)
        result_high = quality_filter.check(high_vol, tier="tier1", pillar=2, iv_rank=50.0)

        # Both pass, but high volume should have a higher score
        assert result_low.passed
        assert result_high.passed
        assert result_high.quality_score >= result_low.quality_score


# ── IV Rank Tests ────────────────────────────────────────────────


class TestIVRankFiltering:
    """Test IV rank filtering for different pillars."""

    def test_iron_condor_needs_high_iv(self, quality_filter):
        """P1 (Iron Condors) need IV rank >= 50."""
        option = _make_option(volume=1000)

        # IV rank 60 — should pass
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=60.0)
        assert result.passed

        # IV rank 30 — should fail
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=30.0)
        assert not result.passed
        assert any("LOW_IV_RANK" in r for r in result.reasons)

    def test_spreads_need_moderate_iv(self, quality_filter):
        """P2/P3 (spreads) need IV rank in 30-70 range."""
        option = _make_option(volume=1000)

        # IV rank 50 — should pass for P2
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)
        assert result.passed

        # IV rank 80 — too high, should fail for P2
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=80.0)
        assert not result.passed
        assert any("IV_RANK_OUT_OF_RANGE" in r for r in result.reasons)

        # IV rank 20 — too low, should fail for P3
        result = quality_filter.check(option, tier="tier1", pillar=3, iv_rank=20.0)
        assert not result.passed

    def test_p4_no_iv_constraint(self, quality_filter):
        """P4 (directional scalps) have no IV rank requirement."""
        option = _make_option(volume=1000)

        # Even extreme IV ranks should pass for P4
        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=5.0)
        assert result.passed

        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=95.0)
        assert result.passed

    def test_no_iv_rank_skips_check(self, quality_filter):
        """When iv_rank is None, the IV check should be skipped."""
        option = _make_option(volume=1000)
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=None)

        # Should pass since IV check is skipped
        assert result.passed
        assert not any("IV_RANK" in r for r in result.reasons)


# ── Spread Pair Tests ────────────────────────────────────────────


class TestSpreadPairChecks:
    """Test quality checking for spread pairs (two-leg positions)."""

    def test_both_legs_good(self, quality_filter):
        """Both legs passing should produce a passing result."""
        short = _make_option(bid=3.00, ask=3.20, volume=1500)
        long = _make_option(bid=1.50, ask=1.65, volume=1200)

        result = quality_filter.check_spread_pair(
            short, long, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert result.passed

    def test_one_leg_bad_fails_pair(self, quality_filter):
        """If either leg fails, the whole spread should fail."""
        good_leg = _make_option(bid=3.00, ask=3.20, volume=1500)
        bad_leg = _make_option(bid=0.01, ask=0.50, volume=10)  # terrible quality

        result = quality_filter.check_spread_pair(
            good_leg, bad_leg, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert not result.passed

    def test_pair_score_is_average(self, quality_filter):
        """When both legs pass, the score should be the average."""
        leg1 = _make_option(bid=3.00, ask=3.15, volume=2000)
        leg2 = _make_option(bid=1.50, ask=1.60, volume=1800)

        result = quality_filter.check_spread_pair(
            leg1, leg2, tier="tier1", pillar=2, iv_rank=50.0
        )
        assert result.passed
        # Score should be reasonable
        assert 50 <= result.quality_score <= 100


# ── Quality Score Tests ──────────────────────────────────────────


class TestQualityScoring:
    """Test the quality score calculation."""

    def test_perfect_option_high_score(self, quality_filter):
        """A high-quality option should get a high score."""
        option = _make_option(bid=5.00, ask=5.05, volume=5000)  # tight spread, high volume
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        assert result.passed
        assert result.quality_score > 90

    def test_mediocre_option_moderate_score(self, quality_filter):
        """A passable but not great option should get a moderate score."""
        option = _make_option(bid=5.00, ask=5.80, volume=600)
        result = quality_filter.check(option, tier="tier1", pillar=2, iv_rank=50.0)

        # May or may not pass depending on exact spread pct
        assert 0 <= result.quality_score <= 100

    def test_score_clamped_to_0_100(self, quality_filter):
        """Score should always be between 0 and 100."""
        # Worst case option
        option = _make_option(bid=0.01, ask=10.0, volume=1)
        result = quality_filter.check(option, tier="tier1", pillar=1, iv_rank=10.0)
        assert 0 <= result.quality_score <= 100

        # Best case option
        option = _make_option(bid=5.00, ask=5.01, volume=50000)
        result = quality_filter.check(option, tier="tier1", pillar=4, iv_rank=50.0)
        assert 0 <= result.quality_score <= 100


# ── QualityCheck Model Tests ────────────────────────────────────


class TestQualityCheckModel:
    """Test the QualityCheck model properties."""

    def test_passed_property(self):
        """The passed property should mirror PASS/REJECT."""
        passing = QualityCheck(result=FilterResult.PASS, quality_score=80.0)
        assert passing.passed is True

        failing = QualityCheck(result=FilterResult.REJECT, quality_score=20.0, reasons=["BAD"])
        assert failing.passed is False

#-------------------------------------------------------------------------------
# FILE: tests/test_risk_manager.py (470 lines)
#-------------------------------------------------------------------------------

"""Tests for the Risk Manager — position limits, daily loss caps, and cooldowns."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from esther.risk.risk_manager import RiskManager, RiskCheck, DailyRiskReport
from esther.execution.position_manager import PositionManager, Position, PositionStatus
from esther.data.tradier import TradierClient


# ── Fixtures ─────────────────────────────────────────────────────


def _make_position(
    symbol: str = "SPY",
    pillar: int = 1,
    tier: str = "tier1",
    pnl: float = 100.0,
    status: PositionStatus = PositionStatus.CLOSED_PROFIT,
    direction: str = "BULL",
) -> Position:
    """Create a test position."""
    return Position(
        id=f"pos_{id(symbol):04d}",
        symbol=symbol,
        pillar=pillar,
        quantity=1,
        entry_price=2.50,
        tier=tier,
        direction=direction,
        unrealized_pnl=pnl,
        status=status,
    )


@pytest.fixture
def mock_pm():
    """Create a mocked PositionManager."""
    pm = MagicMock(spec=PositionManager)
    pm.open_positions = []
    pm.closed_positions = []
    pm.get_daily_pnl.return_value = 0.0
    pm.get_position_count.return_value = 0
    return pm


@pytest.fixture
def risk_mgr(mock_pm):
    """Create a RiskManager with $100k balance."""
    with patch("esther.risk.risk_manager.config") as mock_config:
        from esther.core.config import RiskConfig
        mock_config.return_value.risk = RiskConfig()
        return RiskManager(mock_pm, account_balance=100_000.0)


# ── Tier Position Limit Tests ────────────────────────────────────


class TestTierPositionLimits:
    """Test per-tier position limits (T1: 5, T2: 3, T3: 3)."""

    def test_tier1_under_limit_approved(self, risk_mgr, mock_pm):
        """Tier 1 with fewer than 5 positions should be approved."""
        mock_pm.get_position_count.return_value = 3
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

    def test_tier1_at_limit_rejected(self, risk_mgr, mock_pm):
        """Tier 1 at max 5 positions should be rejected."""
        mock_pm.get_position_count.return_value = 5
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "POSITION_LIMIT" in result.reason

    def test_tier2_limit_is_3(self, risk_mgr, mock_pm):
        """Tier 2 max positions should be 3."""
        mock_pm.get_position_count.return_value = 3
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("GLD", "tier2", max_risk=500.0)
        assert not result.approved
        assert "POSITION_LIMIT" in result.reason

    def test_tier3_limit_is_3(self, risk_mgr, mock_pm):
        """Tier 3 max positions should be 3."""
        mock_pm.get_position_count.return_value = 2
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("NVDA", "tier3", max_risk=500.0)
        assert result.approved

        mock_pm.get_position_count.return_value = 3
        result = risk_mgr.can_open_position("NVDA", "tier3", max_risk=500.0)
        assert not result.approved

    def test_current_positions_in_result(self, risk_mgr, mock_pm):
        """RiskCheck should include current and max position counts."""
        mock_pm.get_position_count.return_value = 2
        mock_pm.get_daily_pnl.return_value = 0.0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.current_positions == 2
        assert result.max_positions == 5


# ── Daily Loss Cap Tests ─────────────────────────────────────────


class TestDailyLossCap:
    """Test daily loss cap enforcement (5% of account)."""

    def test_loss_cap_is_2_percent(self, risk_mgr):
        """Daily loss cap should be 2% of $100k = $2,000 (sovereign instruction set)."""
        assert risk_mgr.daily_loss_cap == 2_000.0

    def test_under_cap_approved(self, risk_mgr, mock_pm):
        """P&L above the loss cap should be approved."""
        mock_pm.get_daily_pnl.return_value = -1_000.0  # Lost $1k, cap is $2k
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

    def test_at_cap_rejected(self, risk_mgr, mock_pm):
        """P&L at or below the loss cap should be rejected."""
        mock_pm.get_daily_pnl.return_value = -2_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "DAILY_LOSS_CAP" in result.reason

    def test_exceeding_cap_triggers_shutdown(self, risk_mgr, mock_pm):
        """Exceeding loss cap should trigger daily shutdown."""
        mock_pm.get_daily_pnl.return_value = -3_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert risk_mgr.is_shutdown

    def test_risk_too_high_for_remaining_cap(self, risk_mgr, mock_pm):
        """Trade that would push past cap should be rejected."""
        mock_pm.get_daily_pnl.return_value = -1_500.0  # Already down $1.5k
        mock_pm.get_position_count.return_value = 0

        # Max risk of $1k would push to $2.5k loss → past the $2k cap
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=1_000.0)
        assert not result.approved
        assert "RISK_TOO_HIGH" in result.reason

    def test_shutdown_blocks_all_trades(self, risk_mgr, mock_pm):
        """Once shutdown is triggered, all subsequent trades should be rejected."""
        mock_pm.get_daily_pnl.return_value = -3_000.0
        mock_pm.get_position_count.return_value = 0

        # First trade triggers shutdown
        risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert risk_mgr.is_shutdown

        # Even a tiny trade should be rejected
        mock_pm.get_daily_pnl.return_value = 0.0  # P&L recovered somehow
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=1.0)
        assert not result.approved
        assert "DAILY_SHUTDOWN" in result.reason

    def test_daily_pnl_included_in_result(self, risk_mgr, mock_pm):
        """RiskCheck should include daily P&L and loss cap."""
        mock_pm.get_daily_pnl.return_value = -1_000.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.daily_pnl == -1_000.0
        assert result.daily_loss_cap == 2_000.0


# ── Cooldown Tests ───────────────────────────────────────────────


class TestCooldownLogic:
    """Test consecutive loss cooldowns."""

    def test_no_cooldown_initially(self, risk_mgr, mock_pm):
        """No cooldown should be active initially."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved
        assert not result.cooldown_active

    def test_single_loss_triggers_recent_loser(self, risk_mgr, mock_pm):
        """A single loss should trigger 5-min recent loser cooldown (candle guard handles longer)."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        loss_pos = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss_pos)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason

    def test_two_consecutive_losses_triggers_cooldown(self, risk_mgr, mock_pm):
        """Two consecutive losses should trigger both recent loser + cooldown."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # First loss
        loss1 = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss1)

        # Second consecutive loss
        loss2 = _make_position("SPY", pnl=-300.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss2)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason  # recent loser check hits first

    def test_win_resets_cooldown_counter(self, risk_mgr, mock_pm):
        """A win should reset the consecutive loss counter but recent loser still active."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # One loss
        loss1 = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss1)

        # A win (different symbol — SPY is on recent loser cooldown)
        win = _make_position("QQQ", pnl=150.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        # QQQ should be tradeable (no loss recorded)
        result = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert result.approved

        # SPY still blocked by recent loser
        result_spy = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result_spy.approved

    def test_cooldown_per_symbol(self, risk_mgr, mock_pm):
        """Cooldown should be per-symbol, not global."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Two consecutive losses on SPY
        for _ in range(2):
            loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # SPY should be on cooldown
        result_spy = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert not result_spy.approved

        # QQQ should still be fine
        result_qqq = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert result_qqq.approved

    def test_cooldown_expires(self, risk_mgr, mock_pm):
        """Both cooldown and recent loser should expire after duration."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Trigger cooldown
        for _ in range(2):
            loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # Manually set cooldown_until to the past AND expire recent loser
        risk_mgr._cooldowns["SPY"] = (2, datetime.now() - timedelta(minutes=1))
        risk_mgr._recent_losers["SPY"] = datetime.now() - timedelta(minutes=31)

        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved  # Both expired


# ── Trade Result Recording Tests ─────────────────────────────────


class TestTradeRecording:
    """Test trade result recording and metric tracking."""

    def test_win_increments_wins(self, risk_mgr):
        """A winning trade should increment the win counter."""
        win = _make_position("SPY", pnl=200.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        assert risk_mgr._daily_stats.winning_trades == 1
        assert risk_mgr._daily_stats.total_trades == 1

    def test_loss_increments_losses(self, risk_mgr):
        """A losing trade should increment the loss counter."""
        loss = _make_position("SPY", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        assert risk_mgr._daily_stats.losing_trades == 1
        assert risk_mgr._daily_stats.total_trades == 1

    def test_loss_triggers_shutdown_when_at_cap(self, risk_mgr, mock_pm):
        """A trade result that pushes P&L past cap should trigger shutdown."""
        mock_pm.get_daily_pnl.return_value = -5_500.0

        loss = _make_position("SPY", pnl=-1000.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        assert risk_mgr.is_shutdown


# ── Force Close Tests ────────────────────────────────────────────


class TestForceClose:
    """Test force close signaling."""

    def test_force_close_triggers_shutdown(self, risk_mgr):
        """trigger_force_close should shut down trading."""
        risk_mgr.trigger_force_close("BLACK_SWAN_RED")

        assert risk_mgr.is_shutdown
        assert "FORCE_CLOSE" in risk_mgr._risk_events[0]

    def test_force_close_logged_in_events(self, risk_mgr):
        """Force close should be recorded in risk events."""
        risk_mgr.trigger_force_close("TEST_REASON")

        assert len(risk_mgr._risk_events) == 2  # FORCE_CLOSE + SHUTDOWN
        assert any("FORCE_CLOSE" in e for e in risk_mgr._risk_events)


# ── Daily Report Tests ───────────────────────────────────────────


class TestDailyReport:
    """Test daily risk report generation."""

    def test_empty_day_report(self, risk_mgr, mock_pm):
        """Report with no trades should have zero metrics."""
        mock_pm.get_daily_pnl.return_value = 0.0
        report = risk_mgr.generate_daily_report()

        assert report.total_trades == 0
        assert report.win_rate == 0.0
        assert report.total_pnl == 0.0
        assert not report.shutdown_triggered

    def test_report_with_trades(self, risk_mgr, mock_pm):
        """Report should reflect recorded trades."""
        mock_pm.get_daily_pnl.return_value = 500.0

        win = _make_position("SPY", pnl=300.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win)

        loss = _make_position("QQQ", pnl=-100.0, status=PositionStatus.CLOSED_STOP)
        risk_mgr.record_trade_result(loss)

        win2 = _make_position("IWM", pnl=300.0, status=PositionStatus.CLOSED_PROFIT)
        risk_mgr.record_trade_result(win2)

        report = risk_mgr.generate_daily_report()

        assert report.total_trades == 3
        assert report.winning_trades == 2
        assert report.losing_trades == 1
        assert abs(report.win_rate - 0.6667) < 0.01
        assert report.total_pnl == 500.0

    def test_report_tracks_shutdown(self, risk_mgr, mock_pm):
        """Report should show if shutdown was triggered."""
        mock_pm.get_daily_pnl.return_value = -6_000.0
        mock_pm.get_position_count.return_value = 0

        risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)

        report = risk_mgr.generate_daily_report()
        assert report.shutdown_triggered


# ── Daily Reset Tests ────────────────────────────────────────────


class TestDailyReset:
    """Test daily state reset."""

    def test_reset_clears_shutdown(self, risk_mgr):
        """Reset should clear the shutdown flag."""
        risk_mgr._shutdown = True
        risk_mgr.reset_daily()

        assert not risk_mgr.is_shutdown

    def test_reset_clears_cooldowns(self, risk_mgr):
        """Reset should clear all cooldowns."""
        risk_mgr._cooldowns["SPY"] = (2, datetime.now() + timedelta(minutes=30))
        risk_mgr.reset_daily()

        assert len(risk_mgr._cooldowns) == 0

    def test_reset_clears_counters(self, risk_mgr):
        """Reset should zero all daily counters."""
        risk_mgr._daily_stats.total_trades = 10
        risk_mgr._daily_stats.winning_trades = 7
        risk_mgr._daily_stats.losing_trades = 3
        risk_mgr.reset_daily()

        assert risk_mgr._daily_stats.total_trades == 0
        assert risk_mgr._daily_stats.winning_trades == 0
        assert risk_mgr._daily_stats.losing_trades == 0

    def test_reset_updates_balance(self, risk_mgr):
        """Reset with new balance should update the account balance."""
        risk_mgr.reset_daily(new_balance=120_000.0)

        assert risk_mgr.account_balance == 120_000.0
        assert risk_mgr.daily_loss_cap == 2_400.0  # 2% of 120k

    def test_reset_keeps_balance_if_none(self, risk_mgr):
        """Reset without new_balance should keep current balance."""
        risk_mgr.reset_daily()
        assert risk_mgr.account_balance == 100_000.0


# ── Integration-Style Tests ──────────────────────────────────────


class TestRiskManagerIntegration:
    """Test realistic sequences of risk manager operations."""

    def test_full_day_scenario(self, risk_mgr, mock_pm):
        """Simulate a full trading day with mixed results."""
        mock_pm.get_daily_pnl.return_value = 0.0
        mock_pm.get_position_count.return_value = 0

        # Morning: two wins on SPY
        for _ in range(2):
            win = _make_position("SPY", pnl=150.0)
            risk_mgr.record_trade_result(win)

        # SPY should still be tradeable (no losses)
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

        # Midday: two losses on QQQ → recent loser block
        for _ in range(2):
            loss = _make_position("QQQ", pnl=-200.0, status=PositionStatus.CLOSED_STOP)
            risk_mgr.record_trade_result(loss)

        # QQQ should be blocked by recent loser
        result = risk_mgr.can_open_position("QQQ", "tier1", max_risk=500.0)
        assert not result.approved
        assert "RECENT_LOSER" in result.reason

        # SPY should still be fine
        result = risk_mgr.can_open_position("SPY", "tier1", max_risk=500.0)
        assert result.approved

        # Generate report
        report = risk_mgr.generate_daily_report()
        assert report.total_trades == 4
        assert report.winning_trades == 2
        assert report.losing_trades == 2


#===============================================================================
# END OF COMPLETE SOURCE V5 — 19823 source lines consolidated
#===============================================================================