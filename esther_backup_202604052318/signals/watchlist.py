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
