"""Additive deployment and rollback rehearsal against a current-release copy."""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import aiomysql
import pytest

from agent.decision_repository import DecisionRepository, DecisionStorageError
from agent.mysql_storage import (
    APPLICATION_DDL,
    MySQLSettings,
    MySQLStorage,
    _ensure_application_tables,
)
from agent.policies.procurement import compare_candidates
from mcp_server.tools import order_tools, procurement_tools
from mcp_server.tools.order_tools import authorize_order_from_evidence
from mcp_server.tools.procurement_tools import build_durable_evidence
from test_procurement_compare import INVENTORIES, PARTS, SUPPLIERS


CURRENT_RELEASE_TABLES = (
    "app_users",
    "auth_sessions",
    "chat_projects",
    "chat_threads",
    "session_display_messages",
    "sandbox_registry",
)
ADDITIVE_TABLES = tuple(name for name in APPLICATION_DDL if name not in CURRENT_RELEASE_TABLES)


async def _create_current_release_copy(admin, source: str, rehearsal: str):
    async with admin.cursor() as cur:
        for database in (source, rehearsal):
            await cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
            await cur.execute(f"USE `{database}`")
            for table in CURRENT_RELEASE_TABLES:
                await cur.execute(APPLICATION_DDL[table])

        await cur.execute(f"USE `{source}`")
        await cur.execute(
            "INSERT INTO app_users(username,password_hash,created_at) "
            "VALUES ('legacy-user','legacy-hash','2026-01-01 00:00:00')"
        )
        await cur.execute(
            "INSERT INTO auth_sessions(token_hash,username,expires_at,created_at) "
            "VALUES (%s,'legacy-user','2030-01-01 00:00:00','2026-01-01 00:00:00')",
            ("a" * 64,),
        )
        await cur.execute(
            "INSERT INTO chat_projects(project_id,user_id,name,created_at,updated_at) "
            "VALUES ('legacy-project','legacy-user','旧项目','2026-01-01','2026-01-01')"
        )
        await cur.execute(
            "INSERT INTO chat_threads(thread_id,user_id,project_id,custom_title,pinned,created_at) "
            "VALUES ('legacy-thread','legacy-user','legacy-project','旧会话',1,'2026-01-01')"
        )
        await cur.execute(
            "INSERT INTO session_display_messages(thread_id,message_index,message,updated_at) "
            "VALUES ('legacy-thread',0,%s,'2026-01-01')",
            (json.dumps({"role": "user", "content": "保留的旧消息"}, ensure_ascii=False),),
        )
        await cur.execute(
            "INSERT INTO sandbox_registry(user_id,sandbox_id,created_at,updated_at) "
            "VALUES ('legacy-user','legacy-sandbox','2026-01-01','2026-01-01')"
        )

        # This is the disposable copy used by the rehearsal; the source remains untouched.
        for table in CURRENT_RELEASE_TABLES:
            await cur.execute(
                f"INSERT INTO `{rehearsal}`.`{table}` SELECT * FROM `{source}`.`{table}`"
            )


async def _assert_legacy_reads(storage):
    assert await storage.get_user("legacy-user") == {
        "username": "legacy-user", "password_hash": "legacy-hash",
    }
    assert await storage.thread_belongs_to("legacy-thread", "legacy-user") is True
    assert await storage.get_display_messages("legacy-thread") == [
        {"role": "user", "content": "保留的旧消息"}
    ]
    projects = await storage.list_projects("legacy-user")
    assert [(row["project_id"], row["name"]) for row in projects] == [
        ("legacy-project", "旧项目")
    ]


@pytest.mark.asyncio
async def test_additive_deploy_mixed_version_fail_closed_and_rollback_preserves_tables():
    base = MySQLSettings.from_env()
    suffix = uuid4().hex[:10]
    source = "agent_release_source_" + suffix
    rehearsal = "agent_release_copy_" + suffix
    assert re.fullmatch(r"agent_release_(source|copy)_[0-9a-f]{10}", source)
    assert re.fullmatch(r"agent_release_(source|copy)_[0-9a-f]{10}", rehearsal)
    admin = await aiomysql.connect(**base.connection_options())
    storage = None
    rolled_back_storage = None
    try:
        await _create_current_release_copy(admin, source, rehearsal)
        pool = await aiomysql.create_pool(
            **base.connection_options(), db=rehearsal, minsize=1, maxsize=3,
        )
        storage = MySQLStorage(base)
        storage.pool = pool

        # Old chat, session, project, and read flows work on the copied release database.
        await _assert_legacy_reads(storage)

        comparison = compare_candidates(
            part_name="六轴惯性传感器", quantity=500, parts=PARTS,
            suppliers=SUPPLIERS, inventories=INVENTORIES,
            budget=Decimal("9000"), max_lead_time_days=14,
        )
        evidence, _returned = build_durable_evidence(
            identity={"sub": "legacy-user", "thread": "legacy-thread", "trace": "DEPLOY-TRACE"},
            request=comparison["request"],
            source={"parts": PARTS, "suppliers": SUPPLIERS, "inventories": INVENTORIES},
            result=comparison,
            now=datetime.now(timezone.utc).replace(tzinfo=None),
        )

        # A new MCP replica reaching the pre-migration copy cannot mint an actionable ID.
        # It raises instead of placing evidence in memory or proceeding to ERP.
        with pytest.raises(DecisionStorageError, match="persistence failed"):
            await DecisionRepository(storage).insert(evidence)
        recommended = next(
            item for item in evidence.result["candidates"]
            if item["partId"] == evidence.result["recommendedPartId"]
        )
        order_detail = [{
            "partId": recommended["partId"],
            "quantity": recommended["quantity"],
            "unitPrice": recommended["unitPrice"],
        }]
        with pytest.raises(aiomysql.MySQLError):
            await authorize_order_from_evidence(
                DecisionRepository(storage), owner_user_id="legacy-user",
                decision_id="DEC-LEGACY-EPHEMERAL", policy_version="supplier-score-v1",
                order_detail=order_detail,
            )

        # Additive deployment creates only missing tables and preserves legacy rows.
        async with storage.cursor() as cur:
            await _ensure_application_tables(cur)
        await _assert_legacy_reads(storage)
        repository = DecisionRepository(storage)
        await repository.insert(evidence)
        assert (await repository.get_for_owner(evidence.decision_id, "legacy-user"))[
            "evidence_fingerprint"
        ] == evidence.evidence_fingerprint

        async with storage.cursor() as cur:
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                (rehearsal,),
            )
            deployed_tables = {row["TABLE_NAME"] for row in await cur.fetchall()}
        assert set(CURRENT_RELEASE_TABLES) | set(ADDITIVE_TABLES) <= deployed_tables

        # Application rollback is represented by closing the new runtime and reopening
        # only the old read surface. Database rollback deliberately performs no DROP.
        await storage.close()
        storage = None
        rollback_pool = await aiomysql.create_pool(
            **base.connection_options(), db=rehearsal, minsize=1, maxsize=2,
        )
        rolled_back_storage = MySQLStorage(base)
        rolled_back_storage.pool = rollback_pool
        await _assert_legacy_reads(rolled_back_storage)
        async with rolled_back_storage.cursor() as cur:
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                (rehearsal,),
            )
            rollback_tables = {row["TABLE_NAME"] for row in await cur.fetchall()}
            await cur.execute(
                "SELECT COUNT(*) AS count FROM procurement_decisions WHERE decision_id=%s",
                (evidence.decision_id,),
            )
            retained_decisions = (await cur.fetchone())["count"]
        assert set(ADDITIVE_TABLES) <= rollback_tables
        assert retained_decisions == 1

        # Static guard: both actionable modules depend on the durable repository and
        # expose no module-level mutable decision cache to fall back to.
        procurement_source = inspect.getsource(procurement_tools)
        order_source = inspect.getsource(order_tools)
        assert "await repository.insert(evidence)" in procurement_source
        assert "authorize_order_from_evidence" in order_source
        assert "DecisionRepository" in procurement_source and "DecisionRepository" in order_source
        mutable_snapshots = {
            name for module in (procurement_tools, order_tools)
            for name, value in vars(module).items()
            if isinstance(value, dict) and not name.startswith("__") and not name.isupper()
        }
        assert mutable_snapshots == set()
    finally:
        if storage is not None:
            await storage.close()
        if rolled_back_storage is not None:
            await rolled_back_storage.close()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{rehearsal}`")
            await cur.execute(f"DROP DATABASE IF EXISTS `{source}`")
        admin.close()
        await admin.ensure_closed()
