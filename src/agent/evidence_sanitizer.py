"""Allowlist and secret-boundary enforcement for persisted evidence."""

from __future__ import annotations

import json
import os
import re
from typing import Any


REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(
    r"(^|[_-])(password|passwd|pwd|token|authorization|cookie|api[_-]?key|secret|credential)s?($|[_-])",
    re.IGNORECASE,
)
FORBIDDEN_EVIDENCE_KEYS = {
    "messages", "chat", "conversation", "chain_of_thought", "reasoning", "hidden_reasoning",
}
REQUEST_FIELDS = {"partName", "quantity", "budget", "maxLeadTimeDays"}
PART_FIELDS = {
    "id", "partCode", "name", "model", "purchasePrice", "supplierId",
    "minOrderQuantity", "leadTimeDays",
}
SUPPLIER_FIELDS = {"id", "name", "creditRating", "status"}
INVENTORY_FIELDS = {"partId", "currentQuantity", "safetyStock", "warehouseLocation"}
RESULT_FIELDS = {
    "policyVersion", "scoreWeights", "request", "hardConstraints", "candidates",
    "recommendedPartId", "recommendedSupplierId", "minimumFeasibleBudget", "recommendation",
}
CANDIDATE_FIELDS = {
    "partId", "partCode", "partName", "model", "supplierId", "supplierName", "creditRating",
    "supplierStatus", "unitPrice", "quantity", "totalAmount", "minOrderQuantity", "leadTimeDays",
    "inventory", "eligible", "rejectedReasons", "scores", "totalScore", "explanation",
}


def _configured_secret_keys() -> set[str]:
    return {
        key.strip().lower()
        for key in os.getenv("EVIDENCE_SECRET_FIELDS", "").split(",")
        if key.strip()
    }


def _is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY.search(key)) or key.lower() in _configured_secret_keys()


def reject_secret_bearing(value: Any, path: str = "evidence") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if _is_secret_key(name) or name.lower() in FORBIDDEN_EVIDENCE_KEYS:
                raise ValueError(f"forbidden evidence field: {path}.{name}")
            reject_secret_bearing(item, f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_secret_bearing(item, f"{path}[{index}]")


def _allow(record: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    return {key: record.get(key) for key in fields if key in record}


def sanitize_request(request: dict[str, Any]) -> dict[str, Any]:
    reject_secret_bearing(request, "request")
    return _allow(request, REQUEST_FIELDS)


def sanitize_source(source: dict[str, Any]) -> dict[str, Any]:
    reject_secret_bearing(source, "source")
    parts = [_allow(item, PART_FIELDS) for item in source.get("parts", [])]
    suppliers = {
        str(key): _allow(item, SUPPLIER_FIELDS)
        for key, item in source.get("suppliers", {}).items()
    }
    inventories = {
        str(key): _allow(item, INVENTORY_FIELDS)
        for key, item in source.get("inventories", {}).items()
    }
    return {"parts": parts, "suppliers": suppliers, "inventories": inventories}


def sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    reject_secret_bearing(result, "result")
    clean = _allow(result, RESULT_FIELDS)
    clean["request"] = sanitize_request(result.get("request", {}))
    clean["candidates"] = [
        _allow(candidate, CANDIDATE_FIELDS) for candidate in result.get("candidates", [])
    ]
    return clean


def sanitize_event(payload: dict[str, Any]) -> dict[str, Any]:
    def visit(value: Any, key: str | None = None) -> Any:
        if key is not None and (_is_secret_key(key) or key.lower() in FORBIDDEN_EVIDENCE_KEYS):
            return REDACTED
        if isinstance(value, dict):
            return {str(item_key): visit(item, str(item_key)) for item_key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    sanitized = visit(payload)
    encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("decision event payload exceeds storage limit")
    return sanitized
