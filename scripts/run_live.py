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
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also set up file logging if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            handlers=[logging.FileHandler(log_path)],
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(message)s", # structlog handles formatting
        )



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
