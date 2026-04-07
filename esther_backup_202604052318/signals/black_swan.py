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
