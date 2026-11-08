"""MySQL integration tests for cross-connection decision claiming."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import aiomysql
import pytest

from agent.decision_repository import DecisionClaimedError, DecisionRepository
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage


@pytest.mark.asyncio
async def test_two_connections_claim_once_and_ambiguous_claim_is_not_released():
    base = MySQLSettings.from_env()
    database = "agent_claim_test_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_claim_test_[0-9a-f]{12}", database)
    admin = await aiomysql.connect(**base.connection_options())
    pool = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        pool = await aiomysql.create_pool(
            **base.connection_options(), db=database, minsize=2, maxsize=4,
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute(
                    "INSERT INTO app_users(username,password_hash,created_at) "
                    "VALUES ('alice','test',UTC_TIMESTAMP(6))"
                )
                await cur.execute(
                    "INSERT INTO chat_threads(thread_id,user_id,created_at) "
                    "VALUES ('thread-race','alice',UTC_TIMESTAMP(6))"
                )
                await cur.execute(
                    "INSERT INTO procurement_decisions "
                    "(decision_id,owner_user_id,thread_id,policy_version,canonicalization_version,"
                    "evidence_fingerprint,request_json,source_json,result_json,created_at,expires_at) "
                    "VALUES ('DEC-RACE','alice','thread-race','supplier-score-v1','procurement-json-v1',"
                    "%s,'{}','{}','{}',UTC_TIMESTAMP(6),DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 15 MINUTE))",
                    ("a" * 64,),
                )

        storage = MySQLStorage(base)
        storage.pool = pool
        first = DecisionRepository(storage)
        second = DecisionRepository(storage)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        assert await first.append_event(
            "DEC-RACE", "alice", "APPROVAL_REQUESTED", actor_user_id="alice",
            event_key="approval-request:req-1",
            payload={"approvalRequestId": "req-1", "threadId": "thread-race"},
            created_at=now,
        )
        pending = await second.pending_approvals_for_thread("thread-race", "alice")
        assert [item["decision_id"] for item in pending] == ["DEC-RACE"]
        assert await second.append_event(
            "DEC-RACE", "alice", "APPROVED", actor_user_id="alice",
            event_key="approval-resolution:req-1",
            payload={"approvalRequestId": "req-1", "decision": "approve"},
            created_at=now,
        )
        assert await first.pending_approvals_for_thread("thread-race", "alice") == []

        async def attempt(repository, token):
            try:
                await repository.claim(
                    "DEC-RACE", "alice", claim_token=token,
                    idempotency_key="idempotency:" + token,
                    lease_expires_at=now + timedelta(seconds=30), now=now,
                )
                return "won"
            except DecisionClaimedError:
                return "claimed"

        outcomes = await asyncio.gather(attempt(first, "1" * 64), attempt(second, "2" * 64))
        assert sorted(outcomes) == ["claimed", "won"]
        claim = await first.get_claim("DEC-RACE", "alice")
        winning_token = claim["claim_token"]
        await first.mark_reconciliation_required(
            "DEC-RACE", "alice", claim_token=winning_token, reason="ERP timeout", now=now,
        )
        claim = await second.get_claim("DEC-RACE", "alice")
        assert claim["claim_state"] == "RECONCILIATION_REQUIRED"

        with pytest.raises(DecisionClaimedError):
            await second.claim(
                "DEC-RACE", "alice", claim_token="3" * 64,
                idempotency_key="idempotency:" + ("3" * 64), now=now,
            )
    finally:
        if pool is not None:
            pool.close()
            await pool.wait_closed()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        await admin.ensure_closed()
