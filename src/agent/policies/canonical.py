"""Canonical evidence serialization and integrity fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any


CANONICALIZATION_VERSION = "procurement-json-v1"


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("canonical numbers must be finite")
    if value == 0:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_canonical(value: Any) -> Any:
    """Convert values into the stable JSON domain used by evidence hashes."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical numbers must be finite")
        try:
            return _decimal_text(Decimal(str(value)))
        except InvalidOperation as exc:
            raise ValueError("invalid canonical number") from exc
    if isinstance(value, dict):
        return {str(key): normalize_canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_canonical(item) for item in value]
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate.get("id", "")),
        str(candidate.get("partCode", "")),
        str(candidate.get("supplierId", "")),
    )


def canonical_evidence_payload(
    *,
    policy_version: str,
    request: dict[str, Any],
    source: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build the versioned evidence payload before JSON serialization.

    Source candidates are unordered facts and are sorted by stable business
    identifiers. Result candidates retain their order because it represents the
    policy's ranking and is therefore material evidence.
    """
    source_copy = dict(source)
    source_copy["parts"] = sorted(
        [dict(item) for item in source.get("parts", [])],
        key=_candidate_sort_key,
    )
    return {
        "canonicalizationVersion": CANONICALIZATION_VERSION,
        "policyVersion": policy_version,
        "request": request,
        "source": source_copy,
        "result": result,
    }


def evidence_fingerprint(
    *,
    policy_version: str,
    request: dict[str, Any],
    source: dict[str, Any],
    result: dict[str, Any],
) -> str:
    payload = canonical_evidence_payload(
        policy_version=policy_version,
        request=request,
        source=source,
        result=result,
    )
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def decision_id_from_fingerprint(fingerprint: str) -> str:
    if len(fingerprint) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in fingerprint):
        raise ValueError("fingerprint must be a full SHA-256 hexadecimal digest")
    return "DEC-" + fingerprint[:16].upper()
