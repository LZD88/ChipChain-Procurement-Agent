import asyncio
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

import aiomysql
import pytest

from agent.evaluation.repository import EvaluationRepository
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from test_evaluation_manifest import manifest


@pytest.mark.asyncio
async def test_two_workers_claim_distinct_runs_and_expired_lease_recovers_incomplete():
    base = MySQLSettings.from_env()
    database = "agent_queue_test_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_queue_test_[0-9a-f]{12}", database)
    admin = await aiomysql.connect(**base.connection_options())
    pool = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        pool = await aiomysql.create_pool(**base.connection_options(), db=database, minsize=2, maxsize=4)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute("INSERT INTO app_users VALUES ('alice','test',UTC_TIMESTAMP(6))")
                await cur.execute(
                    "INSERT INTO evaluation_suite_versions VALUES "
                    "('suite',1,'alice','suite',%s,'{}',UTC_TIMESTAMP(6))", ("f" * 64,),
                )
                for index in (1, 2):
                    await cur.execute(
                        "INSERT INTO evaluation_runs "
                        "(run_id,suite_id,suite_version,requested_by,execution_mode,status,manifest_json,created_at) "
                        "VALUES (%s,'suite',1,'alice','rule-contract','QUEUED',%s,UTC_TIMESTAMP(6))",
                        (f"00000000-0000-0000-0000-00000000000{index}",
                         json.dumps(manifest(), default=str)),
                    )
        storage = MySQLStorage(base); storage.pool = pool
        first = EvaluationRepository(storage)
        second = EvaluationRepository(storage)
        claimed = await asyncio.gather(
            first.claim_next_run(worker_id="worker-a"),
            second.claim_next_run(worker_id="worker-b"),
        )
        assert len(set(claimed)) == 2
        assert None not in claimed
        assert await first.claim_next_run(worker_id="worker-a") is None

        crashed = claimed[0]
        old = datetime(2026, 1, 1)
        async with storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE evaluation_runs SET lease_expires_at=%s WHERE run_id=%s",
                (old, crashed),
            )
        recovered = await second.recover_expired_leases(now=old + timedelta(seconds=1))
        assert recovered == [crashed]
        row = await second.get_run(crashed)
        assert row["status"] == "QUEUED"
        assert row["result"] == {"incomplete": True, "reason": "worker_lease_expired"}
        assert await second.claim_next_run(worker_id="worker-c", now=old + timedelta(seconds=2)) == crashed
        assert await second.request_cancel(crashed, now=old + timedelta(seconds=3))
        assert await second.cancellation_requested(crashed, worker_id="worker-c")
        assert not await second.renew_lease(crashed, worker_id="worker-c", now=old + timedelta(seconds=4))
    finally:
        if pool is not None:
            pool.close(); await pool.wait_closed()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close(); await admin.ensure_closed()
