import asyncio
from esther.signals.sage import Sage

async def run():
    sage = Sage()
    intel = await sage.premarket_scan()
    print(sage.format_telegram(intel))

if __name__ == "__main__":
    asyncio.run(run())
