"""Compatible evaluation comparisons and safety-first regression gates."""

from __future__ import annotations


SAFETY_METRICS = frozenset({"unsafe_write_prevention", "approval_boundary_compliance"})


class IncompatibleRunsError(ValueError):
    pass


def _manifest(report):
    return report.get("manifest", {})


def ensure_compatible(baseline, candidate):
    before, after = _manifest(baseline), _manifest(candidate)
    for key in ("suiteId", "suiteVersion", "fixtureVersion", "fixtureFingerprint", "executionMode"):
        if before.get(key) != after.get(key):
            raise IncompatibleRunsError(f"evaluation runs are incompatible: {key} differs")


def _cases(report):
    return {item["scenarioId"]: item for item in report.get("cases", [])}


def compare_runs(baseline, candidate):
    ensure_compatible(baseline, candidate)
    before_cases, after_cases = _cases(baseline), _cases(candidate)
    scenario_diffs = []
    for scenario_id in sorted(set(before_cases) | set(after_cases)):
        before, after = before_cases.get(scenario_id), after_cases.get(scenario_id)
        before_result, after_result = (before or {}).get("result", {}), (after or {}).get("result", {})
        scenario_diffs.append({
            "scenarioId": scenario_id,
            "beforeStatus": (before or {}).get("status"),
            "afterStatus": (after or {}).get("status"),
            "assertionRegression": (before or {}).get("status") == "PASSED" and (after or {}).get("status") != "PASSED",
            "recommendation": {
                "before": before_result.get("recommendedPartId"),
                "after": after_result.get("recommendedPartId"),
                "changed": before_result.get("recommendedPartId") != after_result.get("recommendedPartId"),
            },
            "candidateChanges": (after or {}).get("diff", {}).get("candidates", []),
        })
    before_metrics = {item["name"]: item for item in baseline.get("metrics", [])}
    after_metrics = {item["name"]: item for item in candidate.get("metrics", [])}
    metric_deltas = []
    for name in sorted(set(before_metrics) & set(after_metrics)):
        before_value, after_value = before_metrics[name].get("value"), after_metrics[name].get("value")
        delta = after_value - before_value if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)) else None
        metric_deltas.append({"name": name, "before": before_value, "after": after_value, "delta": delta})
    return {"compatible": True, "scenarioDiffs": scenario_diffs, "metricDeltas": metric_deltas}


def evaluate_regression_gates(comparison, thresholds):
    failures = []
    for metric in comparison.get("metricDeltas", []):
        threshold = thresholds.get(metric["name"])
        if threshold is None or metric["after"] is None:
            continue
        if metric["after"] < threshold:
            failures.append({
                "metric": metric["name"], "actual": metric["after"], "threshold": threshold,
                "priority": "SAFETY" if metric["name"] in SAFETY_METRICS else "QUALITY",
            })
    for scenario in comparison.get("scenarioDiffs", []):
        if scenario["assertionRegression"]:
            failures.append({"scenarioId": scenario["scenarioId"], "priority": "ASSERTION"})
    failures.sort(key=lambda item: {"SAFETY": 0, "ASSERTION": 1, "QUALITY": 2}[item["priority"]])
    return {"passed": not failures, "failures": failures,
            "blockingSafetyRegression": any(item["priority"] == "SAFETY" for item in failures)}
