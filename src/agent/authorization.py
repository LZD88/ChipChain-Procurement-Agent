"""Narrow application capabilities for decision evidence and evaluation."""

from __future__ import annotations

import os


DECISION_AUDIT = "decision:audit"
EVALUATION_VIEW = "evaluation:view"
EVALUATION_MAINTAIN = "evaluation:maintain"
KNOWN_CAPABILITIES = frozenset({DECISION_AUDIT, EVALUATION_VIEW, EVALUATION_MAINTAIN})


def validate_capability(capability: str) -> str:
    if capability not in KNOWN_CAPABILITIES:
        raise ValueError(f"unknown application capability: {capability}")
    return capability


def bootstrap_grants_from_env() -> dict[str, set[str]]:
    """Parse explicit grants; absence means deny-by-default for every account.

    Format: ``username=capability|capability,other=evaluation:view``.
    """
    configured = os.getenv("APP_CAPABILITY_BOOTSTRAP", "").strip()
    if not configured:
        return {}
    grants: dict[str, set[str]] = {}
    for entry in configured.split(","):
        username, separator, raw_capabilities = entry.strip().partition("=")
        if not separator or not username or not raw_capabilities:
            raise ValueError("APP_CAPABILITY_BOOTSTRAP has invalid syntax")
        values = {validate_capability(value.strip()) for value in raw_capabilities.split("|")}
        grants.setdefault(username.lower(), set()).update(values)
    return grants


async def can_read_decision(storage, *, actor: str, owner: str) -> bool:
    return actor == owner or await storage.has_capability(actor, DECISION_AUDIT)


async def require_evaluation_view(storage, actor: str) -> bool:
    return await storage.has_capability(actor, EVALUATION_VIEW) or await storage.has_capability(
        actor, EVALUATION_MAINTAIN
    )


async def require_evaluation_maintain(storage, actor: str) -> bool:
    return await storage.has_capability(actor, EVALUATION_MAINTAIN)
