"""Explicit sample denominators; missing telemetry is never zero."""
import math
from collections import Counter


def quantile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows):
    counts = Counter(r["status"] for r in rows)
    completed = [r for r in rows if r["status"] in {"success", "failure"}]
    durations = [r["durationSeconds"] for r in completed if r.get("durationSeconds") is not None]
    usage = [r for r in completed if r.get("usageState") == "complete"]
    return {"sampleCount": len(rows), "completedCount": len(completed), "statusCounts": dict(counts),
            "sufficient": len(completed) >= 20,
            "successRate": counts["success"] / len(completed) if completed else None,
            "p50Seconds": quantile(durations, .5), "p95Seconds": quantile(durations, .95),
            "durationSamples": len(durations), "usageSamples": len(usage), "usageExcluded": len(completed) - len(usage),
            "inputTokens": sum(r["inputTokens"] for r in usage) if usage else None,
            "outputTokens": sum(r["outputTokens"] for r in usage) if usage else None,
            "estimatedCostUsd": sum(r["estimatedCostUsd"] for r in usage) if usage and all(r.get("estimatedCostUsd") is not None for r in usage) else None,
            "routes": {route: summarize_route([r for r in rows if r["route"] == route]) for route in sorted({r["route"] for r in rows})}}


def summarize_route(rows):
    return {"count": len(rows), "outcomes": dict(Counter(r["status"] for r in rows))}
