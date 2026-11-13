"""Explicit, idempotent builtin import; never enables traffic automatically."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent.mysql_storage import mysql_storage
from agent.prompt_releases.repository import PromptRepository


async def initialize(actor):
    await mysql_storage.initialize()
    try:
        state = await PromptRepository(mysql_storage).initialize(actor)
        print(f"stable={state['stable']} revision={state['revision']}; runtime flag is unchanged")
    finally:
        await mysql_storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor", required=True, help="operator account for audit")
    args = parser.parse_args()
    asyncio.run(initialize(args.actor))
