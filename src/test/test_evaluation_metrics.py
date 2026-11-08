from agent.evaluation.metrics import calculate_metrics


def by_name(report, name):
    return next(item for item in report["metrics"] if item["name"] == name)


def test_zero_denominator_and_missing_telemetry_are_not_false_passes():
    report = calculate_metrics({
        "status": "PARTIAL", "executionMode": "workflow-safety",
        "cases": [
            {"status": "TIMED_OUT", "assertions": []},
            {"status": "SETUP_ERROR", "assertions": []},
        ],
    })
    recommendation = by_name(report, "expected_recommendation_agreement")
    assert recommendation["denominator"] == 0
    assert recommendation["value"] is None
    assert recommendation["status"] == "NOT_APPLICABLE"
    assert by_name(report, "latency_distribution")["status"] == "NOT_AVAILABLE"
    assert by_name(report, "token_usage")["value"] is None
    assert by_name(report, "estimated_model_cost")["value"] is None
    assert report["incomplete"] is True


def test_metrics_publish_numerators_denominators_exclusions_latency_tokens_and_cost():
    report = calculate_metrics({
        "status": "COMPLETED", "executionMode": "workflow-safety",
        "cases": [
            {"status": "PASSED", "durationMs": 10,
             "usage": {"inputTokens": 100, "outputTokens": 50},
             "assertions": [
                 {"type": "unsafe_write_count_equals", "status": "PASSED"},
                 {"type": "approval_required", "status": "PASSED"},
             ]},
            {"status": "FAILED", "durationMs": 30, "assertions": [
                {"type": "unsafe_write_count_equals", "status": "FAILED"},
            ]},
            {"status": "CANCELLED", "assertions": []},
        ],
    }, input_cost_per_1k=.01, output_cost_per_1k=.02)
    safety = by_name(report, "unsafe_write_prevention")
    assert (safety["numerator"], safety["denominator"], safety["excluded"]) == (1, 2, 1)
    assert safety["value"] == .5
    assert by_name(report, "latency_distribution")["value"] == {"p50": 10, "p95": 30, "min": 10, "max": 30}
    assert by_name(report, "token_usage")["value"]["total"] == 150
    assert by_name(report, "estimated_model_cost")["status"] == "ESTIMATED"
