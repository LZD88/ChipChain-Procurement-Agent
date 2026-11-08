from collections import deque

import pytest

from mcp_server.tools.order_tools import reconcile_ambiguous_order


DECISION_ID = "DEC-RECONCILE"
OWNER = "alice"
ORDER_NUMBER = "POA20260101RECONCILE1"
IDEMPOTENCY_KEY = "agent-order:" + "e" * 64
TRACE = "AGT-RECONCILE-1"
REQUEST = {
    "orderNumber": ORDER_NUMBER,
    "remark": "decisionId=DEC-RECONCILE; policyVersion=supplier-score-v1",
    "orderDetail": [{"partId": 7, "quantity": 25, "unitPrice": 12.3}],
}


class FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.body


class ReconciliationClient:
    def __init__(self, *, lookups, retry_response=None):
        self.lookups = deque(lookups)
        self.retry_response = retry_response
        self.get_calls = []
        self.post_calls = []

    async def get(self, path):
        self.get_calls.append(path)
        return self.lookups.popleft()

    async def post(self, path, **kwargs):
        self.post_calls.append((path, kwargs))
        return self.retry_response


class ReconciliationRepository:
    def __init__(self):
        self.claim = {
            "decision_id": DECISION_ID,
            "owner_user_id": OWNER,
            "claim_token": "c" * 64,
            "idempotency_key": IDEMPOTENCY_KEY,
            "claim_state": "RECONCILIATION_REQUIRED",
            "order_number": ORDER_NUMBER,
            "trace_id": TRACE,
        }
        self.events = [{
            "event_type": "ERP_DISPATCHED",
            "payload": {
                "idempotencyKey": IDEMPOTENCY_KEY,
                "orderNumber": ORDER_NUMBER,
                "traceId": TRACE,
                "orderRequest": REQUEST,
            },
        }]
        self.appended = []
        self.resolutions = []

    async def get_claim(self, decision_id, owner_user_id):
        return self.claim if (decision_id, owner_user_id) == (DECISION_ID, OWNER) else None

    async def events_for_owner(self, decision_id, owner_user_id):
        return list(self.events)

    async def append_event(self, decision_id, owner_user_id, event_type, **kwargs):
        self.appended.append((event_type, kwargs))
        return True

    async def resolve_claim(self, decision_id, owner_user_id, **kwargs):
        self.resolutions.append(kwargs)


def lookup(status, order=None):
    data = {"status": status}
    if order is not None:
        data["order"] = order
    return FakeResponse({"code": 200, "data": data}, headers={"X-Trace-Id": "ERP-TRACE"})


@pytest.mark.asyncio
async def test_reconciliation_resolves_existing_order_without_second_write():
    repository = ReconciliationRepository()
    client = ReconciliationClient(lookups=[lookup(
        "COMPLETED", {"id": 501, "orderNumber": ORDER_NUMBER},
    )])
    result = await reconcile_ambiguous_order(
        repository,
        client,
        owner_user_id=OWNER,
        decision_id=DECISION_ID,
        retry_if_absent=True,
        retry_delay_seconds=0,
    )

    assert result["id"] == 501
    assert result["createdDuringReconciliation"] is False
    assert client.post_calls == []
    resolution = repository.resolutions[0]
    assert resolution["state"] == "SUCCEEDED"
    assert resolution["order_id"] == 501
    assert resolution["outcome"]["idempotencyOutcome"] == "RECONCILED_EXISTING"


@pytest.mark.asyncio
async def test_absent_order_retries_once_with_same_request_and_idempotency_key():
    repository = ReconciliationRepository()
    retry_response = FakeResponse(
        {"code": 200, "data": {"id": 502, "orderNumber": ORDER_NUMBER}},
        headers={"X-ERP-Audit-Ref": "AUD-502", "Idempotency-Replayed": "false"},
    )
    client = ReconciliationClient(lookups=[lookup("ABSENT")], retry_response=retry_response)
    result = await reconcile_ambiguous_order(
        repository,
        client,
        owner_user_id=OWNER,
        decision_id=DECISION_ID,
        retry_if_absent=True,
        retry_delay_seconds=0,
    )

    assert result["id"] == 502
    assert result["createdDuringReconciliation"] is True
    assert len(client.post_calls) == 1
    path, call = client.post_calls[0]
    assert path == "/orders/create"
    assert call["json"] == REQUEST
    assert call["headers"]["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert repository.resolutions[0]["state"] == "SUCCEEDED"
    assert repository.resolutions[0]["outcome"]["idempotencyOutcome"] == "RECONCILIATION_RETRY_CREATED"


@pytest.mark.asyncio
async def test_processing_lookup_stops_at_configured_bound_without_write():
    repository = ReconciliationRepository()
    client = ReconciliationClient(lookups=[lookup("PROCESSING"), lookup("PROCESSING")])
    result = await reconcile_ambiguous_order(
        repository,
        client,
        owner_user_id=OWNER,
        decision_id=DECISION_ID,
        max_attempts=2,
        retry_if_absent=True,
        retry_delay_seconds=0,
    )

    assert result["reconciliationRequired"] is True
    assert result["attempts"] == 2
    assert len(client.get_calls) == 2
    assert client.post_calls == []
    assert repository.resolutions == []
