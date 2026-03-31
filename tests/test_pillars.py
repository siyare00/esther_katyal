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
