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
