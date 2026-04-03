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
            # 180 Acrobat Flip 20x: trigger inversion on EVERY loss (1 loss) instead of waiting for 3.
            if tracker.consecutive_losses >= 1: # was self._cfg.consecutive_loss_trigger
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
