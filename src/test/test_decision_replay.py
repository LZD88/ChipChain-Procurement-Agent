from copy import deepcopy
from decimal import Decimal

from agent.decision_replay import replay_diff, replay_policy
from agent.policies.registry import get_replay_policy
from mcp_server.tools.procurement_tools import build_durable_evidence
from test_durable_comparison import identity, result, source


def test_original_policy_replay_uses_stored_facts_and_returns_structured_zero_diff():
    original_result = result(Decimal("9000"))
    evidence, _returned = build_durable_evidence(
        identity=identity(),
        request=original_result["request"],
        source=source(),
        result=original_result,
    )
    stored = {
        "request": deepcopy(evidence.request),
        "source": deepcopy(evidence.source),
        "result": deepcopy(evidence.result),
    }
    before = deepcopy(stored)
    simulated = replay_policy(get_replay_policy("supplier-score-v1").evaluator, stored)
    diff = replay_diff(stored["result"], simulated)

    assert simulated["recommendedPartId"] == stored["result"]["recommendedPartId"]
    assert diff["recommendation"]["changed"] is False
    assert all(not item["eligibility"]["changed"] for item in diff["candidates"])
    assert all(not item["rank"]["changed"] for item in diff["candidates"])
    assert all(not item["rejectionReasons"]["added"] for item in diff["candidates"])
    assert all(not item["rejectionReasons"]["removed"] for item in diff["candidates"])
    assert stored == before
