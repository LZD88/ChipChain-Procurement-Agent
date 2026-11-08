import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.authorization import DECISION_AUDIT, EVALUATION_MAINTAIN, EVALUATION_VIEW
from api_view.api import decisions
from api_view.auth import require_user


NOW = datetime(2026, 1, 1)


class FakeRepository:
    def __init__(self):
        self.rows = {
            ("DEC-ALICE", "alice"): {
                "decision_id": "DEC-ALICE",
                "owner_user_id": "alice",
                "thread_id": "thread-1",
                "policy_version": "supplier-score-v1",
                "canonicalization_version": "procurement-json-v1",
                "evidence_fingerprint": "a" * 64,
                "request": {"partName": "传感器", "quantity": 1},
                "source": {"parts": []},
                "result": {"recommendedPartId": 7},
                "trace_id": "TRACE-1",
                "created_at": NOW,
                "expires_at": NOW + timedelta(minutes=15),
                "purged_at": None,
                "purge_reason": None,
                "password_hash": "must-not-leak",
            },
            ("DEC-BOB", "bob"): {
                "decision_id": "DEC-BOB", "owner_user_id": "bob",
                "policy_version": "supplier-score-v1", "evidence_fingerprint": "b" * 64,
                "created_at": NOW, "expires_at": NOW + timedelta(minutes=15),
                "purged_at": None, "request": {}, "source": {}, "result": {},
            },
        }
        self.events = [{
            "event_id": 1, "event_type": "CREATED", "event_key": "created",
            "actor_user_id": "alice", "payload": {"traceId": "TRACE-1"},
            "created_at": NOW,
        }]
        self.audit = []
        self.replays = []

    async def list_for_owner(self, owner, *, page, size):
        return [row for (_id, row_owner), row in self.rows.items() if row_owner == owner]

    async def list_for_auditor(self, *, owner_user_id=None, page, size):
        return [row for (_id, owner), row in self.rows.items()
                if owner_user_id is None or owner == owner_user_id]

    async def get_for_owner(self, decision_id, owner):
        return self.rows.get((decision_id, owner))

    async def events_for_owner(self, decision_id, owner):
        return list(self.events) if (decision_id, owner) in self.rows else []

    async def append_event(self, decision_id, owner, event_type, **kwargs):
        self.events.append({
            "event_id": len(self.events) + 1, "event_type": event_type,
            "event_key": kwargs.get("event_key"), "actor_user_id": kwargs.get("actor_user_id"),
            "payload": kwargs.get("payload", {}), "created_at": NOW,
        })
        return True

    async def record_audit_action(self, **kwargs):
        self.audit.append(kwargs)

    async def record_replay(self, **kwargs):
        self.replays.append(kwargs)


@pytest.fixture
def api(monkeypatch):
    repository = FakeRepository()
    actor = {"name": "alice"}
    grants = {"alice": set(), "auditor": {DECISION_AUDIT}}

    async def current_actor():
        return actor["name"]

    async def has_capability(username, capability):
        return capability in grants.get(username, set())

    monkeypatch.setattr(decisions, "DecisionRepository", lambda storage: repository)
    monkeypatch.setattr(decisions.mysql_storage, "has_capability", has_capability)
    app = FastAPI()
    app.include_router(decisions.router, prefix="/api")
    app.dependency_overrides[require_user] = current_actor
    return TestClient(app), repository, actor, grants


def test_owner_list_detail_lifecycle_are_filtered_redacted_and_no_store(api):
    client, _repository, _actor, _grants = api
    listed = client.get("/api/decisions", headers={"X-Requested-With": "DeepAgent"})
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert [item["decisionId"] for item in listed.json()["items"]] == ["DEC-ALICE"]

    detail = client.get("/api/decisions/DEC-ALICE")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert "password_hash" not in detail.text
    assert detail.json()["lifecycle"][0]["eventType"] == "CREATED"

    lifecycle = client.get("/api/decisions/DEC-ALICE/lifecycle")
    assert lifecycle.status_code == 200
    assert lifecycle.json()["decisionId"] == "DEC-ALICE"


def test_auditor_can_filter_owner_but_other_grants_cannot(api):
    client, _repository, actor, grants = api
    actor["name"] = "auditor"
    response = client.get("/api/decisions", params={"owner": "bob"})
    assert [item["decisionId"] for item in response.json()["items"]] == ["DEC-BOB"]
    assert client.get("/api/decisions/DEC-BOB", params={"owner": "bob"}).status_code == 200

    for capability in (EVALUATION_VIEW, EVALUATION_MAINTAIN):
        actor["name"] = "viewer"
        grants["viewer"] = {capability}
        denied = client.get("/api/decisions/DEC-BOB", params={"owner": "bob"})
        assert denied.status_code == 404
        assert denied.json()["detail"] == "决策证据不存在"


def test_pagination_bounds_and_denied_lookup_are_uniform(api):
    client, repository, _actor, _grants = api
    assert client.get("/api/decisions", params={"page": 0}).status_code == 422
    assert client.get("/api/decisions", params={"size": 101}).status_code == 422
    denied = client.get("/api/decisions/DEC-BOB", params={"owner": "bob"})
    missing = client.get("/api/decisions/DEC-MISSING")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    assert denied.headers["cache-control"] == missing.headers["cache-control"] == "no-store"
    assert [entry["outcome"] for entry in repository.audit[-2:]] == ["DENIED", "NOT_FOUND"]


def test_json_export_has_integrity_fingerprint_and_audit_events(api):
    client, repository, _actor, _grants = api
    response = client.get("/api/decisions/DEC-ALICE/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].endswith('DEC-ALICE.json"')
    body = response.json()
    fingerprint = body.pop("exportFingerprint")
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert fingerprint == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert repository.events[-1]["event_type"] == "EXPORTED"
    assert repository.audit[-1]["action"] == "DECISION_EXPORT"
    assert repository.audit[-1]["outcome"] == "SUCCESS"


def test_replay_is_simulated_persisted_and_does_not_mutate_original_or_claim(api):
    client, repository, _actor, _grants = api
    original = deepcopy(repository.rows[("DEC-ALICE", "alice")])
    response = client.post(
        "/api/decisions/DEC-ALICE/replay",
        json={"policyVersion": "supplier-score-v1"},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["manifest"]["simulation"] is True
    assert set(body["diff"]) == {"recommendation", "candidates"}
    assert repository.replays[0]["decision_id"] == "DEC-ALICE"
    assert repository.replays[0]["manifest"]["simulation"] is True
    assert repository.rows[("DEC-ALICE", "alice")] == original
    assert not hasattr(repository, "claims")


def test_replay_rejects_unknown_policy_and_arbitrary_source_payload(api):
    client, repository, _actor, _grants = api
    unknown = client.post(
        "/api/decisions/DEC-ALICE/replay",
        json={"policyVersion": "does-not-exist"},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert unknown.status_code == 422
    injected = client.post(
        "/api/decisions/DEC-ALICE/replay",
        json={"policyVersion": "supplier-score-v1", "source": {"parts": []}},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert injected.status_code == 422
    assert repository.replays == []
