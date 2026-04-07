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
