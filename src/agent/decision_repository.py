"""Durable, owner-scoped access to procurement decision evidence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiomysql

from agent.policies.canonical import evidence_fingerprint
from agent.evidence_sanitizer import sanitize_event, sanitize_request, sanitize_result, sanitize_source


MAX_EVIDENCE_JSON_BYTES = 1_000_000
MAX_PAGE_SIZE = 100


class DecisionConflictError(RuntimeError):
    pass


class DecisionIntegrityError(RuntimeError):
    pass


class DecisionStorageError(RuntimeError):
    pass


class DecisionClaimedError(RuntimeError):
    pass


class DecisionUnavailableError(RuntimeError):
    pass


class DecisionClaimStateError(RuntimeError):
    pass


def _json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_EVIDENCE_JSON_BYTES:
        raise ValueError("decision evidence payload exceeds storage limit")
    return encoded


def _decode(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if len(value.encode("utf-8")) > MAX_EVIDENCE_JSON_BYTES:
        raise DecisionIntegrityError("stored decision evidence exceeds read limit")
    return json.loads(value)


@dataclass(frozen=True)
class DecisionEvidence:
    decision_id: str
    owner_user_id: str
    thread_id: str | None
    policy_version: str
    canonicalization_version: str
    evidence_fingerprint: str
    request: dict[str, Any]
    source: dict[str, Any]
    result: dict[str, Any]
    trace_id: str | None
    created_at: datetime
    expires_at: datetime


class DecisionRepository:
    def __init__(self, storage):
        self.storage = storage

    async def insert(self, evidence: DecisionEvidence) -> None:
        request = sanitize_request(evidence.request)
        source = sanitize_source(evidence.source)
        result = sanitize_result(evidence.result)
        sanitized_fingerprint = evidence_fingerprint(
            policy_version=evidence.policy_version,
            request=request,
            source=source,
            result=result,
        )
        if sanitized_fingerprint.lower() != evidence.evidence_fingerprint.lower():
            raise DecisionIntegrityError("decision fingerprint must cover sanitized evidence")
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute(
                    "INSERT INTO procurement_decisions "
                    "(decision_id,owner_user_id,thread_id,policy_version,canonicalization_version,"
                    "evidence_fingerprint,request_json,source_json,result_json,trace_id,created_at,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        evidence.decision_id,
                        evidence.owner_user_id,
                        evidence.thread_id,
                        evidence.policy_version,
                        evidence.canonicalization_version,
                        evidence.evidence_fingerprint,
                        _json(request),
                        _json(source),
                        _json(result),
                        evidence.trace_id,
                        evidence.created_at,
                        evidence.expires_at,
                    ),
                )
                await cur.execute(
                    "INSERT INTO procurement_decision_events "
                    "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                    "VALUES (%s,%s,'CREATED','created',%s,%s,%s)",
                    (
                        evidence.decision_id,
                        evidence.owner_user_id,
                        evidence.owner_user_id,
                        _json(sanitize_event({"traceId": evidence.trace_id})),
                        evidence.created_at,
                    ),
                )
        except aiomysql.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                raise DecisionConflictError("decision evidence already exists") from exc
            raise DecisionStorageError("decision evidence persistence failed") from exc
        except (DecisionConflictError, ValueError):
            raise
        except Exception as exc:
            raise DecisionStorageError("decision evidence persistence failed") from exc

    async def append_event(
        self,
        decision_id: str,
        owner_user_id: str,
        event_type: str,
        *,
        actor_user_id: str | None,
        payload: dict[str, Any] | None = None,
        event_key: str | None = None,
        created_at: datetime | None = None,
    ) -> bool:
        created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute(
                    "INSERT INTO procurement_decision_events "
                    "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                    "SELECT decision_id,owner_user_id,%s,%s,%s,%s,%s FROM procurement_decisions "
                    "WHERE decision_id=%s AND owner_user_id=%s",
                    (
                        event_type,
                        event_key,
                        actor_user_id,
                        _json(sanitize_event(payload or {})),
                        created_at,
                        decision_id,
                        owner_user_id,
                    ),
                )
                return cur.rowcount == 1
        except aiomysql.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062 and event_key:
                return False
            raise DecisionStorageError("decision event persistence failed") from exc
        except Exception as exc:
            raise DecisionStorageError("decision event persistence failed") from exc

    async def claim(
        self,
        decision_id: str,
        owner_user_id: str,
        *,
        claim_token: str,
        idempotency_key: str,
        lease_expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        """Atomically claim an intact, unexpired decision exactly once."""
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute(
                    "INSERT INTO procurement_decision_claims "
                    "(decision_id,owner_user_id,claim_token,idempotency_key,claim_state,"
                    "lease_expires_at,claimed_at) "
                    "SELECT decision_id,owner_user_id,%s,%s,'CLAIMED',%s,%s "
                    "FROM procurement_decisions WHERE decision_id=%s AND owner_user_id=%s "
                    "AND purged_at IS NULL AND expires_at>%s",
                    (
                        claim_token,
                        idempotency_key,
                        lease_expires_at,
                        now,
                        decision_id,
                        owner_user_id,
                        now,
                    ),
                )
                if cur.rowcount != 1:
                    raise DecisionUnavailableError("decision is missing, expired, or purged")
                await cur.execute(
                    "INSERT INTO procurement_decision_events "
                    "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                    "VALUES (%s,%s,'CLAIMED',%s,%s,%s,%s)",
                    (
                        decision_id,
                        owner_user_id,
                        f"claim:{claim_token}",
                        owner_user_id,
                        _json({"idempotencyKey": idempotency_key}),
                        now,
                    ),
                )
        except DecisionUnavailableError:
            raise
        except aiomysql.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                raise DecisionClaimedError("decision already has an active or resolved claim") from exc
            raise DecisionStorageError("decision claim failed") from exc
        except Exception as exc:
            if isinstance(exc, (DecisionClaimedError, DecisionStorageError)):
                raise
            raise DecisionStorageError("decision claim failed") from exc

    async def mark_reconciliation_required(
        self,
        decision_id: str,
        owner_user_id: str,
        *,
        claim_token: str,
        reason: str,
        order_number: str | None = None,
        trace_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE procurement_decision_claims SET claim_state='RECONCILIATION_REQUIRED',"
                "order_number=%s,trace_id=%s,lease_expires_at=NULL "
                "WHERE decision_id=%s AND owner_user_id=%s "
                "AND claim_token=%s AND claim_state='CLAIMED'",
                (order_number, trace_id, decision_id, owner_user_id, claim_token),
            )
            if cur.rowcount != 1:
                raise DecisionClaimStateError("claim is not in CLAIMED state")
            await cur.execute(
                "INSERT INTO procurement_decision_events "
                "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                "VALUES (%s,%s,'RECONCILIATION_REQUIRED',%s,%s,%s,%s)",
                (
                    decision_id,
                    owner_user_id,
                    f"reconciliation:{claim_token}",
                    owner_user_id,
                    _json({
                        "reason": reason,
                        "orderNumber": order_number,
                        "traceId": trace_id,
                    }),
                    now,
                ),
            )

    async def resolve_claim(
        self,
        decision_id: str,
        owner_user_id: str,
        *,
        claim_token: str,
        state: str,
        order_number: str | None = None,
        order_id: int | None = None,
        trace_id: str | None = None,
        erp_audit_ref: str | None = None,
        outcome: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if state not in {"SUCCEEDED", "REJECTED", "FAILED"}:
            raise ValueError("invalid terminal claim state")
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE procurement_decision_claims SET claim_state=%s,order_number=%s,order_id=%s,"
                "trace_id=%s,erp_audit_ref=%s,lease_expires_at=NULL,resolved_at=%s "
                "WHERE decision_id=%s AND owner_user_id=%s AND claim_token=%s "
                "AND claim_state IN ('CLAIMED','RECONCILIATION_REQUIRED')",
                (
                    state,
                    order_number,
                    order_id,
                    trace_id,
                    erp_audit_ref,
                    now,
                    decision_id,
                    owner_user_id,
                    claim_token,
                ),
            )
            if cur.rowcount != 1:
                raise DecisionClaimStateError("claim cannot transition to terminal state")
            await cur.execute(
                "INSERT INTO procurement_decision_events "
                "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    decision_id,
                    owner_user_id,
                    state,
                    f"resolution:{claim_token}:{state}",
                    owner_user_id,
                    _json({
                        "orderNumber": order_number,
                        "orderId": order_id,
                        "traceId": trace_id,
                        "erpAuditRef": erp_audit_ref,
                        **(outcome or {}),
                    }),
                    now,
                ),
            )

    async def get_claim(self, decision_id: str, owner_user_id: str):
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT * FROM procurement_decision_claims WHERE decision_id=%s AND owner_user_id=%s",
                (decision_id, owner_user_id),
            )
            return await cur.fetchone()

    async def list_for_owner(self, owner_user_id: str, *, page: int = 1, size: int = 20):
        if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
            raise ValueError("invalid decision page")
        offset = (page - 1) * size
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT decision_id,policy_version,evidence_fingerprint,trace_id,created_at,"
                "expires_at,purged_at FROM procurement_decisions WHERE owner_user_id=%s "
                "ORDER BY created_at DESC,decision_id DESC LIMIT %s OFFSET %s",
                (owner_user_id, size, offset),
            )
            rows = await cur.fetchall()
        return list(rows)

    async def list_for_auditor(
        self,
        *,
        owner_user_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ):
        if page < 1 or size < 1 or size > MAX_PAGE_SIZE:
            raise ValueError("invalid decision page")
        offset = (page - 1) * size
        async with self.storage.cursor() as cur:
            if owner_user_id:
                await cur.execute(
                    "SELECT decision_id,owner_user_id,policy_version,evidence_fingerprint,trace_id,"
                    "created_at,expires_at,purged_at FROM procurement_decisions "
                    "WHERE owner_user_id=%s ORDER BY created_at DESC,decision_id DESC "
                    "LIMIT %s OFFSET %s",
                    (owner_user_id, size, offset),
                )
            else:
                await cur.execute(
                    "SELECT decision_id,owner_user_id,policy_version,evidence_fingerprint,trace_id,"
                    "created_at,expires_at,purged_at FROM procurement_decisions "
                    "ORDER BY created_at DESC,decision_id DESC LIMIT %s OFFSET %s",
                    (size, offset),
                )
            rows = await cur.fetchall()
        return list(rows)

    async def get_for_owner(self, decision_id: str, owner_user_id: str):
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT * FROM procurement_decisions WHERE decision_id=%s AND owner_user_id=%s",
                (decision_id, owner_user_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        decoded = dict(row)
        decoded["request"] = _decode(decoded.pop("request_json"))
        decoded["source"] = _decode(decoded.pop("source_json"))
        decoded["result"] = _decode(decoded.pop("result_json"))
        if decoded.get("purged_at") is None:
            actual = evidence_fingerprint(
                policy_version=decoded["policy_version"],
                request=decoded["request"],
                source=decoded["source"],
                result=decoded["result"],
            )
            if actual.lower() != decoded["evidence_fingerprint"].lower():
                raise DecisionIntegrityError("decision evidence fingerprint mismatch")
        return decoded

    async def record_audit_action(
        self,
        *,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> None:
        created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO decision_audit_actions "
                "(actor_user_id,action,resource_type,resource_id,outcome,metadata_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    actor_user_id,
                    action,
                    resource_type,
                    resource_id,
                    outcome,
                    _json(sanitize_event(metadata or {})),
                    created_at,
                ),
            )

    async def record_replay(
        self,
        *,
        replay_id: str,
        decision_id: str,
        owner_user_id: str,
        policy_version: str,
        manifest: dict[str, Any],
        result: dict[str, Any],
        diff: dict[str, Any],
        created_by: str,
        created_at: datetime | None = None,
    ) -> None:
        created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
        clean_result = sanitize_result(result)
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute(
                    "INSERT INTO procurement_replays "
                    "(replay_id,decision_id,owner_user_id,policy_version,status,manifest_json,"
                    "result_json,diff_json,created_by,created_at) "
                    "SELECT %s,decision_id,owner_user_id,%s,'COMPLETED',%s,%s,%s,%s,%s "
                    "FROM procurement_decisions WHERE decision_id=%s AND owner_user_id=%s "
                    "AND purged_at IS NULL",
                    (
                        replay_id,
                        policy_version,
                        _json(sanitize_event(manifest)),
                        _json(clean_result),
                        _json(sanitize_event(diff)),
                        created_by,
                        created_at,
                        decision_id,
                        owner_user_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise DecisionUnavailableError("decision is missing or purged")
                await cur.execute(
                    "INSERT INTO procurement_decision_events "
                    "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                    "VALUES (%s,%s,'REPLAYED',%s,%s,%s,%s)",
                    (
                        decision_id,
                        owner_user_id,
                        f"replay:{replay_id}",
                        created_by,
                        _json({"replayId": replay_id, "policyVersion": policy_version}),
                        created_at,
                    ),
                )
        except DecisionUnavailableError:
            raise
        except Exception as exc:
            raise DecisionStorageError("decision replay persistence failed") from exc

    async def events_for_owner(self, decision_id: str, owner_user_id: str):
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT event_id,event_type,event_key,actor_user_id,payload_json,created_at "
                "FROM procurement_decision_events WHERE decision_id=%s AND owner_user_id=%s "
                "ORDER BY event_id",
                (decision_id, owner_user_id),
            )
            rows = await cur.fetchall()
        return [{**row, "payload": _decode(row.get("payload_json"))} for row in rows]

    async def pending_approvals_for_thread(
        self,
        thread_id: str,
        owner_user_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 20:
            raise ValueError("invalid pending approval limit")
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT req.event_id,req.decision_id,req.event_key,req.payload_json,req.created_at "
                "FROM procurement_decision_events req "
                "JOIN procurement_decisions d ON d.decision_id=req.decision_id "
                "AND d.owner_user_id=req.owner_user_id "
                "LEFT JOIN procurement_decision_events resolved "
                "ON resolved.decision_id=req.decision_id "
                "AND resolved.owner_user_id=req.owner_user_id "
                "AND resolved.event_key=CONCAT('approval-resolution:',"
                "SUBSTRING(req.event_key,CHAR_LENGTH('approval-request:')+1)) "
                "WHERE d.thread_id=%s AND req.owner_user_id=%s "
                "AND req.event_type='APPROVAL_REQUESTED' AND resolved.event_id IS NULL "
                "ORDER BY req.event_id ASC LIMIT %s",
                (thread_id, owner_user_id, limit),
            )
            rows = await cur.fetchall()
        return [{**row, "payload": _decode(row.get("payload_json"))} for row in rows]

    async def place_retention_hold(
        self,
        decision_id: str,
        owner_user_id: str,
        *,
        hold_id: str,
        reason: str,
        created_by: str,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO procurement_decision_retention_holds "
                "(hold_id,decision_id,owner_user_id,reason,created_by,created_at,released_at) "
                "SELECT %s,decision_id,owner_user_id,%s,%s,%s,NULL FROM procurement_decisions "
                "WHERE decision_id=%s AND owner_user_id=%s AND purged_at IS NULL",
                (hold_id, reason, created_by, now, decision_id, owner_user_id),
            )
            if cur.rowcount != 1:
                raise DecisionUnavailableError("decision is missing or already purged")

    async def release_retention_hold(
        self,
        hold_id: str,
        *,
        released_by: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE procurement_decision_retention_holds SET released_at=%s "
                "WHERE hold_id=%s AND released_at IS NULL",
                (now, hold_id),
            )
            changed = cur.rowcount == 1
            if changed:
                await cur.execute(
                    "INSERT INTO decision_audit_actions "
                    "(actor_user_id,action,resource_type,resource_id,outcome,metadata_json,created_at) "
                    "VALUES (%s,'RETENTION_HOLD_RELEASE','RETENTION_HOLD',%s,'SUCCESS','{}',%s)",
                    (released_by, hold_id, now),
                )
            return changed

    async def purge_expired(
        self,
        *,
        now: datetime | None = None,
        retention_days: int | None = None,
        limit: int = 100,
    ) -> list[str]:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        if retention_days is None:
            retention_days = int(os.getenv("DECISION_EVIDENCE_RETENTION_DAYS", "90"))
        if retention_days < 1 or limit < 1 or limit > 1000:
            raise ValueError("invalid decision retention configuration")
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT d.decision_id,d.owner_user_id,d.policy_version,d.evidence_fingerprint,"
                "d.created_at FROM procurement_decisions d "
                "LEFT JOIN procurement_decision_retention_holds h ON h.decision_id=d.decision_id "
                "AND h.owner_user_id=d.owner_user_id AND h.released_at IS NULL "
                "WHERE d.purged_at IS NULL AND d.created_at < DATE_SUB(%s,INTERVAL %s DAY) "
                "AND h.hold_id IS NULL ORDER BY d.created_at LIMIT %s FOR UPDATE",
                (now, retention_days, limit),
            )
            rows = await cur.fetchall()
            purged = []
            for row in rows:
                decision_id = row["decision_id"]
                owner = row["owner_user_id"]
                await cur.execute(
                    "INSERT INTO procurement_decision_tombstones "
                    "(decision_id,owner_user_id,policy_version,evidence_fingerprint,"
                    "original_created_at,purged_at,purge_reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        decision_id, owner, row["policy_version"], row["evidence_fingerprint"],
                        row["created_at"], now, "retention_expired",
                    ),
                )
                await cur.execute(
                    "UPDATE procurement_decisions SET request_json=NULL,source_json=NULL,result_json=NULL,"
                    "purged_at=%s,purge_reason='retention_expired' "
                    "WHERE decision_id=%s AND owner_user_id=%s AND purged_at IS NULL",
                    (now, decision_id, owner),
                )
                await cur.execute(
                    "INSERT INTO procurement_decision_events "
                    "(decision_id,owner_user_id,event_type,event_key,actor_user_id,payload_json,created_at) "
                    "VALUES (%s,%s,'PURGED','purged',NULL,%s,%s)",
                    (decision_id, owner, _json({"reason": "retention_expired"}), now),
                )
                purged.append(decision_id)
            return purged
