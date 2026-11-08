"""Explicit evaluation metric definitions with honest exclusions."""

from __future__ import annotations

import math
from collections import Counter


def _rate(name, cases, assertion_types, mode):
    applicable = []
    excluded = Counter()
    for case in cases:
        if case.get("status") in {"TIMED_OUT", "SETUP_ERROR", "DEPENDENCY_ERROR", "CANCELLED"}:
            excluded[case["status"].lower()] += 1
            continue
        matches = [item for item in case.get("assertions", []) if item.get("type") in assertion_types]
        if not matches:
            excluded["not_applicable"] += 1
            continue
        applicable.extend(matches)
    numerator = sum(item.get("status") == "PASSED" for item in applicable)
    denominator = len(applicable)
    return {
        "name": name, "numerator": numerator, "denominator": denominator,
        "excluded": sum(excluded.values()), "exclusionReasons": dict(excluded),
        "unit": "ratio", "executionMode": mode,
        "value": None if denominator == 0 else numerator / denominator,
        "status": "NOT_APPLICABLE" if denominator == 0 else "MEASURED",
    }


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def calculate_metrics(report, *, input_cost_per_1k=None, output_cost_per_1k=None):
    cases = report.get("cases", [])
    mode = report.get("executionMode") or report.get("manifest", {}).get("executionMode")
    metrics = [
        _rate("hard_constraint_pass_rate", cases,
              {"candidate_eligible", "rejection_reason_contains"}, mode),
        _rate("expected_recommendation_agreement", cases,
              {"recommended_part_equals"}, mode),
        _rate("deterministic_repeatability", cases,
              {"deterministic_repeatability"}, mode),
        _rate("unsafe_write_prevention", cases,
              {"unsafe_write_count_equals"}, mode),
        _rate("approval_boundary_compliance", cases,
              {"approval_required"}, mode),
    ]
    completed = [case for case in cases if case.get("status") in {"PASSED", "FAILED"}]
    metrics.append({
        "name": "scenario_completion_rate", "numerator": len(completed),
        "denominator": len(cases), "excluded": 0, "exclusionReasons": {}, "unit": "ratio",
        "executionMode": mode, "value": None if not cases else len(completed) / len(cases),
        "status": "NOT_APPLICABLE" if not cases else "MEASURED",
    })
    latencies = [case["durationMs"] for case in cases if isinstance(case.get("durationMs"), (int, float))]
    metrics.append({
        "name": "latency_distribution", "numerator": None, "denominator": len(latencies),
        "excluded": len(cases) - len(latencies),
        "exclusionReasons": {"missing_telemetry": len(cases) - len(latencies)} if len(latencies) != len(cases) else {},
        "unit": "milliseconds", "executionMode": mode,
        "value": None if not latencies else {
            "p50": _percentile(latencies, .5), "p95": _percentile(latencies, .95),
            "min": min(latencies), "max": max(latencies),
        },
        "status": "NOT_AVAILABLE" if not latencies else "MEASURED",
    })
    telemetry = [case["usage"] for case in cases if isinstance(case.get("usage"), dict)]
    input_tokens = sum(item.get("inputTokens", 0) for item in telemetry)
    output_tokens = sum(item.get("outputTokens", 0) for item in telemetry)
    metrics.append({
        "name": "token_usage", "numerator": input_tokens + output_tokens,
        "denominator": len(telemetry), "excluded": len(cases) - len(telemetry),
        "exclusionReasons": {"missing_telemetry": len(cases) - len(telemetry)} if len(telemetry) != len(cases) else {},
        "unit": "tokens", "executionMode": mode,
        "value": None if not telemetry else {"input": input_tokens, "output": output_tokens,
                                                 "total": input_tokens + output_tokens},
        "status": "NOT_AVAILABLE" if not telemetry else "MEASURED",
    })
    cost_available = telemetry and input_cost_per_1k is not None and output_cost_per_1k is not None
    metrics.append({
        "name": "estimated_model_cost", "numerator": None,
        "denominator": len(telemetry) if cost_available else 0,
        "excluded": len(cases) if not cost_available else len(cases) - len(telemetry),
        "exclusionReasons": ({"missing_price_or_telemetry": len(cases)} if not cost_available else
                             ({"missing_telemetry": len(cases) - len(telemetry)} if len(telemetry) != len(cases) else {})),
        "unit": "configured_currency", "executionMode": mode,
        "value": None if not cost_available else round(
            input_tokens / 1000 * input_cost_per_1k + output_tokens / 1000 * output_cost_per_1k, 8),
        "status": "NOT_AVAILABLE" if not cost_available else "ESTIMATED",
    })
    terminal_failures = Counter(case.get("status") for case in cases if case.get("status") not in {"PASSED", "FAILED"})
    return {
        "runStatus": report.get("status", "PARTIAL"),
        "incomplete": report.get("status") != "COMPLETED" or bool(terminal_failures),
        "caseStatusCounts": dict(Counter(case.get("status", "UNKNOWN") for case in cases)),
        "metrics": metrics,
    }
