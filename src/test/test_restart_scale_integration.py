"""Restart-and-scale integration coverage for durable procurement evidence."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import aiomysql
import pytest

from agent.decision_repository import DecisionClaimedError, DecisionRepository
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from agent.policies.procurement import compare_candidates
from mcp_server.tools.order_tools import (
    _deterministic_order_number,
    authorize_order_from_evidence,
    dispatch_claimed_order,
)
from mcp_server.tools.procurement_tools import build_durable_evidence
from test_procurement_compare import INVENTORIES, PARTS, SUPPLIERS


@dataclass
class FakeResponse:
    status_code: int
    body: dict
    headers: dict

    def json(self):
        return self.body


class IdempotentFakeErp:
    """Small ERP boundary fake that deduplicates by the supplied idempotency key."""

    def __init__(self):
        self.post_calls = []
        self.orders = {}

    async def post(self, path, *, json, headers):
        self.post_calls.append((path, json, headers))
        idempotency_key = headers["Idempotency-Key"]
        replayed = idempotency_key in self.orders
        if not replayed:
            self.orders[idempotency_key] = {
                "id": 91,
                "orderNumber": json["orderNumber"],
                "request": json,
                "requestTraceId": headers["X-Trace-Id"],
                "traceId": "ERP-TRACE-SCALE",
                "erpAuditRef": "AUD-SCALE-91",
            }
        order = self.orders[idempotency_key]
        return FakeResponse(
            status_code=200,
            body={
                "code": 200,
                "data": {"id": order["id"], "orderNumber": order["orderNumber"]},
            },
            headers={
                "X-Trace-Id": order["traceId"],
                "Idempotency-Replayed": "true" if replayed else "false",
                "X-ERP-Audit-Ref": order["erpAuditRef"],
            },
        )


async def _new_pool(settings: MySQLSettings, database: str):
    return await aiomysql.create_pool(
        **settings.connection_options(),
        db=database,
        minsize=1,
        maxsize=2,
    )


@pytest.mark.asyncio
async def test_restart_then_two_replicas_create_one_linked_idempotent_order():
    base = MySQLSettings.from_env()
    database = "agent_restart_scale_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_restart_scale_[0-9a-f]{12}", database)

    admin = await aiomysql.connect(**base.connection_options())
    storage_before_restart = None
    replica_a = None
    replica_b = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )

        storage_before_restart = MySQLStorage(base)
        storage_before_restart.pool = await _new_pool(base, database)
        async with storage_before_restart.pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute(
                    "INSERT INTO app_users(username,password_hash,created_at) "
                    "VALUES ('alice','test',UTC_TIMESTAMP(6))"
                )
                await cur.execute(
                    "INSERT INTO chat_threads(thread_id,user_id,created_at) "
                    "VALUES ('thread-scale','alice',UTC_TIMESTAMP(6))"
                )

        comparison = compare_candidates(
            part_name="六轴惯性传感器",
            quantity=500,
            parts=PARTS,
            suppliers=SUPPLIERS,
            inventories=INVENTORIES,
            budget=Decimal("9000"),
            max_lead_time_days=14,
        )
        evidence, returned = build_durable_evidence(
            identity={
                "sub": "alice",
                "thread": "thread-scale",
                "trace": "AGT-TRACE-SCALE",
            },
            request=comparison["request"],
            source={
                "parts": PARTS,
                "suppliers": SUPPLIERS,
                "inventories": INVENTORIES,
            },
            result=comparison,
            now=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        await DecisionRepository(storage_before_restart).insert(evidence)

        # Closing the original pool and discarding its repository simulates the
        # MCP process restart; the next reads must come from durable MySQL state.
        await storage_before_restart.close()
        storage_before_restart = None

        replica_a = MySQLStorage(base)
        replica_a.pool = await _new_pool(base, database)
        replica_b = MySQLStorage(base)
        replica_b.pool = await _new_pool(base, database)
        repository_a = DecisionRepository(replica_a)
        repository_b = DecisionRepository(replica_b)

        restored = await repository_a.get_for_owner(evidence.decision_id, "alice")
        assert restored is not None
        assert restored["evidence_fingerprint"] == returned["evidenceFingerprint"]
        assert restored["result"] == evidence.result

        recommended = next(
            candidate
            for candidate in restored["result"]["candidates"]
            if candidate["partId"] == restored["result"]["recommendedPartId"]
        )
        order_detail = [{
            "partId": recommended["partId"],
            "quantity": recommended["quantity"],
            "unitPrice": recommended["unitPrice"],
        }]

        async def attempt(repository, token):
            try:
                authorization = await authorize_order_from_evidence(
                    repository,
                    owner_user_id="alice",
                    decision_id=evidence.decision_id,
                    policy_version="supplier-score-v1",
                    order_detail=order_detail,
                    claim_token=token,
                )
                return repository, authorization
            except DecisionClaimedError:
                return None

        attempts = await asyncio.gather(
            attempt(repository_a, "1" * 64),
            attempt(repository_b, "2" * 64),
        )
        winners = [attempt_result for attempt_result in attempts if attempt_result is not None]
        assert len(winners) == 1
        winning_repository, authorization = winners[0]

        fake_erp = IdempotentFakeErp()
        order_number = _deterministic_order_number(evidence.decision_id)
        created = await dispatch_claimed_order(
            winning_repository,
            fake_erp,
            owner_user_id="alice",
            decision_id=evidence.decision_id,
            authorization=authorization,
            request_data={"orderNumber": order_number, "orderDetail": order_detail},
            trace_id="AGT-TRACE-SCALE",
        )
        assert created == {"id": 91, "orderNumber": order_number}

        linked_evidence = await repository_b.get_for_owner(evidence.decision_id, "alice")
        claim = await repository_b.get_claim(evidence.decision_id, "alice")
        lifecycle = await repository_b.events_for_owner(evidence.decision_id, "alice")
        async with replica_b.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) AS count FROM procurement_decision_claims "
                "WHERE decision_id=%s AND owner_user_id='alice'",
                (evidence.decision_id,),
            )
            claim_count = (await cur.fetchone())["count"]

        assert linked_evidence["evidence_fingerprint"] == evidence.evidence_fingerprint
        assert linked_evidence["result"] == evidence.result
        assert claim_count == 1
        assert claim["claim_state"] == "SUCCEEDED"
        assert claim["resolved_at"] is not None
        assert claim["order_id"] == 91
        assert claim["order_number"] == order_number
        assert claim["trace_id"] == "ERP-TRACE-SCALE"
        assert claim["erp_audit_ref"] == "AUD-SCALE-91"

        assert len(fake_erp.post_calls) == 1
        assert len(fake_erp.orders) == 1
        fake_order = next(iter(fake_erp.orders.values()))
        assert fake_order["requestTraceId"] == "AGT-TRACE-SCALE"
        assert fake_order["traceId"] == claim["trace_id"]
        assert fake_order["erpAuditRef"] == claim["erp_audit_ref"]

        event_types = [event["event_type"] for event in lifecycle]
        assert event_types.count("CLAIMED") == 1
        assert event_types.count("ERP_DISPATCHED") == 1
        assert event_types.count("SUCCEEDED") == 1
        resolution = next(event for event in lifecycle if event["event_type"] == "SUCCEEDED")
        assert resolution["payload"]["orderId"] == claim["order_id"]
        assert resolution["payload"]["orderNumber"] == claim["order_number"]
        assert resolution["payload"]["traceId"] == claim["trace_id"]
        assert resolution["payload"]["erpAuditRef"] == claim["erp_audit_ref"]
    finally:
        if storage_before_restart is not None:
            await storage_before_restart.close()
        if replica_a is not None:
            await replica_a.close()
        if replica_b is not None:
            await replica_b.close()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        await admin.ensure_closed()
