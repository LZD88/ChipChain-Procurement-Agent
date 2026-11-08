import json
from decimal import Decimal
from pathlib import Path

from agent.policies import get_policy
from agent.policies.procurement import compare_candidates
from test_procurement_compare import INVENTORIES, PARTS, SUPPLIERS


FIXTURE = Path(__file__).with_name("fixtures") / "procurement_compare_v1_expected.json"


def test_extracted_policy_matches_pre_extraction_fixture():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = compare_candidates(
        part_name="六轴惯性传感器",
        quantity=500,
        parts=PARTS,
        suppliers=SUPPLIERS,
        inventories=INVENTORIES,
        budget=Decimal("9000"),
        max_lead_time_days=14,
    )

    assert result["decisionId"] == expected["decisionId"]
    assert result["policyVersion"] == expected["policyVersion"]
    assert result["recommendedPartId"] == expected["recommendedPartId"]
    assert result["recommendedSupplierId"] == expected["recommendedSupplierId"]
    assert result["minimumFeasibleBudget"] == expected["minimumFeasibleBudget"]
    assert [item["partId"] for item in result["candidates"]] == expected["candidateOrder"]
    assert [item["totalScore"] for item in result["candidates"]] == expected["candidateScores"]
    assert [item["totalAmount"] for item in result["candidates"]] == expected["candidateAmounts"]
    assert result["scoreWeights"] == expected["scoreWeights"]


def test_v1_policy_is_available_through_registry():
    policy = get_policy("supplier-score-v1")
    assert policy.evaluator is compare_candidates
    assert policy.replay_supported is True
