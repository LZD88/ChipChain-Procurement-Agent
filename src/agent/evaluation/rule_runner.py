"""Deterministic rule-contract evaluation without network or model access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.evaluation.schemas import EvaluationSuite
from agent.evidence_sanitizer import reject_secret_bearing
from agent.policies.canonical import canonical_json
from agent.policies.procurement import parse_decimal
from agent.policies.registry import get_policy


ROOT = Path(__file__).resolve().parents[2]


def load_default_inputs():
    suite = EvaluationSuite.model_validate_json(
        (ROOT / "evaluation" / "suites" / "default-v1.json").read_text(encoding="utf-8")
    )
    fixtures = json.loads(
        (ROOT / "evaluation" / "fixtures" / "procurement-fixtures-v1.json").read_text(encoding="utf-8")
    )
    return suite, fixtures


def evaluate_assertion(assertion, result):
    candidate = next(
        (item for item in result.get("candidates", []) if item.get("partId") == assertion.candidatePartId),
        None,
    )
    if assertion.type == "recommended_part_equals":
        actual = result.get("recommendedPartId")
    elif assertion.type == "policy_version_equals":
        actual = result.get("policyVersion")
    elif assertion.type == "candidate_eligible":
        actual = candidate.get("eligible") if candidate else None
    elif assertion.type == "rejection_reason_contains":
        actual = bool(candidate and any(assertion.reason in reason for reason in candidate.get("rejectedReasons", [])))
    else:
        raise ValueError(f"assertion is not supported by rule-contract: {assertion.type}")
    return {"type": assertion.type, "expected": assertion.expected, "actual": actual,
            "status": "PASSED" if actual == assertion.expected else "FAILED"}


def run_rule_contract(suite=None, fixtures=None, *, policy_version="supplier-score-v1"):
    suite, fixtures = (suite, fixtures) if suite is not None else load_default_inputs()
    reject_secret_bearing(fixtures, "evaluation.fixtures")
    policy = get_policy(policy_version)
    fixture_fingerprint = hashlib.sha256(canonical_json(fixtures).encode("utf-8")).hexdigest()
    manifest = {
        "suiteId": suite.suiteId,
        "suiteFingerprint": suite.content_fingerprint(),
        "fixtureVersion": suite.fixtureVersion,
        "fixtureFingerprint": fixture_fingerprint,
        "policyVersion": policy_version,
        "executionMode": "rule-contract",
        "runnerVersion": "rule-contract-v1",
    }
    cases = []
    for scenario in suite.scenarios:
        if scenario.executionMode != "rule-contract":
            continue
        fixture = fixtures[scenario.fixture["case"]]
        request = fixture["request"]
        result = policy.evaluator(
            part_name=request["partName"], quantity=request["quantity"],
            budget=parse_decimal(request.get("budget")),
            max_lead_time_days=request.get("maxLeadTimeDays"), parts=fixture["parts"],
            suppliers={int(key): value for key, value in fixture["suppliers"].items()},
            inventories={int(key): value for key, value in fixture["inventories"].items()},
        )
        assertions = [evaluate_assertion(assertion, result) for assertion in scenario.assertions]
        cases.append({"scenarioId": scenario.id, "status": "PASSED" if all(
            item["status"] == "PASSED" for item in assertions) else "FAILED",
            "assertions": assertions, "result": result})
    report = {"manifest": manifest, "status": "COMPLETED", "cases": cases}
    report["reportFingerprint"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report
