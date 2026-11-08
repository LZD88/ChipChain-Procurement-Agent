from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

import pytest

from agent.decision_repository import DecisionRepository, DecisionStorageError
from agent.policies.procurement import compare_candidates
from mcp_server.tools.procurement_tools import build_durable_evidence
from test_procurement_compare import INVENTORIES, PARTS, SUPPLIERS
from test_decision_repository import FakeStorage, make_evidence


def result(budget=Decimal("9000")):
    return compare_candidates(
        part_name="六轴惯性传感器", quantity=500, parts=PARTS,
        suppliers=SUPPLIERS, inventories=INVENTORIES, budget=budget,
        max_lead_time_days=14,
    )


def source(parts=PARTS):
    return {"parts": parts, "suppliers": SUPPLIERS, "inventories": INVENTORIES}


def identity():
    return {"sub": "alice", "thread": "thread-1", "trace": "TRC-1"}


def test_durable_id_is_deterministic_across_source_order_and_covers_no_solution():
    first, first_result = build_durable_evidence(
        identity=identity(), request=result()["request"], source=source(), result=result(),
        now=datetime(2026, 1, 1),
    )
    reversed_result = result()
    second, second_result = build_durable_evidence(
        identity=identity(), request=reversed_result["request"],
        source=source(list(reversed(PARTS))), result=reversed_result,
        now=datetime(2026, 1, 1),
    )
    assert first.decision_id == second.decision_id
    assert first.evidence_fingerprint == second.evidence_fingerprint
    assert first_result["decisionId"] == second_result["decisionId"]

    no_solution_result = result(Decimal("4000"))
    no_solution, returned = build_durable_evidence(
        identity=identity(), request=no_solution_result["request"], source=source(),
        result=no_solution_result, now=datetime(2026, 1, 1),
    )
    assert no_solution.result["recommendedPartId"] is None
    assert returned["decisionId"].startswith("DEC-")


class UnavailableStorage:
    @asynccontextmanager
    async def cursor(self, transaction=False):
        raise RuntimeError("offline")
        yield


@pytest.mark.asyncio
async def test_persistence_failure_is_fail_closed():
    comparison = result()
    evidence, _returned = build_durable_evidence(
        identity=identity(), request=comparison["request"], source=source(), result=comparison,
    )
    with pytest.raises(DecisionStorageError):
        await DecisionRepository(UnavailableStorage()).insert(evidence)


@pytest.mark.asyncio
async def test_evidence_survives_repository_recreation():
    storage = FakeStorage()
    await DecisionRepository(storage).insert(make_evidence())
    restarted_repository = DecisionRepository(storage)
    restored = await restarted_repository.get_for_owner("DEC-1", "alice")
    assert restored["decision_id"] == "DEC-1"
    assert restored["result"]["recommendedPartId"] == 1
