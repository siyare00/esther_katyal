"""Main Orchestrator Engine — The Brain of Esther.

Chains all modules together in the main trading loop:
    1. Black Swan check → gate all activity
    2. Bias Engine → directional scoring
    3. Inversion Engine → self-correction
    3c. Alpha (Sonnet) → market condition analysis (once per cycle)
    3d. Neo (Opus) → periodic health check + error diagnosis + SELF-HEALING
    4. Quality Filter → option quality gate
    5. AI Debate → Alpha context + Kimi/Riki/Abi/Kage argue the trade
    6. AI Sizing → Kelly + capital recycler
    7. Pillar Executor → build and submit orders
    8. Position Manager → track and manage open positions
    9. Risk Manager → enforce limits throughout

6 AI Agents:
    Alpha 🌐 — Market condition analyzer (Sonnet) — runs once per scan cycle
    Kimi 🔬  — Research analyst (Sonnet) — quantified risk analysis per trade
    Riki 🐂  — The bull (Sonnet) — argues for going long
    Abi 🐻   — The bear (Sonnet) — argues for going short
    Kage ⚖️  — The judge (Sonnet) — final verdict per trade
    Neo 🛡️   — Self-healing watchdog (Opus) — diagnoses errors, patches code live, hot-reloads

5 Pillars:
    P1 — Iron Condors (neutral zone)
    P2 — Bear Call Spreads (bearish)
    P3 — Bull Put Spreads (bullish)
    P4 — Directional Scalps (high conviction)
    P5 — Butterfly Spreads (moderate conviction)

Runs every scan_interval_seconds during market hours (9:30 AM - 4:00 PM ET).
Pre-market scan at 9:15 AM. EOD cleanup at 3:45 PM.
"""

from __future__ import annotations

import asyncio
import json
import sys
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
from esther.ai.alpha import AlphaAgent, AlphaReport
from esther.ai.debate import AIDebate, DebateInput, DebateVerdict
from esther.ai.neo import NeoAgent, NeoAlert, NeoHealthCheck
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
        self._alpha: AlphaAgent | None = None
        self._debate: AIDebate | None = None
        self._neo: NeoAgent | None = None
        self._sizer: AISizer | None = None
        self._executor: PillarExecutor | None = None
        self._position_mgr: PositionManager | None = None
        self._risk_mgr: RiskManager | None = None
        self._journal = TradeJournal()
        self._reentry = ReentryGuard(required_candles=2)
        self._sage = Sage()
        self._last_sage_scan: float = 0.0  # timestamp of last intraday sage scan
        self._module_mtimes: dict[str, float] = {}  # for hot-reloading signals

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

        # Neo error tracking
        self._errors_today: int = 0
        self._rejections_today: int = 0

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
        self._alpha = AlphaAgent()
        self._debate = AIDebate()
        self._neo = NeoAgent(health_check_interval=self._cfg.ai.neo_health_check_interval)
        self._sizer = AISizer()
        self._executor = PillarExecutor(self._client)
        self._position_mgr = PositionManager(self._client)

    def _reinit_healed_component(self, neo_alert) -> None:
        """Re-instantiate a component after Neo patched and hot-reloaded its module."""
        if not neo_alert.patch or not neo_alert.patch.reloaded:
            return

        module_name = neo_alert.patch.file_path.replace("/", ".").replace("\\", ".").removesuffix(".py")

        try:
            if module_name == "esther.signals.bias_engine":
                self._bias_engine = sys.modules[module_name].BiasEngine()
            elif module_name == "esther.signals.inversion_engine":
                self._inversion = sys.modules[module_name].InversionEngine()
            elif module_name == "esther.signals.quality_filter":
                self._quality_filter = sys.modules[module_name].QualityFilter()
            elif module_name == "esther.signals.black_swan":
                self._black_swan = sys.modules[module_name].BlackSwanDetector(self._client)
            elif module_name == "esther.signals.reentry":
                self._reentry = sys.modules[module_name].ReentryGuard(required_candles=2)
            elif module_name == "esther.signals.sage":
                self._sage = sys.modules[module_name].Sage()
            elif module_name == "esther.ai.debate":
                self._debate = sys.modules[module_name].AIDebate()
            elif module_name == "esther.ai.sizing":
                self._sizer = sys.modules[module_name].AISizer()
            elif module_name == "esther.ai.alpha":
                self._alpha = sys.modules[module_name].AlphaAgent()
            elif module_name == "esther.execution.pillars":
                self._executor = sys.modules[module_name].PillarExecutor(self._client)
            elif module_name == "esther.execution.position_manager":
                logger.info("neo_heal_position_mgr_skipped", reason="preserving open position state")
            elif module_name == "esther.risk.risk_manager":
                logger.info("neo_heal_risk_mgr_skipped", reason="preserving daily risk state")
            else:
                logger.info("neo_heal_no_reinit_needed", module=module_name)

            logger.info("neo_component_reinit", module=module_name)
        except Exception as e:
            logger.error("neo_reinit_failed", module=module_name, error=str(e))

    # ── Main Loop ────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        """Core loop: wait for market hours, scan tickers, manage positions."""
        while self._running:
            now_et = datetime.now(ET)
            current_time = now_et.time()

            market_open = time(9, 30)
            market_close = time(16, 0)
            pre_market = time(9, 15)
            # Read eod_cleanup from config (format "HH:MM"), fallback to 15:45
            try:
                _eod_parts = self._cfg.engine.eod_cleanup.split(":")
                eod_time = time(int(_eod_parts[0]), int(_eod_parts[1]))
            except Exception:
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
        self._errors_today = 0
        self._rejections_today = 0

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

        # Sage pre-market intelligence scan + dynamic ticker injection
        try:
            sage_intel = await self._sage.premarket_scan()
            logger.info("sage_premarket_complete", brief_len=len(sage_intel.intel_brief))

            # Inject top 20 flow tickers from UW into tier3 for today's session
            if sage_intel.dynamic_tickers:
                existing = set()
                for tc in self._cfg.tickers.values():
                    existing.update(tc.symbols)

                # Add new tickers not already in any tier
                new_tickers = [t for t in sage_intel.dynamic_tickers if t not in existing]
                if new_tickers:
                    self._cfg.tickers["tier3"].symbols = list(
                        set(self._cfg.tickers["tier3"].symbols) | set(new_tickers)
                    )
                    logger.info(
                        "dynamic_tickers_injected",
                        new=new_tickers,
                        total_tier3=len(self._cfg.tickers["tier3"].symbols),
                    )
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
                if self._inversion and pos.direction in ("BULL", "BEAR"):
                    orig_dir = getattr(pos, "original_direction", pos.direction).lower()
                    self._inversion.record_result(TradeResult(
                        symbol=pos.symbol,
                        direction=orig_dir if orig_dir else pos.direction.lower(),
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

        # Place GTC overnight orders for tomorrow (Tradier only — Alpaca paper doesn't support multi-leg GTC)
        if not isinstance(self._client, AlpacaClient):
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

        # Step 0.5: Self-Healing Loop (Hot-Reload signals)
        try:
            import sys
            import importlib
            from pathlib import Path
            signals_dir = Path("esther-trading/esther/signals") if Path("esther-trading/esther/signals").exists() else Path("esther/signals")
            if signals_dir.exists():
                for py_file in signals_dir.glob("*.py"):
                    if py_file.name == "__init__.py": continue
                    mod_name = f"esther.signals.{py_file.stem}"
                    if mod_name in sys.modules:
                        mtime = py_file.stat().st_mtime
                        if mod_name not in self._module_mtimes:
                            self._module_mtimes[mod_name] = mtime
                        elif self._module_mtimes[mod_name] < mtime:
                            logger.info("hot_reloading_module", module=mod_name)
                            importlib.reload(sys.modules[mod_name])
                            self._module_mtimes[mod_name] = mtime
                            
                            # Re-instantiate if it's a direct engine component
                            if mod_name == "esther.signals.bias_engine":
                                self._bias_engine = sys.modules[mod_name].BiasEngine()
                            elif mod_name == "esther.signals.inversion_engine":
                                self._inversion = sys.modules[mod_name].InversionEngine()
                            elif mod_name == "esther.signals.quality_filter":
                                self._quality_filter = sys.modules[mod_name].QualityFilter()
                            elif mod_name == "esther.signals.black_swan":
                                self._black_swan = sys.modules[mod_name].BlackSwanDetector(self._client)
                            elif mod_name == "esther.signals.reentry":
                                self._reentry = sys.modules[mod_name].ReentryGuard(required_candles=2)
                            elif mod_name == "esther.signals.sage":
                                self._sage = sys.modules[mod_name].Sage()
        except Exception as e:
            logger.warning("hot_reload_failed", error=str(e))

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
                        orig_dir = getattr(pos, "original_direction", pos.direction).lower()
                        orig_dir = orig_dir if orig_dir else ("bull" if pos.direction == "BULL" else "bear")
                        self._inversion.record_result(TradeResult(
                            symbol=pos.symbol,
                            direction=orig_dir,
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
                        orig_dir = getattr(pos, "original_direction", pos.direction).lower()
                        orig_dir = orig_dir if orig_dir else ("bull" if pos.direction == "BULL" else "bear")
                        self._inversion.record_result(TradeResult(
                            symbol=pos.symbol,
                            direction=orig_dir,
                            pnl=pos.unrealized_pnl,
                            won=pos.unrealized_pnl > 0,
                        ))
            except Exception as e:
                logger.error("position_update_failed", error=str(e))

        # Step 3: If risk manager shut us down, skip new entries
        if self._risk_mgr and self._risk_mgr.is_shutdown:
            logger.warning("risk_shutdown_active", reason="daily limit hit")
            return

        # Step 3b: FOMO Zone guard — No new trades before 10:00 AM ET (does not suppress 3 PM Power Hour)
        now_et = datetime.now(ET)
        if now_et.time() < time(10, 0):
            return

        # Step 3c: Alpha market condition analysis (once per cycle)
        try:
            spy_price = 0.0
            spy_change = 0.0
            qqq_price = 0.0
            qqq_change = 0.0
            try:
                spy_quotes = await self._client.get_quotes(["SPY"])
                if spy_quotes:
                    spy_price = spy_quotes[0].last
                    spy_change = spy_quotes[0].change_pct
                qqq_quotes = await self._client.get_quotes(["QQQ"])
                if qqq_quotes:
                    qqq_price = qqq_quotes[0].last
                    qqq_change = qqq_quotes[0].change_pct
            except Exception:
                pass

            sage_intel = self._sage.get_intel_for_debate() or None
            daily_pnl = self._position_mgr.get_daily_pnl() if self._position_mgr else 0.0

            alpha_report = await self._alpha.analyze(
                vix_level=self._current_swan_status.vix if self._current_swan_status else 20.0,
                spy_price=spy_price,
                spy_change_pct=spy_change,
                qqq_price=qqq_price,
                qqq_change_pct=qqq_change,
                sage_intel=sage_intel,
                account_balance=self._account_balance,
                daily_pnl=daily_pnl,
            )

            if alpha_report.posture == "CASH":
                logger.warning("alpha_says_cash", summary=alpha_report.summary)
                return

        except Exception as e:
            logger.warning("alpha_analysis_failed", error=str(e))

        # Step 3d: Neo health check (periodic)
        try:
            daily_pnl = self._position_mgr.get_daily_pnl() if self._position_mgr else 0.0
            open_pos = self._position_mgr.get_position_count() if self._position_mgr else 0
            total_trades = self._risk_mgr._daily_stats.total_trades if self._risk_mgr else 0

            neo_check = await self._neo.health_check(
                account_balance=self._account_balance,
                daily_pnl=daily_pnl,
                open_positions=open_pos,
                total_trades_today=total_trades,
                scan_count=self._scan_count,
                errors_today=self._errors_today,
                rejection_count=self._rejections_today,
            )

            if neo_check and neo_check.health == "CRITICAL":
                logger.error("neo_critical_health", issues=neo_check.issues)
        except Exception as e:
            logger.warning("neo_health_check_failed", error=str(e))

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
                    self._errors_today += 1
                    logger.error(
                        "ticker_processing_failed",
                        symbol=symbol,
                        tier=tier_name,
                        error=str(e),
                        exc_info=True,
                    )
                    # Neo diagnoses and auto-fixes the error
                    try:
                        neo_alert = await self._neo.on_error(
                            error=e,
                            context=f"Processing ticker {symbol} in {tier_name}",
                            symbol=symbol,
                            component="engine._process_ticker",
                        )
                        if neo_alert.healed:
                            self._reinit_healed_component(neo_alert)
                        if neo_alert.should_stop:
                            logger.error("neo_says_stop", alert=neo_alert.root_cause)
                    except Exception:
                        pass  # Neo failure is non-fatal
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
                    original_direction="BULL" if raw_bias.score > 0 else "BEAR",
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
        original_direction: str,
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

        # Inject Alpha's market condition report into debate context
        alpha_context = self._alpha.get_debate_context() if self._alpha else ""

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
            journal_lessons=journal_lessons + alpha_context,
        )

        try:
            verdict = await self._debate.debate(debate_input)  # Kage-only: Riki→Abi→Kage (no Kimi veto)
        except Exception as e:
            self._errors_today += 1
            log.error("debate_failed", error=str(e))
            try:
                neo_alert = await self._neo.on_error(e, context=f"AI debate for {symbol}", symbol=symbol, component="ai.debate")
                if neo_alert.healed:
                    self._reinit_healed_component(neo_alert)
            except Exception:
                pass
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
        if verdict.confidence < 50:
            log.info("debate_low_confidence", confidence=verdict.confidence, min_required=50)
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
            self._rejections_today += 1
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
            if self._neo:
                self._neo.on_trade_success()
        except Exception as e:
            self._errors_today += 1
            log.error("order_submit_failed", error=str(e))
            try:
                neo_alert = await self._neo.on_error(e, context=f"Order submit for {symbol} P{pillar}", symbol=symbol, component="execution.pillars")
                if neo_alert.healed:
                    self._reinit_healed_component(neo_alert)
            except Exception:
                pass
            return

        # Register position with position manager
        position = self._position_mgr.open_position(
            order=final_order,
            order_id=order_id,
            direction=direction,
            original_direction=original_direction,
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
            # self._journal.record(entry)  # Moved to PositionManager._close_position for synchronous Verified Ledger Entry
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
