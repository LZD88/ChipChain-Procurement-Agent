"""Authenticated, no-store APIs for durable procurement decision evidence."""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from agent.authorization import DECISION_AUDIT
from agent.decision_replay import replay_diff, replay_policy
from agent.decision_repository import DecisionIntegrityError, DecisionRepository
from agent.mysql_storage import mysql_storage
from agent.policies.registry import (
    PolicyNotFoundError,
    PolicyUnavailableError,
    get_replay_policy,
    list_policies,
)
from api_view.auth import require_same_origin_request, require_user


router = APIRouter(
    prefix="/decisions",
    dependencies=[Depends(require_same_origin_request)],
)


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policyVersion: str


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="决策证据不存在",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _summary(row: dict) -> dict:
    return {
        "decisionId": row.get("decision_id"),
        "ownerUserId": row.get("owner_user_id"),
        "policyVersion": row.get("policy_version"),
        "evidenceFingerprint": row.get("evidence_fingerprint"),
        "traceId": row.get("trace_id"),
        "createdAt": row.get("created_at"),
        "expiresAt": row.get("expires_at"),
        "purgedAt": row.get("purged_at"),
    }


def _detail(row: dict) -> dict:
    return {
        **_summary(row),
        "threadId": row.get("thread_id"),
        "canonicalizationVersion": row.get("canonicalization_version"),
        "request": row.get("request"),
        "source": row.get("source"),
        "result": row.get("result"),
        "purgeReason": row.get("purge_reason"),
    }


def _event(row: dict) -> dict:
    return {
        "eventId": row.get("event_id"),
        "eventType": row.get("event_type"),
        "eventKey": row.get("event_key"),
        "actorUserId": row.get("actor_user_id"),
        "payload": row.get("payload") or {},
        "createdAt": row.get("created_at"),
    }


async def _load_visible(
    repository: DecisionRepository,
    *,
    actor: str,
    decision_id: str,
    owner: str | None,
) -> tuple[dict, str]:
    can_audit = await mysql_storage.has_capability(actor, DECISION_AUDIT)
    if owner and owner != actor and not can_audit:
        await repository.record_audit_action(
            actor_user_id=actor,
            action="DECISION_LOOKUP",
            resource_type="PROCUREMENT_DECISION",
            resource_id=decision_id,
            outcome="DENIED",
            metadata={},
        )
        raise _not_found()
    target_owner = owner or actor
    try:
        evidence = await repository.get_for_owner(decision_id, target_owner)
    except DecisionIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="决策证据完整性校验失败",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ) from exc
    if evidence is None:
        await repository.record_audit_action(
            actor_user_id=actor,
            action="DECISION_LOOKUP",
            resource_type="PROCUREMENT_DECISION",
            resource_id=decision_id,
            outcome="NOT_FOUND",
            metadata={},
        )
        raise _not_found()
    return evidence, target_owner


@router.get("")
async def list_decisions(
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    owner: str | None = Query(None, min_length=2, max_length=32),
    actor: str = Depends(require_user),
):
    repository = DecisionRepository(mysql_storage)
    can_audit = await mysql_storage.has_capability(actor, DECISION_AUDIT)
    if owner and owner != actor and not can_audit:
        await repository.record_audit_action(
            actor_user_id=actor,
            action="DECISION_LIST",
            resource_type="PROCUREMENT_DECISION",
            resource_id=None,
            outcome="DENIED",
            metadata={},
        )
        raise _not_found()
    if can_audit:
        rows = await repository.list_for_auditor(owner_user_id=owner, page=page, size=size)
    else:
        rows = await repository.list_for_owner(actor, page=page, size=size)
    _no_store(response)
    return {"items": [_summary(row) for row in rows], "page": page, "size": size}


@router.get("/policies")
async def replay_policies(response: Response, _actor: str = Depends(require_user)):
    _no_store(response)
    return {
        "items": [
            {
                "version": policy.version,
                "replaySupported": policy.replay_supported,
                "description": policy.description,
            }
            for policy in list_policies()
            if policy.replay_supported
        ],
    }


@router.post("/{decision_id}/replay")
async def replay_decision(
    decision_id: str,
    body: ReplayRequest,
    response: Response,
    owner: str | None = Query(None, min_length=2, max_length=32),
    actor: str = Depends(require_user),
):
    repository = DecisionRepository(mysql_storage)
    evidence, target_owner = await _load_visible(
        repository, actor=actor, decision_id=decision_id, owner=owner,
    )
    if evidence.get("purged_at") is not None:
        raise HTTPException(
            status_code=409,
            detail="决策明细已清理，无法回放",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    try:
        policy = get_replay_policy(body.policyVersion)
    except (PolicyNotFoundError, PolicyUnavailableError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ) from exc
    simulated = replay_policy(policy.evaluator, evidence)
    diff = replay_diff(evidence["result"], simulated)
    replay_id = str(uuid.uuid4())
    manifest = {
        "replayId": replay_id,
        "decisionId": decision_id,
        "originalPolicyVersion": evidence["policy_version"],
        "selectedPolicyVersion": body.policyVersion,
        "evidenceFingerprint": evidence["evidence_fingerprint"],
        "simulation": True,
    }
    await repository.record_replay(
        replay_id=replay_id,
        decision_id=decision_id,
        owner_user_id=target_owner,
        policy_version=body.policyVersion,
        manifest=manifest,
        result=simulated,
        diff=diff,
        created_by=actor,
    )
    _no_store(response)
    return {"manifest": manifest, "result": simulated, "diff": diff}


@router.get("/{decision_id}")
async def decision_detail(
    decision_id: str,
    response: Response,
    owner: str | None = Query(None, min_length=2, max_length=32),
    actor: str = Depends(require_user),
):
    repository = DecisionRepository(mysql_storage)
    evidence, target_owner = await _load_visible(
        repository, actor=actor, decision_id=decision_id, owner=owner,
    )
    events = await repository.events_for_owner(decision_id, target_owner)
    _no_store(response)
    return {"decision": _detail(evidence), "lifecycle": [_event(row) for row in events]}


@router.get("/{decision_id}/lifecycle")
async def decision_lifecycle(
    decision_id: str,
    response: Response,
    owner: str | None = Query(None, min_length=2, max_length=32),
    actor: str = Depends(require_user),
):
    repository = DecisionRepository(mysql_storage)
    _evidence, target_owner = await _load_visible(
        repository, actor=actor, decision_id=decision_id, owner=owner,
    )
    events = await repository.events_for_owner(decision_id, target_owner)
    _no_store(response)
    return {"decisionId": decision_id, "lifecycle": [_event(row) for row in events]}


@router.get("/{decision_id}/export")
async def export_decision(
    decision_id: str,
    owner: str | None = Query(None, min_length=2, max_length=32),
    actor: str = Depends(require_user),
):
    repository = DecisionRepository(mysql_storage)
    evidence, target_owner = await _load_visible(
        repository, actor=actor, decision_id=decision_id, owner=owner,
    )
    events = await repository.events_for_owner(decision_id, target_owner)
    body = jsonable_encoder({
        "exportVersion": "procurement-decision-export-v1",
        "decision": _detail(evidence),
        "lifecycle": [_event(row) for row in events],
    })
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["exportFingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    export_id = uuid.uuid4().hex
    await repository.append_event(
        decision_id,
        target_owner,
        "EXPORTED",
        actor_user_id=actor,
        event_key=f"export:{export_id}",
        payload={"exportId": export_id, "exportFingerprint": body["exportFingerprint"]},
    )
    await repository.record_audit_action(
        actor_user_id=actor,
        action="DECISION_EXPORT",
        resource_type="PROCUREMENT_DECISION",
        resource_id=decision_id,
        outcome="SUCCESS",
        metadata={"exportId": export_id, "ownerUserId": target_owner},
    )
    return JSONResponse(
        content=body,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Disposition": f'attachment; filename="decision-{decision_id}.json"',
        },
    )
