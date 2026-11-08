import pytest

from agent.evaluation.comparison import (
    IncompatibleRunsError, compare_runs, evaluate_regression_gates,
)


def report(*, fixture="f", recommendation=1, status="PASSED", safety=1.0, latency=100):
    return {
        "manifest": {"suiteId": "suite", "suiteVersion": 1, "fixtureVersion": "v1",
                     "fixtureFingerprint": fixture, "executionMode": "rule-contract"},
        "cases": [{"scenarioId": "success", "status": status,
                   "result": {"recommendedPartId": recommendation}}],
        "metrics": [
            {"name": "unsafe_write_prevention", "value": safety},
            {"name": "latency_ms", "value": latency},
        ],
    }


def test_comparison_reports_changed_recommendation_and_assertion_regression():
    comparison = compare_runs(report(), report(recommendation=2, status="FAILED"))
    scenario = comparison["scenarioDiffs"][0]
    assert scenario["recommendation"]["changed"] is True
    assert scenario["assertionRegression"] is True


def test_incompatible_fixture_is_rejected():
    with pytest.raises(IncompatibleRunsError, match="fixtureFingerprint"):
        compare_runs(report(fixture="a"), report(fixture="b"))


def test_safety_regression_blocks_even_when_latency_improves():
    comparison = compare_runs(report(safety=1, latency=100), report(safety=.5, latency=10))
    gate = evaluate_regression_gates(comparison, {
        "unsafe_write_prevention": 1.0,
        "latency_ms": 5,
    })
    assert gate["passed"] is False
    assert gate["blockingSafetyRegression"] is True
    assert gate["failures"][0]["metric"] == "unsafe_write_prevention"
