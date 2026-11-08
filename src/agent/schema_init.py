"""One-shot additive Agent schema initialization for deployment orchestration."""

from __future__ import annotations

import asyncio

from agent.mysql_storage import mysql_storage


async def initialize_and_close() -> None:
    try:
        await mysql_storage.initialize()
    finally:
        await mysql_storage.close()


def main() -> None:
    asyncio.run(initialize_and_close())


if __name__ == "__main__":
    main()
