"""Initialize the six Agent persistence tables without starting models or sandboxes."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.mysql_storage import MySQLStorage


async def main():
    storage = MySQLStorage()
    try:
        await storage.initialize()
        settings = storage.settings
        print(f"MySQL ready: {settings.host}:{settings.port}/{settings.database}")
        async with storage.cursor() as cur:
            await cur.execute("SELECT VERSION() AS version")
            print("Server version:", (await cur.fetchone())["version"])
            await cur.execute("SHOW TABLES")
            for row in await cur.fetchall():
                print(" -", next(iter(row.values())))
    finally:
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
