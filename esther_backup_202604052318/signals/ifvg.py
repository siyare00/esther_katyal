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

        try:
            fvgs_5m = self.detect_fvgs(bars_5m)
            fvgs_1m = self.detect_fvgs(bars_1m)

            # Store active FVGs
            self._active_fvgs[symbol] = fvgs_5m + fvgs_1m

            # Check for reversals
            signal_5m = self.detect_ifvg_reversal(bars_5m, fvgs_5m)
            signal_1m = self.detect_ifvg_reversal(bars_1m, fvgs_1m)
        except Exception as e:
            logger.error("ifvg_calculation_error_labyrinth", symbol=symbol, error=str(e), exc_info=True)
            return None

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
