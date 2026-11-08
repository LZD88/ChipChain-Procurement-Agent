from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import aiomysql
import pytest

from agent.decision_repository import (
    DecisionConflictError,
    DecisionEvidence,
    DecisionIntegrityError,
    DecisionRepository,
    DecisionStorageError,
)
from agent.policies.canonical import CANONICALIZATION_VERSION, evidence_fingerprint
from agent.evidence_sanitizer import sanitize_request, sanitize_result, sanitize_source


class FakeCursor:
    def __init__(self, storage):
        self.storage = storage
        self.rowcount = 0
        self.rows = []
        self.row = None

    async def execute(self, sql, params=None):
        if self.storage.fail:
            raise RuntimeError("database unavailable")
        if sql.startswith("INSERT INTO procurement_decisions "):
            key = (params[0], params[1])
            if key in self.storage.decisions:
                raise aiomysql.IntegrityError(1062, "duplicate")
            self.storage.decisions[key] = {
                "decision_id": params[0], "owner_user_id": params[1], "thread_id": params[2],
                "policy_version": params[3], "canonicalization_version": params[4],
                "evidence_fingerprint": params[5], "request_json": params[6],
                "source_json": params[7], "result_json": params[8], "trace_id": params[9],
                "created_at": params[10], "expires_at": params[11], "purged_at": None,
                "purge_reason": None,
            }
            self.rowcount = 1
        elif sql.startswith("INSERT INTO procurement_decision_events "):
            if self.storage.fail_event:
                raise RuntimeError("event insert failed")
            if "SELECT decision_id" in sql:
                key = (params[-2], params[-1])
                if key in self.storage.decisions:
                    self.storage.events.append(params)
                    self.rowcount = 1
                else:
                    self.rowcount = 0
            else:
                self.storage.events.append(params)
                self.rowcount = 1
        elif sql.startswith("SELECT * FROM procurement_decisions"):
            self.row = self.storage.decisions.get((params[0], params[1]))
        elif sql.startswith("SELECT decision_id,policy_version"):
            owner, size, offset = params
            rows = [row for (_decision, row_owner), row in self.storage.decisions.items()
                    if row_owner == owner]
            rows.sort(key=lambda row: (row["created_at"], row["decision_id"]), reverse=True)
            self.rows = rows[offset:offset + size]

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class FakeStorage:
    def __init__(self):
        self.decisions = {}
        self.events = []
        self.fail = False
        self.fail_event = False

    @asynccontextmanager
    async def cursor(self, transaction=False):
        before = dict(self.decisions)
        events = list(self.events)
        try:
            yield FakeCursor(self)
        except Exception:
            if transaction:
                self.decisions = before
                self.events = events
            raise


def make_evidence(owner="alice", decision_id="DEC-1", price="1"):
    request = {"partName": "传感器", "quantity": 1}
    source = {"parts": [{"id": 1, "purchasePrice": price}], "suppliers": {}, "inventories": {}}
    result = {"recommendedPartId": 1, "candidates": [{"partId": 1}]}
    now = datetime(2026, 1, 1)
    return DecisionEvidence(
        decision_id=decision_id, owner_user_id=owner, thread_id="thread-1",
        policy_version="supplier-score-v1", canonicalization_version=CANONICALIZATION_VERSION,
        evidence_fingerprint=evidence_fingerprint(
            policy_version="supplier-score-v1", request=sanitize_request(request),
            source=sanitize_source(source), result=sanitize_result(result),
        ), request=request, source=source, result=result, trace_id="TRC-1",
        created_at=now, expires_at=now + timedelta(minutes=15),
    )


@pytest.mark.asyncio
async def test_insert_duplicate_and_transaction_rollback():
    storage = FakeStorage()
    repository = DecisionRepository(storage)
    evidence = make_evidence()
    await repository.insert(evidence)
    with pytest.raises(DecisionConflictError):
        await repository.insert(evidence)

    storage2 = FakeStorage()
    storage2.fail_event = True
    with pytest.raises(DecisionStorageError):
        await DecisionRepository(storage2).insert(evidence)
    assert storage2.decisions == {}


@pytest.mark.asyncio
async def test_owner_filtering_paging_and_bounded_page():
    storage = FakeStorage()
    repository = DecisionRepository(storage)
    await repository.insert(make_evidence("alice", "DEC-1"))
    await repository.insert(make_evidence("alice", "DEC-2"))
    await repository.insert(make_evidence("bob", "DEC-3"))
    page = await repository.list_for_owner("alice", page=1, size=1)
    assert len(page) == 1
    assert page[0]["owner_user_id"] == "alice"
    assert await repository.get_for_owner("DEC-3", "alice") is None
    with pytest.raises(ValueError, match="invalid decision page"):
        await repository.list_for_owner("alice", size=101)


@pytest.mark.asyncio
async def test_read_detects_tampering():
    storage = FakeStorage()
    repository = DecisionRepository(storage)
    await repository.insert(make_evidence())
    storage.decisions[("DEC-1", "alice")]["source_json"] = '{"parts":[{"id":1,"purchasePrice":"0.01"}],"suppliers":{},"inventories":{}}'
    with pytest.raises(DecisionIntegrityError, match="fingerprint mismatch"):
        await repository.get_for_owner("DEC-1", "alice")


@pytest.mark.asyncio
async def test_append_event_is_owner_scoped_and_idempotency_ready():
    storage = FakeStorage()
    repository = DecisionRepository(storage)
    await repository.insert(make_evidence())
    assert await repository.append_event(
        "DEC-1", "alice", "APPROVED", actor_user_id="alice",
        payload={"decision": "approve"}, event_key="approval:1",
    ) is True
    assert await repository.append_event(
        "DEC-1", "bob", "APPROVED", actor_user_id="bob",
        payload={}, event_key="approval:1",
    ) is False


@pytest.mark.asyncio
async def test_read_rejects_oversized_stored_payload():
    storage = FakeStorage()
    repository = DecisionRepository(storage)
    await repository.insert(make_evidence())
    storage.decisions[("DEC-1", "alice")]["source_json"] = '"' + ("x" * 1_000_001) + '"'
    with pytest.raises(DecisionIntegrityError, match="exceeds read limit"):
        await repository.get_for_owner("DEC-1", "alice")


@pytest.mark.asyncio
async def test_storage_unavailability_is_explicit():
    storage = FakeStorage()
    storage.fail = True
    with pytest.raises(DecisionStorageError, match="persistence failed"):
        await DecisionRepository(storage).insert(make_evidence())
