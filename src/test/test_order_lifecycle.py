from dataclasses import dataclass

import pytest

from mcp_server.tools.order_tools import AuthorizedOrderClaim, dispatch_claimed_order


DECISION_ID = "DEC-1234567890ABCDEF"
OWNER = "alice"
TRACE = "AGT-TRACE-1"
ORDER_NUMBER = "POA20260101ABCDEF1234"


@dataclass
class FakeResponse:
    status_code: int
    body: dict
    headers: dict

    def json(self):
        return self.body


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self.error:
            raise self.error
        return self.response


class RecordingRepository:
    def __init__(self):
        self.events = []
        self.resolutions = []
        self.reconciliations = []

    async def append_event(self, decision_id, owner_user_id, event_type, **kwargs):
        self.events.append((decision_id, owner_user_id, event_type, kwargs))
        return True

    async def resolve_claim(self, decision_id, owner_user_id, **kwargs):
        self.resolutions.append((decision_id, owner_user_id, kwargs))

    async def mark_reconciliation_required(self, decision_id, owner_user_id, **kwargs):
        self.reconciliations.append((decision_id, owner_user_id, kwargs))


def authorization():
    return AuthorizedOrderClaim(
        claim_token="c" * 64,
        idempotency_key="agent-order:" + "d" * 64,
        evidence={},
    )


def request_data():
    return {
        "orderNumber": ORDER_NUMBER,
        "orderDetail": [{"partId": 7, "quantity": 25, "unitPrice": 12.3}],
    }


async def dispatch(repository, client):
    return await dispatch_claimed_order(
        repository,
        client,
        owner_user_id=OWNER,
        decision_id=DECISION_ID,
        authorization=authorization(),
        request_data=request_data(),
        trace_id=TRACE,
    )


@pytest.mark.asyncio
async def test_success_links_created_order_trace_idempotency_and_audit_reference():
    repository = RecordingRepository()
    client = FakeClient(FakeResponse(
        200,
        {"code": 200, "data": {"id": 91, "orderNumber": ORDER_NUMBER}},
        {
            "X-Trace-Id": "ERP-TRACE-91",
            "Idempotency-Replayed": "false",
            "X-ERP-Audit-Ref": "AUD-91",
        },
    ))
    result = await dispatch(repository, client)

    assert result["id"] == 91
    assert repository.events[0][2] == "ERP_DISPATCHED"
    resolution = repository.resolutions[0][2]
    assert resolution["state"] == "SUCCEEDED"
    assert resolution["order_number"] == ORDER_NUMBER
    assert resolution["order_id"] == 91
    assert resolution["trace_id"] == "ERP-TRACE-91"
    assert resolution["erp_audit_ref"] == "AUD-91"
    assert resolution["outcome"] == {
        "idempotencyOutcome": "CREATED",
        "erpAuditRefStatus": "AVAILABLE",
    }
    sent_headers = client.calls[0][1]["headers"]
    assert sent_headers["Idempotency-Key"] == authorization().idempotency_key
    assert sent_headers["X-Trace-Id"] == TRACE
    assert sent_headers["X-Request-Id"].startswith("ERP-AUD-")


@pytest.mark.asyncio
async def test_idempotent_replay_is_distinct_from_created_order():
    repository = RecordingRepository()
    client = FakeClient(FakeResponse(
        200,
        {"code": 200, "data": {"id": 91, "orderNumber": ORDER_NUMBER, "auditRef": "AUD-91"}},
        {"Idempotency-Replayed": "true"},
    ))
    await dispatch(repository, client)
    resolution = repository.resolutions[0][2]
    assert resolution["outcome"]["idempotencyOutcome"] == "REPLAYED"
    assert resolution["erp_audit_ref"] == "AUD-91"


@pytest.mark.asyncio
async def test_success_without_audit_reference_is_recorded_as_pending():
    repository = RecordingRepository()
    client = FakeClient(FakeResponse(
        200,
        {"code": 200, "data": {"id": 92, "orderNumber": ORDER_NUMBER}},
        {},
    ))
    await dispatch(repository, client)
    resolution = repository.resolutions[0][2]
    assert resolution["state"] == "SUCCEEDED"
    assert resolution["erp_audit_ref"] is None
    assert resolution["outcome"]["erpAuditRefStatus"] == "PENDING"


@pytest.mark.asyncio
async def test_definite_rejection_resolves_claim_without_reconciliation():
    repository = RecordingRepository()
    client = FakeClient(FakeResponse(
        400,
        {"code": 400, "message": "订单字段不合法"},
        {"X-Trace-Id": "ERP-REJECT-1"},
    ))
    result = await dispatch(repository, client)
    assert "订单字段不合法" in result["error"]
    resolution = repository.resolutions[0][2]
    assert resolution["state"] == "REJECTED"
    assert resolution["trace_id"] == "ERP-REJECT-1"
    assert resolution["outcome"]["idempotencyOutcome"] == "REJECTED"
    assert repository.reconciliations == []


@pytest.mark.asyncio
async def test_post_dispatch_timeout_preserves_claim_for_reconciliation():
    repository = RecordingRepository()
    client = FakeClient(error=TimeoutError("read timed out"))
    result = await dispatch(repository, client)
    assert result["reconciliationRequired"] is True
    assert result["orderNumber"] == ORDER_NUMBER
    assert repository.resolutions == []
    reconciliation = repository.reconciliations[0][2]
    assert reconciliation["order_number"] == ORDER_NUMBER
    assert reconciliation["trace_id"] == TRACE
    assert "timed out" in reconciliation["reason"]
