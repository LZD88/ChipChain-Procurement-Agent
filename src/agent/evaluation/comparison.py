"""Compatible evaluation comparisons and safety-first regression gates."""

from __future__ import annotations

from collections import Counter


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
    cases = {}
    for item in report.get("cases", []):
        key = (item["scenarioId"], item.get("repetition", 1))
        if key in cases:
            raise IncompatibleRunsError(f"duplicate evaluation case: {key[0]} repetition {key[1]}")
        cases[key] = item
    return cases


def _scenario_summary(cases):
    statuses = Counter(case.get("status", "UNKNOWN") for case in cases)
    recommendations = Counter(
        case["result"]["recommendedPartId"] for case in cases
        if "recommendedPartId" in (case.get("result") or {})
    )
    count = len(cases)
    passed = statuses.get("PASSED", 0)
    return {
        "sampleCount": count,
        "passedCount": passed,
        "passRate": passed / count if count else None,
        "statusCounts": dict(sorted(statuses.items())),
        # A single observation cannot establish repeatability.
        "hasVariation": (len(statuses) > 1 or len(recommendations) > 1) if count > 1 else None,
        "recommendationCounts": [
            {"partId": part_id, "count": recommendations[part_id]}
            for part_id in sorted(recommendations, key=lambda value: (value is None, str(value)))
        ],
    }


def compare_runs(baseline, candidate):
    ensure_compatible(baseline, candidate)
    before_cases, after_cases = _cases(baseline), _cases(candidate)
    scenario_diffs = []
    case_keys = sorted(set(before_cases) | set(after_cases))
    for scenario_id, repetition in case_keys:
        before = before_cases.get((scenario_id, repetition))
        after = after_cases.get((scenario_id, repetition))
        before_result, after_result = (before or {}).get("result") or {}, (after or {}).get("result") or {}
        scenario_diffs.append({
            "scenarioId": scenario_id,
            "repetition": repetition,
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
    before_groups, after_groups = {}, {}
    for groups, cases in ((before_groups, before_cases), (after_groups, after_cases)):
        for (scenario_id, _), case in cases.items():
            groups.setdefault(scenario_id, []).append(case)
    scenario_summaries = [{
        "scenarioId": scenario_id,
        "before": _scenario_summary(before_groups.get(scenario_id, [])),
        "after": _scenario_summary(after_groups.get(scenario_id, [])),
    } for scenario_id in sorted({name for name, _ in case_keys})]
    return {"compatible": True, "scenarioDiffs": scenario_diffs,
            "scenarioSummaries": scenario_summaries, "metricDeltas": metric_deltas}


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
            failures.append({"scenarioId": scenario["scenarioId"],
                             "repetition": scenario.get("repetition", 1), "priority": "ASSERTION"})
    failures.sort(key=lambda item: {"SAFETY": 0, "ASSERTION": 1, "QUALITY": 2}[item["priority"]])
    return {"passed": not failures, "failures": failures,
            "blockingSafetyRegression": any(item["priority"] == "SAFETY" for item in failures)}
