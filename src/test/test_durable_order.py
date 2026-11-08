import asyncio
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from agent.decision_repository import (
    DecisionClaimedError,
    DecisionIntegrityError,
    DecisionRepository,
)
from mcp_server.tools.order_tools import (
    OrderDecisionValidationError,
    authorize_order_from_evidence,
)
from test_decision_repository import FakeStorage, make_evidence


NOW = datetime(2026, 1, 1, 12, 0, 0)
VALID_DETAIL = [{"partId": 7, "quantity": 25, "unitPrice": 12.30}]


def stored_evidence(*, expires_at=None):
    return {
        "decision_id": "DEC-DURABLE",
        "owner_user_id": "alice",
        "policy_version": "supplier-score-v1",
        "expires_at": expires_at or NOW + timedelta(minutes=15),
        "purged_at": None,
        "result": {
            "policyVersion": "supplier-score-v1",
            "recommendedPartId": 7,
            "candidates": [{
                "partId": 7,
                "eligible": True,
                "quantity": 25,
                "unitPrice": 12.3,
            }],
        },
    }


class SharedClaims:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.claimed = set()


class FakeRepository:
    def __init__(self, evidence, shared=None):
        self.evidence = evidence
        self.shared = shared or SharedClaims()

    async def get_for_owner(self, decision_id, owner_user_id):
        if (self.evidence is None
                or decision_id != self.evidence["decision_id"]
                or owner_user_id != self.evidence["owner_user_id"]):
            return None
        return deepcopy(self.evidence)

    async def claim(self, decision_id, owner_user_id, **kwargs):
        key = (decision_id, owner_user_id)
        async with self.shared.lock:
            if key in self.shared.claimed:
                raise DecisionClaimedError("already claimed")
            self.shared.claimed.add(key)


async def authorize(repository, *, detail=None, policy="supplier-score-v1", token="a" * 64):
    return await authorize_order_from_evidence(
        repository,
        owner_user_id="alice",
        decision_id="DEC-DURABLE",
        policy_version=policy,
        order_detail=detail or VALID_DETAIL,
        expected_delivery_date="2026-02-01",
        now=NOW,
        claim_token=token,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        [{"partId": 8, "quantity": 25, "unitPrice": 12.30}],
        [{"partId": 7, "quantity": 26, "unitPrice": 12.30}],
        [{"partId": 7, "quantity": 25, "unitPrice": 12.31}],
    ],
    ids=["part", "quantity", "price"],
)
async def test_tampered_order_fields_are_rejected_before_claim(detail):
    repository = FakeRepository(stored_evidence())
    with pytest.raises(OrderDecisionValidationError, match="不一致"):
        await authorize(repository, detail=detail)
    assert repository.shared.claimed == set()


@pytest.mark.asyncio
async def test_tampered_policy_and_expired_evidence_are_rejected_before_claim():
    repository = FakeRepository(stored_evidence())
    with pytest.raises(OrderDecisionValidationError, match="规则版本不匹配"):
        await authorize(repository, policy="supplier-score-v2")
    assert repository.shared.claimed == set()

    expired = FakeRepository(stored_evidence(expires_at=NOW))
    with pytest.raises(OrderDecisionValidationError, match="已过期"):
        await authorize(expired)
    assert expired.shared.claimed == set()


@pytest.mark.asyncio
async def test_repository_fingerprint_verification_precedes_claim():
    storage = FakeStorage()
    evidence = make_evidence(decision_id="DEC-DURABLE")
    await DecisionRepository(storage).insert(evidence)
    storage.decisions[("DEC-DURABLE", "alice")]["result_json"] = (
        '{"policyVersion":"supplier-score-v1","recommendedPartId":7,'
        '"candidates":[{"partId":7,"eligible":true,"quantity":25,"unitPrice":12.3}]}'
    )
    with pytest.raises(DecisionIntegrityError, match="fingerprint mismatch"):
        await authorize_order_from_evidence(
            DecisionRepository(storage),
            owner_user_id="alice",
            decision_id="DEC-DURABLE",
            policy_version="supplier-score-v1",
            order_detail=VALID_DETAIL,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_duplicate_use_and_two_mcp_boundary_instances_race_to_one_claim():
    shared = SharedClaims()
    first = FakeRepository(stored_evidence(), shared)
    second = FakeRepository(stored_evidence(), shared)

    async def attempt(repository, token):
        try:
            await authorize(repository, token=token)
            return "won"
        except DecisionClaimedError:
            return "claimed"

    outcomes = await asyncio.gather(
        attempt(first, "1" * 64),
        attempt(second, "2" * 64),
    )
    assert sorted(outcomes) == ["claimed", "won"]
    with pytest.raises(DecisionClaimedError):
        await authorize(first, token="3" * 64)
