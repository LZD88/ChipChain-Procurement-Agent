from copy import deepcopy

import pytest

from api_view.api.chat import record_hitl_approval_requests, record_hitl_resume_events


class ApprovalRepository:
    def __init__(self):
        self.evidence = {
            ("DEC-APPROVAL", "alice"): {
                "decision_id": "DEC-APPROVAL",
                "owner_user_id": "alice",
                "thread_id": "thread-1",
                "result": {"recommendedPartId": 7},
            },
        }
        self.original = deepcopy(self.evidence)
        self.events = []
        self.event_keys = set()

    async def get_for_owner(self, decision_id, owner_user_id):
        return self.evidence.get((decision_id, owner_user_id))

    async def append_event(
        self, decision_id, owner_user_id, event_type, *, event_key, payload, **kwargs,
    ):
        key = (decision_id, owner_user_id, event_key)
        if key in self.event_keys:
            return False
        self.event_keys.add(key)
        self.events.append({
            "event_id": len(self.events) + 1,
            "decision_id": decision_id,
            "owner_user_id": owner_user_id,
            "event_type": event_type,
            "event_key": event_key,
            "payload": payload,
        })
        return True

    async def pending_approvals_for_thread(self, thread_id, owner_user_id, *, limit):
        requests = [
            event for event in self.events
            if event["owner_user_id"] == owner_user_id
            and event["event_type"] == "APPROVAL_REQUESTED"
            and event["payload"]["threadId"] == thread_id
        ]
        pending = []
        for event in requests:
            approval_id = event["payload"]["approvalRequestId"]
            resolved_key = (event["decision_id"], owner_user_id, f"approval-resolution:{approval_id}")
            if resolved_key not in self.event_keys:
                pending.append(event)
        return pending[:limit]


def action_request():
    return [{
        "id": "tool-call-1",
        "name": "order_create",
        "args": {"decision_id": "DEC-APPROVAL", "policy_version": "supplier-score-v1"},
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "event_type"),
    [("approve", "APPROVED"), ("reject", "REJECTED")],
)
async def test_resume_appends_approval_or_rejection_without_mutating_evidence(decision, event_type):
    repository = ApprovalRepository()
    assert await record_hitl_approval_requests(
        repository,
        thread_id="thread-1",
        user_id="alice",
        action_requests=action_request(),
    ) == 1
    await record_hitl_resume_events(
        repository,
        thread_id="thread-1",
        user_id="alice",
        decisions=[{"type": decision}],
    )
    assert [event["event_type"] for event in repository.events] == [
        "APPROVAL_REQUESTED", event_type,
    ]
    assert repository.evidence == repository.original


@pytest.mark.asyncio
async def test_cross_user_approval_request_cannot_link_foreign_decision():
    repository = ApprovalRepository()
    with pytest.raises(RuntimeError, match="不属于当前会话"):
        await record_hitl_approval_requests(
            repository,
            thread_id="thread-1",
            user_id="bob",
            action_requests=action_request(),
        )
    assert repository.events == []


@pytest.mark.asyncio
async def test_resume_api_thread_dependency_hides_cross_user_thread(monkeypatch):
    from fastapi import HTTPException
    from api_view import auth

    async def does_not_belong(thread_id, username):
        return False

    monkeypatch.setattr(auth.mysql_storage, "thread_belongs_to", does_not_belong)
    with pytest.raises(HTTPException) as caught:
        await auth.require_thread("thread-1", "bob")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_repeated_resume_is_rejected_and_does_not_duplicate_event():
    repository = ApprovalRepository()
    await record_hitl_approval_requests(
        repository,
        thread_id="thread-1",
        user_id="alice",
        action_requests=action_request(),
    )
    await record_hitl_resume_events(
        repository,
        thread_id="thread-1",
        user_id="alice",
        decisions=[{"type": "approve"}],
    )
    with pytest.raises(ValueError, match="已处理"):
        await record_hitl_resume_events(
            repository,
            thread_id="thread-1",
            user_id="alice",
            decisions=[{"type": "approve"}],
        )
    assert [event["event_type"] for event in repository.events].count("APPROVED") == 1


@pytest.mark.asyncio
async def test_invalid_resume_shape_is_rejected_before_event_persistence():
    from agent.input_guard import InputSafetyError, get_input_guard

    repository = ApprovalRepository()
    await record_hitl_approval_requests(
        repository,
        thread_id="thread-1",
        user_id="alice",
        action_requests=action_request(),
    )
    with pytest.raises(InputSafetyError):
        await get_input_guard().check_resume(
            {"decisions": [{"type": "override"}]},
            user_id="alice",
        )
    assert [event["event_type"] for event in repository.events] == ["APPROVAL_REQUESTED"]
