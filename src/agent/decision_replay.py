"""Pure, non-writing replay of stored procurement source facts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agent.policies.procurement import parse_decimal


def replay_policy(evaluator, evidence: dict[str, Any]) -> dict[str, Any]:
    request = evidence["request"]
    source = evidence["source"]
    return evaluator(
        part_name=request["partName"],
        quantity=request["quantity"],
        budget=parse_decimal(request.get("budget")),
        max_lead_time_days=request.get("maxLeadTimeDays"),
        parts=source.get("parts", []),
        suppliers={int(key): value for key, value in source.get("suppliers", {}).items()},
        inventories={int(key): value for key, value in source.get("inventories", {}).items()},
    )


def _score_delta(before: Any, after: Any) -> float | None:
    if before is None or after is None:
        return None
    return float(Decimal(str(after)) - Decimal(str(before)))


def replay_diff(original: dict[str, Any], simulated: dict[str, Any]) -> dict[str, Any]:
    original_candidates = original.get("candidates", [])
    simulated_candidates = simulated.get("candidates", [])
    before = {item.get("partId"): (index + 1, item) for index, item in enumerate(original_candidates)}
    after = {item.get("partId"): (index + 1, item) for index, item in enumerate(simulated_candidates)}
    candidates = []
    for part_id in sorted(set(before) | set(after), key=lambda value: (value is None, value or 0)):
        before_rank, before_item = before.get(part_id, (None, {}))
        after_rank, after_item = after.get(part_id, (None, {}))
        before_reasons = before_item.get("rejectedReasons", [])
        after_reasons = after_item.get("rejectedReasons", [])
        score_names = sorted(set(before_item.get("scores", {})) | set(after_item.get("scores", {})))
        score_diff = {
            name: {
                "before": before_item.get("scores", {}).get(name),
                "after": after_item.get("scores", {}).get(name),
                "delta": _score_delta(
                    before_item.get("scores", {}).get(name),
                    after_item.get("scores", {}).get(name),
                ),
            }
            for name in score_names
        }
        candidates.append({
            "partId": part_id,
            "eligibility": {
                "before": before_item.get("eligible"),
                "after": after_item.get("eligible"),
                "changed": before_item.get("eligible") != after_item.get("eligible"),
            },
            "rank": {"before": before_rank, "after": after_rank, "changed": before_rank != after_rank},
            "rejectionReasons": {
                "before": before_reasons,
                "after": after_reasons,
                "added": [item for item in after_reasons if item not in before_reasons],
                "removed": [item for item in before_reasons if item not in after_reasons],
            },
            "scores": score_diff,
            "totalScore": {
                "before": before_item.get("totalScore"),
                "after": after_item.get("totalScore"),
                "delta": _score_delta(before_item.get("totalScore"), after_item.get("totalScore")),
            },
        })
    return {
        "recommendation": {
            "beforePartId": original.get("recommendedPartId"),
            "afterPartId": simulated.get("recommendedPartId"),
            "changed": original.get("recommendedPartId") != simulated.get("recommendedPartId"),
        },
        "candidates": candidates,
    }
