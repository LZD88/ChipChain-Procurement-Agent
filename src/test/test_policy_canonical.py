from copy import deepcopy
from decimal import Decimal

import pytest

from agent.policies.canonical import (
    CANONICALIZATION_VERSION,
    canonical_evidence_payload,
    canonical_json,
    decision_id_from_fingerprint,
    evidence_fingerprint,
)


def evidence(parts=None, result_candidates=None):
    return {
        "policy_version": "supplier-score-v1",
        "request": {"partName": "六轴惯性传感器", "quantity": 2, "budget": Decimal("10.00")},
        "source": {
            "parts": parts or [
                {"id": 2, "partCode": "B", "supplierId": 2, "purchasePrice": 2.0},
                {"id": 1, "partCode": "A", "supplierId": 1, "purchasePrice": Decimal("1.00")},
            ],
            "suppliers": {2: {"name": "乙"}, 1: {"name": "甲"}},
            "inventories": {1: None, 2: {"currentQuantity": 3}},
        },
        "result": {
            "recommendedPartId": 1,
            "candidates": result_candidates or [{"partId": 1}, {"partId": 2}],
        },
    }


def fingerprint(values):
    return evidence_fingerprint(**values)


def test_canonical_json_normalizes_decimal_float_null_and_unicode():
    value = {
        "中文": "传感器",
        "decimal": Decimal("1.2300"),
        "float": 1.0,
        "negativeZero": Decimal("-0.00"),
        "null": None,
    }
    encoded = canonical_json(value)
    assert encoded == '{"decimal":"1.23","float":"1","negativeZero":"0","null":null,"中文":"传感器"}'


def test_source_candidate_order_does_not_change_fingerprint():
    first = evidence()
    second = evidence(parts=list(reversed(first["source"]["parts"])))
    assert fingerprint(first) == fingerprint(second)


def test_result_ranking_and_material_values_change_fingerprint():
    original = evidence()
    reordered_result = evidence(result_candidates=[{"partId": 2}, {"partId": 1}])
    changed = deepcopy(original)
    changed["source"]["parts"][0]["purchasePrice"] = 2.01
    assert fingerprint(original) != fingerprint(reordered_result)
    assert fingerprint(original) != fingerprint(changed)


def test_repeated_fingerprint_is_full_sha256_and_has_versioned_payload():
    values = evidence()
    first = fingerprint(values)
    second = fingerprint(deepcopy(values))
    payload = canonical_evidence_payload(**values)
    assert first == second
    assert len(first) == 64
    assert payload["canonicalizationVersion"] == CANONICALIZATION_VERSION
    assert decision_id_from_fingerprint(first) == "DEC-" + first[:16].upper()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN")])
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        canonical_json({"value": value})
