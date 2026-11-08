"""MySQL security checks for owner isolation and retention holds."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import aiomysql
import pytest

from agent.decision_repository import DecisionRepository
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from agent.policies.procurement import compare_candidates
from mcp_server.tools.procurement_tools import build_durable_evidence
from test_procurement_compare import INVENTORIES, PARTS, SUPPLIERS


@pytest.mark.asyncio
async def test_cross_user_read_is_non_disclosing_and_hold_blocks_real_mysql_purge():
    base = MySQLSettings.from_env()
    database = "agent_security_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_security_[0-9a-f]{12}", database)
    admin = await aiomysql.connect(**base.connection_options())
    storage = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        pool = await aiomysql.create_pool(
            **base.connection_options(), db=database, minsize=1, maxsize=3,
        )
        storage = MySQLStorage(base)
        storage.pool = pool
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute(
                    "INSERT INTO app_users(username,password_hash,created_at) VALUES "
                    "('alice','must-not-leak',UTC_TIMESTAMP(6)),"
                    "('bob','must-not-leak',UTC_TIMESTAMP(6))"
                )
                await cur.execute(
                    "INSERT INTO chat_threads(thread_id,user_id,created_at) "
                    "VALUES ('thread-security','alice',UTC_TIMESTAMP(6))"
                )

        comparison = compare_candidates(
            part_name="六轴惯性传感器", quantity=500, parts=PARTS,
            suppliers=SUPPLIERS, inventories=INVENTORIES,
            budget=Decimal("9000"), max_lead_time_days=14,
        )
        old = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
        evidence, _returned = build_durable_evidence(
            identity={"sub": "alice", "thread": "thread-security", "trace": "SEC-TRACE"},
            request=comparison["request"],
            source={"parts": PARTS, "suppliers": SUPPLIERS, "inventories": INVENTORIES},
            result=comparison,
            now=old,
        )
        repository = DecisionRepository(storage)
        await repository.insert(evidence)

        assert await repository.get_for_owner(evidence.decision_id, "bob") is None
        assert await repository.events_for_owner(evidence.decision_id, "bob") == []
        assert await repository.list_for_owner("bob") == []

        hold_id = str(uuid4())
        await repository.place_retention_hold(
            evidence.decision_id, "alice", hold_id=hold_id,
            reason="security verification", created_by="alice", now=old,
        )
        purge_time = datetime(2026, 11, 8, tzinfo=timezone.utc).replace(tzinfo=None)
        assert await repository.purge_expired(now=purge_time, retention_days=90) == []
        held = await repository.get_for_owner(evidence.decision_id, "alice")
        assert held["purged_at"] is None
        assert held["result"]["recommendedPartId"] == evidence.result["recommendedPartId"]

        assert await repository.release_retention_hold(
            hold_id, released_by="alice", now=purge_time,
        ) is True
        assert await repository.purge_expired(now=purge_time, retention_days=90) == [
            evidence.decision_id
        ]
        purged = await repository.get_for_owner(evidence.decision_id, "alice")
        assert purged["purged_at"] == purge_time
        assert purged["request"] is None
        assert purged["source"] is None
        assert purged["result"] is None
        async with storage.cursor() as cur:
            await cur.execute(
                "SELECT action,outcome FROM decision_audit_actions "
                "WHERE resource_id=%s ORDER BY audit_id", (hold_id,),
            )
            assert await cur.fetchall() == [{
                "action": "RETENTION_HOLD_RELEASE", "outcome": "SUCCESS",
            }]
    finally:
        if storage is not None:
            await storage.close()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        await admin.ensure_closed()
