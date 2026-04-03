
import asyncio
import json
from esther.signals.sage import Sage
from esther.core.config import load_config, set_config, get_env
from pathlib import Path

async def get_market_intel():
    config_path = Path("config-tradier.yaml")
    cfg = load_config(config_path)
    set_config(cfg)
    sage = Sage()
    intel = await sage.intraday_scan()
    print(json.dumps(intel.model_dump(mode="json"), indent=2))

if __name__ == "__main__":
    asyncio.run(get_market_intel())
