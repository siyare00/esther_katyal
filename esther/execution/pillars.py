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
        wing_width = 5  # 5-point wings — live small account

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
        wing_width = 5  # 5-point wings — live small account

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
        wing_width = 5  # 5-point wings — live small account

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
        wing_width = 5  # 5-point wings — live small account

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
