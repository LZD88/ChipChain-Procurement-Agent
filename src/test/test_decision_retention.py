from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest

from agent.decision_repository import DecisionRepository


class RetentionCursor:
    def __init__(self, storage):
        self.storage = storage
        self.rowcount = 0
        self.rows = []

    async def execute(self, sql, params=None):
        if sql.startswith("SELECT d.decision_id"):
            now, days, limit = params
            cutoff = now - timedelta(days=days)
            self.rows = [dict(row) for key, row in self.storage.decisions.items()
                         if row["created_at"] < cutoff and row["purged_at"] is None
                         and key not in self.storage.active_holds][:limit]
        elif sql.startswith("INSERT INTO procurement_decision_tombstones"):
            self.storage.tombstones[(params[0], params[1])] = {
                "decision_id": params[0], "owner_user_id": params[1],
                "policy_version": params[2], "evidence_fingerprint": params[3],
                "original_created_at": params[4], "purged_at": params[5], "purge_reason": params[6],
            }
            self.rowcount = 1
        elif sql.startswith("UPDATE procurement_decisions SET request_json=NULL"):
            row = self.storage.decisions[(params[1], params[2])]
            if row["purged_at"] is None:
                row.update(request_json=None, source_json=None, result_json=None,
                           purged_at=params[0], purge_reason="retention_expired")
                self.rowcount = 1
        elif sql.startswith("INSERT INTO procurement_decision_events"):
            self.storage.events.append(params)
            self.rowcount = 1

    async def fetchall(self):
        return self.rows


class RetentionStorage:
    def __init__(self, now):
        def row(decision_id, age):
            return {
                "decision_id": decision_id, "owner_user_id": "alice",
                "policy_version": "supplier-score-v1", "evidence_fingerprint": "a" * 64,
                "created_at": now - timedelta(days=age), "purged_at": None,
                "request_json": "{}", "source_json": "{}", "result_json": "{}",
            }
        self.decisions = {("old", "alice"): row("old", 100),
                          ("held", "alice"): row("held", 100),
                          ("recent", "alice"): row("recent", 5)}
        self.active_holds = {("held", "alice")}
        self.tombstones = {}
        self.events = []

    @asynccontextmanager
    async def cursor(self, transaction=False):
        yield RetentionCursor(self)


@pytest.mark.asyncio
async def test_purge_preserves_holds_and_recent_evidence_and_writes_tombstone():
    now = datetime(2026, 6, 1)
    storage = RetentionStorage(now)
    purged = await DecisionRepository(storage).purge_expired(now=now, retention_days=90)
    assert purged == ["old"]
    assert storage.decisions[("old", "alice")]["request_json"] is None
    assert storage.decisions[("held", "alice")]["request_json"] == "{}"
    assert storage.decisions[("recent", "alice")]["request_json"] == "{}"
    tombstone = storage.tombstones[("old", "alice")]
    assert tombstone["evidence_fingerprint"] == "a" * 64
    assert tombstone["policy_version"] == "supplier-score-v1"
    assert tombstone["purge_reason"] == "retention_expired"


@pytest.mark.asyncio
async def test_purge_is_idempotent_and_does_not_rewrite_tombstone():
    now = datetime(2026, 6, 1)
    storage = RetentionStorage(now)
    repository = DecisionRepository(storage)
    assert await repository.purge_expired(now=now, retention_days=90) == ["old"]
    first = dict(storage.tombstones[("old", "alice")])
    assert await repository.purge_expired(now=now + timedelta(days=1), retention_days=90) == []
    assert storage.tombstones[("old", "alice")] == first


@pytest.mark.asyncio
async def test_invalid_retention_configuration_is_rejected():
    storage = RetentionStorage(datetime(2026, 6, 1))
    with pytest.raises(ValueError, match="invalid decision retention"):
        await DecisionRepository(storage).purge_expired(retention_days=0)
