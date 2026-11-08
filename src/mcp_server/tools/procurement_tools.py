"""MCP adapter for deterministic procurement policies.

Scoring lives in :mod:`agent.policies.procurement`; this module owns ERP I/O
and persists actionable comparisons through the durable decision repository.
There is intentionally no process-local decision snapshot fallback.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastmcp import Context, FastMCP

from agent.policies.procurement import (
    POLICY_VERSION,
    SCORE_WEIGHTS,
    compare_candidates,
    parse_decimal,
)
from agent.invocation_context import verify_invocation
from agent.decision_repository import DecisionEvidence, DecisionRepository
from agent.evidence_sanitizer import sanitize_request, sanitize_result, sanitize_source
from agent.policies.canonical import (
    CANONICALIZATION_VERSION,
    decision_id_from_fingerprint,
    evidence_fingerprint,
)


DECISION_TTL_SECONDS = 15 * 60

# Backward-compatible private name used by the existing MCP adapter and tests.
_decimal = parse_decimal


def build_durable_evidence(
    *,
    identity: dict[str, Any],
    request: dict[str, Any],
    source: dict[str, Any],
    result: dict[str, Any],
    now: datetime | None = None,
) -> tuple[DecisionEvidence, dict[str, Any]]:
    """Create evidence and its externally returned result without persistence I/O."""
    clean_request = sanitize_request(request)
    clean_source = sanitize_source(source)
    clean_result = sanitize_result(result)
    fingerprint = evidence_fingerprint(
        policy_version=result["policyVersion"],
        request=clean_request,
        source=clean_source,
        result=clean_result,
    )
    decision_id = decision_id_from_fingerprint(fingerprint)
    created = now or datetime.now(timezone.utc).replace(tzinfo=None)
    evidence = DecisionEvidence(
        decision_id=decision_id,
        owner_user_id=identity["sub"],
        thread_id=identity["thread"],
        policy_version=result["policyVersion"],
        canonicalization_version=CANONICALIZATION_VERSION,
        evidence_fingerprint=fingerprint,
        request=clean_request,
        source=clean_source,
        result=clean_result,
        trace_id=identity["trace"],
        created_at=created,
        expires_at=created + timedelta(seconds=DECISION_TTL_SECONDS),
    )
    returned = {
        **result,
        "decisionId": decision_id,
        "evidenceFingerprint": fingerprint,
        "traceId": identity["trace"],
    }
    return evidence, returned


async def _api_data(http_client, path: str, *, params: dict | None = None) -> Any:
    response = await http_client.get(path, params=params)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(
            f"ERP API 失败: code={payload.get('code')}, message={payload.get('message')}"
        )
    return payload.get("data")


def register_procurement_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="procurement_compare")
    async def compare_procurement(
        part_name: str,
        quantity: int,
        budget: Optional[float] = None,
        max_lead_time_days: Optional[int] = None,
        invocation_envelope: dict | None = None,
        ctx: Context = None,
    ) -> dict:
        """使用固定版本规则进行供应商比价和推荐，并返回可审计决策ID。

        AI 只能解释本工具结果，不得自行改写分数、淘汰原因或推荐候选。若
        recommendedPartId 为空，表示没有候选通过预算、交期、MOQ 与供应商状态等
        硬约束，此时不得创建订单。
        """
        try:
            lifespan = ctx.request_context.lifespan_context
            identity = verify_invocation(
                invocation_envelope,
                audience="procurement_compare",
                replay_guard=lifespan.get("invocation_replay_guard"),
            )
            http_client = lifespan.get("http_client")
            parts = await _api_data(
                http_client,
                "/parts/search",
                params={"name": part_name.strip()},
            ) or []
            inventory_page = await _api_data(
                http_client,
                "/inventory/page",
                params={"current": 1, "size": 1000},
            ) or {}
            inventories = {
                item.get("partId"): item
                for item in inventory_page.get("records", [])
                if isinstance(item.get("partId"), int)
            }
            supplier_ids = sorted({
                part.get("supplierId")
                for part in parts
                if isinstance(part.get("supplierId"), int)
            })
            suppliers = {}
            for supplier_id in supplier_ids:
                suppliers[supplier_id] = await _api_data(
                    http_client,
                    f"/suppliers/get/{supplier_id}",
                ) or {}
            result = compare_candidates(
                part_name=part_name,
                quantity=quantity,
                budget=parse_decimal(budget),
                max_lead_time_days=max_lead_time_days,
                parts=parts,
                suppliers=suppliers,
                inventories=inventories,
            )
            source = {"parts": parts, "suppliers": suppliers, "inventories": inventories}
            evidence, returned = build_durable_evidence(
                identity=identity,
                request=result["request"],
                source=source,
                result=result,
            )
            repository = DecisionRepository(lifespan.get("decision_storage"))
            await repository.insert(evidence)
            return returned
        except Exception as exc:
            return {
                "error": str(exc),
                "policyVersion": POLICY_VERSION,
                "recommendedPartId": None,
            }


__all__ = [
    "POLICY_VERSION",
    "SCORE_WEIGHTS",
    "build_durable_evidence",
    "compare_candidates",
    "register_procurement_tools",
]
