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


def repeated_report(*observations):
    result = report()
    result["manifest"]["executionMode"] = "workflow-safety"
    result["cases"] = [
        {"scenarioId": "success", "repetition": repetition, "status": status,
         "result": {"recommendedPartId": recommendation}}
        for repetition, status, recommendation in observations
    ]
    return result


def test_early_failed_repetition_blocks_gate_even_when_last_repetition_passes():
    baseline = repeated_report((1, "PASSED", 1), (2, "PASSED", 1), (3, "PASSED", 1))
    candidate = repeated_report((1, "FAILED", 2), (2, "PASSED", 1), (3, "PASSED", 1))
    comparison = compare_runs(baseline, candidate)

    assert len(comparison["scenarioDiffs"]) == 3
    first, second, last = comparison["scenarioDiffs"]
    assert first["repetition"] == 1
    assert first["assertionRegression"] is True
    assert first["recommendation"]["changed"] is True
    assert not second["assertionRegression"] and not last["assertionRegression"]
    gate = evaluate_regression_gates(comparison, {})
    assert gate["passed"] is False
    assert gate["failures"] == [{"scenarioId": "success", "repetition": 1, "priority": "ASSERTION"}]

    summary = comparison["scenarioSummaries"][0]
    assert summary["before"]["passRate"] == 1
    assert summary["before"]["hasVariation"] is False
    assert summary["after"]["sampleCount"] == 3
    assert summary["after"]["passedCount"] == 2
    assert summary["after"]["passRate"] == pytest.approx(2 / 3)
    assert summary["after"]["statusCounts"] == {"FAILED": 1, "PASSED": 2}
    assert summary["after"]["hasVariation"] is True
    assert summary["after"]["recommendationCounts"] == [{"partId": 1, "count": 2}, {"partId": 2, "count": 1}]


def test_repetitions_pair_by_identity_independently_of_report_order():
    baseline = repeated_report((2, "PASSED", 2), (1, "PASSED", 1))
    candidate = repeated_report((1, "PASSED", 1), (2, "PASSED", 2))
    comparison = compare_runs(baseline, candidate)
    assert [case["repetition"] for case in comparison["scenarioDiffs"]] == [1, 2]
    assert not any(case["recommendation"]["changed"] for case in comparison["scenarioDiffs"])
    assert evaluate_regression_gates(comparison, {})["passed"] is True
    # All assertions can pass while repeated recommendations still vary.
    assert comparison["scenarioSummaries"][0]["after"]["hasVariation"] is True


def test_missing_repetition_is_retained_and_blocks_gate():
    baseline = repeated_report((1, "PASSED", 1), (2, "PASSED", 1), (3, "PASSED", 1))
    candidate = repeated_report((3, "PASSED", 1), (1, "PASSED", 1))
    comparison = compare_runs(baseline, candidate)
    missing = comparison["scenarioDiffs"][1]
    assert missing["repetition"] == 2
    assert missing["afterStatus"] is None
    assert missing["assertionRegression"] is True
    assert evaluate_regression_gates(comparison, {})["passed"] is False
    assert comparison["scenarioSummaries"][0]["after"]["sampleCount"] == 2


def test_legacy_single_sample_and_absent_baseline_do_not_claim_repeatability():
    baseline = report()
    candidate = report()
    baseline["cases"] = []
    candidate["cases"][0]["result"] = None
    comparison = compare_runs(baseline, candidate)
    assert comparison["scenarioDiffs"][0]["repetition"] == 1
    summary = comparison["scenarioSummaries"][0]
    assert summary["before"]["passRate"] is None
    assert summary["before"]["hasVariation"] is None
    assert summary["after"]["hasVariation"] is None
    assert summary["after"]["recommendationCounts"] == []


def test_duplicate_case_identity_is_rejected_instead_of_overwritten():
    candidate = repeated_report((1, "FAILED", 1), (1, "PASSED", 1))
    with pytest.raises(ValueError, match="duplicate evaluation case"):
        compare_runs(repeated_report((1, "PASSED", 1)), candidate)


def test_nonpassing_and_incomplete_samples_remain_in_pass_rate_denominator():
    baseline = repeated_report((1, "PASSED", None), (2, "PASSED", None), (3, "PASSED", None))
    candidate = repeated_report((1, "PASSED", None), (2, "TIMED_OUT", None), (3, "CANCELLED", None))
    comparison = compare_runs(baseline, candidate)
    after = comparison["scenarioSummaries"][0]["after"]
    assert after["sampleCount"] == 3
    assert after["passedCount"] == 1
    assert after["passRate"] == pytest.approx(1 / 3)
    assert after["recommendationCounts"] == [{"partId": None, "count": 3}]
    assert len(evaluate_regression_gates(comparison, {})["failures"]) == 2
