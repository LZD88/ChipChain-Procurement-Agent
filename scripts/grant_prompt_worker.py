"""Add table-specific permissions to the configured evaluation worker account."""
import asyncio
import os
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent.mysql_storage import mysql_storage


async def main():
    await mysql_storage.initialize()
    try:
        user = os.getenv("EVALUATION_DB_USER", "chipchain_evaluation")
        host = os.getenv("EVALUATION_DB_ACCOUNT_HOST", "localhost")
        password = os.getenv("EVALUATION_DB_PASSWORD")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", user) or host not in {"localhost", "127.0.0.1"} or not password:
            raise ValueError("configure a local EVALUATION_DB_USER and EVALUATION_DB_PASSWORD first")
        database = mysql_storage.settings.database
        async with mysql_storage.cursor() as cur:
            await cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, host, password))
            permissions = {"prompt_bundle_versions": "SELECT", "evaluation_suite_versions": "SELECT",
                "evaluation_runs": "SELECT,UPDATE", "evaluation_cases": "SELECT,INSERT,UPDATE",
                "evaluation_assertion_results": "SELECT,INSERT,UPDATE,DELETE"}
            for table, privileges in permissions.items():
                await cur.execute(f"GRANT {privileges} ON `{database}`.`{table}` TO %s@%s", (user, host))
        print("Worker table permissions are ready; no business-write or release-state permissions were added.")
    finally:
        await mysql_storage.close()


if __name__ == "__main__":
    asyncio.run(main())
