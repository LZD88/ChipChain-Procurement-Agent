"""Portable evaluation report exports built from one canonical payload."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from fastapi.encoders import jsonable_encoder


EXPORT_VERSION = "procurement-evaluation-report-v1"


def portable_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe self-describing report with an integrity fingerprint."""
    body = jsonable_encoder(deepcopy(report))
    body["exportVersion"] = EXPORT_VERSION
    body["incomplete"] = body.get("status") != "COMPLETED" or bool(body.get("incomplete"))
    body["completionLabel"] = "INCOMPLETE" if body["incomplete"] else "COMPLETE"
    body.pop("exportFingerprint", None)
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["exportFingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


def render_markdown(report: dict[str, Any]) -> str:
    """Render the canonical report without recomputing any metric or status."""
    body = portable_report(report)
    lines = [
        f"# Evaluation run {body.get('runId', 'unknown')}",
        "",
        f"- Status: **{body.get('status', 'UNKNOWN')}**",
        f"- Completion: **{body['completionLabel']}**",
        f"- Suite: `{body.get('suiteId', 'unknown')}@{body.get('suiteVersion', 'unknown')}`",
        f"- Execution mode: `{body.get('executionMode', 'unknown')}`",
        f"- Scenarios produced: **{len(body.get('cases', []))}**",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Numerator | Denominator | Excluded | Unit |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for metric in body.get("metrics", []):
        value = metric.get("value")
        rendered_value = json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else "N/A"
        lines.append(
            f"| {metric.get('name', '')} | {rendered_value} | "
            f"{metric.get('numerator', 'N/A')} | {metric.get('denominator', 'N/A')} | "
            f"{metric.get('excluded', 0)} | {metric.get('unit', '')} |"
        )
    lines.extend([
        "",
        "## Scenarios",
        "",
        "| Scenario | Repetition | Status | Assertions |",
        "| --- | ---: | --- | ---: |",
    ])
    for case in body.get("cases", []):
        lines.append(
            f"| {case.get('scenarioId', '')} | {case.get('repetition', 1)} | "
            f"{case.get('status', 'UNKNOWN')} | {len(case.get('assertions', []))} |"
        )
    lines.extend([
        "",
        f"Report fingerprint: `{body['exportFingerprint']}`",
        "",
    ])
    return "\n".join(lines)
