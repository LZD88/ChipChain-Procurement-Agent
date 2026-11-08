import random
import hashlib
import json
import secrets
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastmcp import FastMCP, Context
from agent.decision_repository import (
    DecisionClaimedError,
    DecisionIntegrityError,
    DecisionRepository,
    DecisionUnavailableError,
)
from agent.invocation_context import verify_invocation

from typing import Optional, List

GROUP_NAME = "order"


class OrderDecisionValidationError(ValueError):
    """The requested order does not exactly match durable decision evidence."""


@dataclass(frozen=True)
class AuthorizedOrderClaim:
    claim_token: str
    idempotency_key: str
    evidence: dict


def _positive_decimal(value) -> Optional[Decimal]:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed > 0 and parsed.as_tuple().exponent >= -2 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _generate_order_number() -> str:
    """生成订单编号：PO + 年月日(8位) + 3位随机数字"""
    today = datetime.now().strftime("%Y%m%d")
    suffix = str(random.randint(0, 999)).zfill(3)
    return f"PO{today}{suffix}"


def _idempotency_key(decision_id: str, order_detail: List[dict], expected_delivery_date: Optional[str]) -> str:
    canonical = json.dumps({
        "decisionId": decision_id,
        "orderDetail": order_detail,
        "expectedDeliveryDate": expected_delivery_date,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "agent-order:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deterministic_order_number(decision_id: str) -> str:
    digest = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"POA{datetime.now().strftime('%Y%m%d')}{digest}"


def _erp_audit_request_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32].upper()
    return f"ERP-AUD-{digest}"


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_order_evidence(
    evidence: dict | None,
    *,
    policy_version: str,
    order_detail: List[dict],
    now: datetime | None = None,
) -> dict:
    """Validate expiry and exact order fields against verified stored evidence."""
    if evidence is None or evidence.get("purged_at") is not None:
        raise OrderDecisionValidationError("采购决策不存在或不可用，请重新执行 procurement_compare")

    current = _utc_naive(now or datetime.now(timezone.utc))
    expires_at = evidence.get("expires_at")
    if not isinstance(expires_at, datetime) or _utc_naive(expires_at) <= current:
        raise OrderDecisionValidationError("采购决策已过期，请重新比价")

    result = evidence.get("result") or {}
    if (evidence.get("policy_version") != policy_version
            or result.get("policyVersion") != policy_version):
        raise OrderDecisionValidationError("采购决策规则版本不匹配")
    if len(order_detail) != 1:
        raise OrderDecisionValidationError("订单明细与采购决策证据不一致")

    recommended_id = result.get("recommendedPartId")
    recommended = next(
        (
            item for item in result.get("candidates", [])
            if item.get("partId") == recommended_id and item.get("eligible") is True
        ),
        None,
    )
    if recommended_id is None or recommended is None:
        raise OrderDecisionValidationError("采购决策没有可下单的合格推荐")

    detail = order_detail[0]
    requested_price = _positive_decimal(detail.get("unitPrice"))
    expected_price = _positive_decimal(recommended.get("unitPrice"))
    if (detail.get("partId") != recommended_id
            or detail.get("quantity") != recommended.get("quantity")
            or requested_price is None
            or expected_price is None
            or requested_price != expected_price):
        raise OrderDecisionValidationError("订单物料、数量或价格与采购决策证据不一致")
    return recommended


async def authorize_order_from_evidence(
    repository: DecisionRepository,
    *,
    owner_user_id: str,
    decision_id: str,
    policy_version: str,
    order_detail: List[dict],
    expected_delivery_date: Optional[str] = None,
    now: datetime | None = None,
    claim_token: str | None = None,
) -> AuthorizedOrderClaim:
    """Verify owner-scoped evidence, then atomically claim it before ERP dispatch."""
    current = _utc_naive(now or datetime.now(timezone.utc))
    evidence = await repository.get_for_owner(decision_id, owner_user_id)
    validate_order_evidence(
        evidence,
        policy_version=policy_version,
        order_detail=order_detail,
        now=current,
    )
    token = claim_token or secrets.token_hex(32)
    idempotency_key = _idempotency_key(decision_id, order_detail, expected_delivery_date)
    await repository.claim(
        decision_id,
        owner_user_id,
        claim_token=token,
        idempotency_key=idempotency_key,
        lease_expires_at=current + timedelta(seconds=30),
        now=current,
    )
    return AuthorizedOrderClaim(
        claim_token=token,
        idempotency_key=idempotency_key,
        evidence=evidence,
    )


def _response_trace_id(response, fallback: str) -> str:
    return response.headers.get("X-Trace-Id") or fallback


def _erp_audit_ref(response, data: dict) -> str | None:
    return (
        response.headers.get("X-ERP-Audit-Ref")
        or data.get("erpAuditRef")
        or data.get("auditRef")
    )


async def _reconciliation_result(
    repository: DecisionRepository,
    *,
    owner_user_id: str,
    decision_id: str,
    authorization: AuthorizedOrderClaim,
    order_number: str,
    trace_id: str,
    reason: str,
) -> dict:
    await repository.mark_reconciliation_required(
        decision_id,
        owner_user_id,
        claim_token=authorization.claim_token,
        reason=reason,
        order_number=order_number,
        trace_id=trace_id,
    )
    return {
        "error": reason,
        "reconciliationRequired": True,
        "orderNumber": order_number,
        "traceId": trace_id,
    }


async def dispatch_claimed_order(
    repository: DecisionRepository,
    http_client,
    *,
    owner_user_id: str,
    decision_id: str,
    authorization: AuthorizedOrderClaim,
    request_data: dict,
    trace_id: str,
) -> dict:
    """Dispatch a claimed order and persist an unambiguous lifecycle outcome."""
    order_number = request_data["orderNumber"]
    audit_request_id = _erp_audit_request_id(authorization.idempotency_key)
    await repository.append_event(
        decision_id,
        owner_user_id,
        "ERP_DISPATCHED",
        actor_user_id=owner_user_id,
        event_key=f"dispatch:{authorization.claim_token}",
        payload={
            "orderNumber": order_number,
            "traceId": trace_id,
            "idempotencyKey": authorization.idempotency_key,
            "erpAuditRequestId": audit_request_id,
            "orderRequest": request_data,
        },
    )
    try:
        response = await http_client.post(
            "/orders/create",
            json=request_data,
            headers={
                "Idempotency-Key": authorization.idempotency_key,
                "X-Trace-Id": trace_id,
                "X-Request-Id": audit_request_id,
            },
        )
    except Exception as exc:
        return await _reconciliation_result(
            repository,
            owner_user_id=owner_user_id,
            decision_id=decision_id,
            authorization=authorization,
            order_number=order_number,
            trace_id=trace_id,
            reason=f"ERP 请求发送后结果不明确: {exc}",
        )

    resolved_trace = _response_trace_id(response, trace_id)
    if response.status_code >= 500:
        return await _reconciliation_result(
            repository,
            owner_user_id=owner_user_id,
            decision_id=decision_id,
            authorization=authorization,
            order_number=order_number,
            trace_id=resolved_trace,
            reason=f"ERP 请求发送后返回 {response.status_code}，结果需要核对",
        )

    try:
        payload = response.json()
    except Exception as exc:
        return await _reconciliation_result(
            repository,
            owner_user_id=owner_user_id,
            decision_id=decision_id,
            authorization=authorization,
            order_number=order_number,
            trace_id=resolved_trace,
            reason=f"ERP 返回无法确认的响应: {exc}",
        )

    api_code = payload.get("code")
    if response.status_code >= 400 or api_code != 200:
        message = payload.get("message") or f"HTTP {response.status_code}"
        await repository.resolve_claim(
            decision_id,
            owner_user_id,
            claim_token=authorization.claim_token,
            state="REJECTED",
            order_number=order_number,
            trace_id=resolved_trace,
            outcome={
                "idempotencyOutcome": "REJECTED",
                "httpStatus": response.status_code,
                "apiCode": api_code,
                "message": str(message),
            },
        )
        return {"error": f"API error: code={api_code}, message={message}"}

    data = payload.get("data")
    if (not isinstance(data, dict)
            or not isinstance(data.get("id"), int)
            or data.get("orderNumber") != order_number):
        return await _reconciliation_result(
            repository,
            owner_user_id=owner_user_id,
            decision_id=decision_id,
            authorization=authorization,
            order_number=order_number,
            trace_id=resolved_trace,
            reason="ERP 返回成功但缺少匹配的订单标识，结果需要核对",
        )

    replayed = response.headers.get("Idempotency-Replayed", "false").lower() == "true"
    audit_ref = _erp_audit_ref(response, data)
    await repository.resolve_claim(
        decision_id,
        owner_user_id,
        claim_token=authorization.claim_token,
        state="SUCCEEDED",
        order_number=order_number,
        order_id=data["id"],
        trace_id=resolved_trace,
        erp_audit_ref=audit_ref,
        outcome={
            "idempotencyOutcome": "REPLAYED" if replayed else "CREATED",
            "erpAuditRefStatus": "AVAILABLE" if audit_ref else "PENDING",
        },
    )
    return data


async def reconcile_ambiguous_order(
    repository: DecisionRepository,
    http_client,
    *,
    owner_user_id: str,
    decision_id: str,
    max_attempts: int = 3,
    retry_if_absent: bool = False,
    retry_delay_seconds: float = 0.25,
) -> dict:
    """Bounded reconciliation using ERP idempotency state and the exact stored request."""
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("reconciliation attempts must be between 1 and 5")
    claim = await repository.get_claim(decision_id, owner_user_id)
    if not claim or claim.get("claim_state") != "RECONCILIATION_REQUIRED":
        raise OrderDecisionValidationError("采购决策没有待协调的订单结果")

    events = await repository.events_for_owner(decision_id, owner_user_id)
    dispatch_event = next(
        (
            event for event in reversed(events)
            if event.get("event_type") == "ERP_DISPATCHED"
            and (event.get("payload") or {}).get("idempotencyKey") == claim.get("idempotency_key")
        ),
        None,
    )
    dispatch_payload = (dispatch_event or {}).get("payload") or {}
    order_request = dispatch_payload.get("orderRequest")
    order_number = claim.get("order_number") or dispatch_payload.get("orderNumber")
    trace_id = claim.get("trace_id") or dispatch_payload.get("traceId")
    if not isinstance(order_request, dict) or not order_number or not trace_id:
        raise OrderDecisionValidationError("待协调订单缺少原始派发证据")

    idempotency_key = claim["idempotency_key"]
    audit_request_id = _erp_audit_request_id(idempotency_key)
    last_error = "ERP 幂等状态仍未确定"
    for attempt in range(1, max_attempts + 1):
        await repository.append_event(
            decision_id,
            owner_user_id,
            "RECONCILIATION_CHECK",
            actor_user_id=owner_user_id,
            event_key=f"reconciliation-check:{claim['claim_token']}:{attempt}",
            payload={"attempt": attempt, "maxAttempts": max_attempts},
        )
        try:
            response = await http_client.get(f"/orders/idempotency/{idempotency_key}")
            payload = response.json()
        except Exception as exc:
            last_error = f"ERP 幂等查询失败: {exc}"
            if attempt < max_attempts and retry_delay_seconds:
                await asyncio.sleep(retry_delay_seconds)
            continue

        if response.status_code >= 500 or payload.get("code") != 200:
            last_error = (
                f"ERP 幂等查询未成功: HTTP {response.status_code}, "
                f"code={payload.get('code')}"
            )
            if attempt < max_attempts and retry_delay_seconds:
                await asyncio.sleep(retry_delay_seconds)
            continue

        lookup = payload.get("data") or {}
        status = lookup.get("status")
        if status == "COMPLETED":
            order = lookup.get("order") or {}
            if not isinstance(order.get("id"), int) or order.get("orderNumber") != order_number:
                last_error = "ERP 幂等结果与派发订单标识不一致"
                break
            resolved_trace = _response_trace_id(response, trace_id)
            await repository.resolve_claim(
                decision_id,
                owner_user_id,
                claim_token=claim["claim_token"],
                state="SUCCEEDED",
                order_number=order_number,
                order_id=order["id"],
                trace_id=resolved_trace,
                erp_audit_ref=audit_request_id,
                outcome={
                    "idempotencyOutcome": "RECONCILED_EXISTING",
                    "erpAuditRefStatus": "AVAILABLE",
                    "reconciliationAttempts": attempt,
                },
            )
            return {**order, "reconciled": True, "createdDuringReconciliation": False}

        if status == "PROCESSING":
            last_error = "ERP 幂等请求仍在处理中"
            if attempt < max_attempts and retry_delay_seconds:
                await asyncio.sleep(retry_delay_seconds)
            continue

        if status != "ABSENT":
            last_error = f"ERP 返回未知幂等状态: {status}"
            break
        if not retry_if_absent:
            return {
                "error": "ERP 确认订单不存在，但当前策略不允许自动重试",
                "reconciliationRequired": True,
                "retryAllowed": False,
            }

        await repository.append_event(
            decision_id,
            owner_user_id,
            "RECONCILIATION_RETRY_DISPATCHED",
            actor_user_id=owner_user_id,
            event_key=f"reconciliation-retry:{claim['claim_token']}",
            payload={
                "orderNumber": order_number,
                "traceId": trace_id,
                "idempotencyKey": idempotency_key,
            },
        )
        try:
            retry_response = await http_client.post(
                "/orders/create",
                json=order_request,
                headers={
                    "Idempotency-Key": idempotency_key,
                    "X-Trace-Id": trace_id,
                    "X-Request-Id": audit_request_id,
                },
            )
            retry_payload = retry_response.json()
        except Exception as exc:
            return {
                "error": f"协调重试后结果仍不明确: {exc}",
                "reconciliationRequired": True,
                "retryAllowed": True,
            }

        resolved_trace = _response_trace_id(retry_response, trace_id)
        if retry_response.status_code >= 500:
            return {
                "error": f"协调重试返回 {retry_response.status_code}，结果仍需核对",
                "reconciliationRequired": True,
                "retryAllowed": True,
            }
        if retry_response.status_code >= 400 or retry_payload.get("code") != 200:
            message = retry_payload.get("message") or f"HTTP {retry_response.status_code}"
            await repository.resolve_claim(
                decision_id,
                owner_user_id,
                claim_token=claim["claim_token"],
                state="REJECTED",
                order_number=order_number,
                trace_id=resolved_trace,
                outcome={
                    "idempotencyOutcome": "RECONCILIATION_RETRY_REJECTED",
                    "message": str(message),
                },
            )
            return {"error": f"协调重试被 ERP 拒绝: {message}", "reconciled": True}

        order = retry_payload.get("data") or {}
        if not isinstance(order.get("id"), int) or order.get("orderNumber") != order_number:
            return {
                "error": "协调重试返回成功但订单标识不明确",
                "reconciliationRequired": True,
                "retryAllowed": True,
            }
        audit_ref = _erp_audit_ref(retry_response, order) or audit_request_id
        await repository.resolve_claim(
            decision_id,
            owner_user_id,
            claim_token=claim["claim_token"],
            state="SUCCEEDED",
            order_number=order_number,
            order_id=order["id"],
            trace_id=resolved_trace,
            erp_audit_ref=audit_ref,
            outcome={
                "idempotencyOutcome": "RECONCILIATION_RETRY_CREATED",
                "erpAuditRefStatus": "AVAILABLE",
                "reconciliationAttempts": attempt,
            },
        )
        return {**order, "reconciled": True, "createdDuringReconciliation": True}

    return {
        "error": last_error,
        "reconciliationRequired": True,
        "attempts": max_attempts,
    }


def _prepare_create_request(data: dict) -> dict:
    """仅为创建请求生成订单号；时间、金额、状态和创建人均由 ERP 服务端生成。"""
    # 自动生成 orderNumber
    if not data.get("orderNumber"):
        data["orderNumber"] = _generate_order_number()

    return _serialize_request(data)


def _serialize_request(data: dict) -> dict:
    """递归处理：Decimal → float，date/datetime → ISO 字符串"""
    for key, value in data.items():
        if isinstance(value, Decimal):
            data[key] = float(value)
        elif hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _serialize_request(item)
        elif isinstance(value, dict):
            _serialize_request(value)
    return data


def register_order_tools(mcp: FastMCP):
    """注册订单分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_create")
    async def create_order(
        order_detail: List[dict],
        decision_id: str,
        order_number: Optional[str] = None,
        expected_delivery_date: Optional[str] = None,
        policy_version: str = "supplier-score-v1",
        remark: Optional[str] = None,
        invocation_envelope: dict | None = None,
        ctx: Context = None,
    ) -> dict:
        """
        创建采购订单（POST /orders/create）。

        orderNumber 不传则自动生成（规则：PO+年月日+3位随机数字）。
        orderDetail 必填，至少包含一个明细项；每项需提供 partId、quantity、unitPrice。
        decisionId 必须来自 procurement_compare 的原始结果，用于审计回放。

        Args:
            order_detail: 订单明细列表，每项需提供 partId, quantity, unitPrice，可选 remark
            decision_id: procurement_compare 返回的决策 ID
            order_number: 订单编号（唯一标识），不传则自动生成
            expected_delivery_date: 预计交货日期，格式 yyyy-MM-dd
            policy_version: 比价规则版本，必须与决策结果一致
            remark: 备注
        """
        lifespan = ctx.request_context.lifespan_context
        try:
            identity = verify_invocation(
                invocation_envelope,
                audience="order_create",
                replay_guard=lifespan.get("invocation_replay_guard"),
            )
        except Exception as exc:
            return {"error": str(exc)}
        http_client = lifespan.get("http_client")

        if not decision_id or not decision_id.startswith("DEC-"):
            return {"error": "创建订单必须携带 procurement_compare 返回的有效 decision_id"}
        if policy_version != "supplier-score-v1":
            return {"error": "不支持的采购决策规则版本"}
        if not order_detail:
            return {"error": "订单至少需要一项物料明细"}
        allowed_detail_fields = {"partId", "quantity", "unitPrice", "remark"}
        for detail in order_detail:
            if not isinstance(detail, dict) or set(detail) - allowed_detail_fields:
                return {"error": "订单明细只允许 partId、quantity、unitPrice、remark 字段"}
            if not isinstance(detail.get("partId"), int) or not isinstance(detail.get("quantity"), int) \
                    or detail["quantity"] <= 0 or _positive_decimal(detail.get("unitPrice")) is None:
                return {"error": "订单明细必须包含有效的 partId、正整数 quantity 和正数 unitPrice"}
        deterministic_order_number = _deterministic_order_number(decision_id)
        if order_number is not None and order_number != deterministic_order_number:
            return {"error": "订单编号由决策确定，不允许改写"}
        repository = DecisionRepository(lifespan.get("decision_storage"))
        try:
            authorization = await authorize_order_from_evidence(
                repository,
                owner_user_id=identity["sub"],
                decision_id=decision_id,
                policy_version=policy_version,
                order_detail=order_detail,
                expected_delivery_date=expected_delivery_date,
            )
        except DecisionClaimedError:
            return {"error": "采购决策已被认领或用于创建订单，不允许重复使用"}
        except DecisionUnavailableError:
            return {"error": "采购决策不存在、已过期或不可用，请重新比价"}
        except (DecisionIntegrityError, OrderDecisionValidationError) as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": f"采购决策校验或认领失败: {exc}"}

        # 构建请求体（映射到 API 字段名）
        request_data = {}
        request_data["orderNumber"] = deterministic_order_number
        if expected_delivery_date is not None:
            request_data["expectedDeliveryDate"] = expected_delivery_date
        audit_remark = f"decisionId={decision_id}; policyVersion={policy_version}"
        request_data["remark"] = f"{audit_remark}; {remark}" if remark else audit_remark
        if order_detail is not None:
            request_data["orderDetail"] = order_detail

        request_data = _prepare_create_request(request_data)

        try:
            dispatch_result = await dispatch_claimed_order(
                repository,
                http_client,
                owner_user_id=identity["sub"],
                decision_id=decision_id,
                authorization=authorization,
                request_data=request_data,
                trace_id=identity["trace"],
            )
            if not dispatch_result.get("reconciliationRequired"):
                return dispatch_result
            return await reconcile_ambiguous_order(
                repository,
                http_client,
                owner_user_id=identity["sub"],
                decision_id=decision_id,
                max_attempts=int(os.getenv("ORDER_RECONCILIATION_MAX_ATTEMPTS", "3")),
                retry_if_absent=(
                    os.getenv("ORDER_RECONCILIATION_RETRY_IF_ABSENT", "false").lower()
                    == "true"
                ),
            )
        except Exception as exc:
            return {"error": f"订单结果持久化失败: {exc}"}

    @mcp.tool(name=f"{GROUP_NAME}_update")
    async def update_order(
        order_id: int,
        order_detail: Optional[List[dict]] = None,
        expected_delivery_date: Optional[str] = None,
        remark: Optional[str] = None,
        ctx: Context = None,
    ) -> dict:
        """
        更新采购订单（PUT /orders/update/{id}）。

        orderDetail 为可选，传入则替换原有明细。
        Agent 只能修改待审核订单的预计交期、备注和物料明细；状态流转由采购人员完成。

        Args:
            order_id: 订单 ID（路径参数，必填）
            order_detail: 订单明细列表（可选），每项需提供 partId, quantity, unitPrice
            expected_delivery_date: 预计交货日期
            remark: 备注
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        if order_detail is not None:
            allowed_detail_fields = {"partId", "quantity", "unitPrice", "remark"}
            for detail in order_detail:
                if not isinstance(detail, dict) or set(detail) - allowed_detail_fields:
                    return {"error": "订单明细只允许 partId、quantity、unitPrice、remark 字段"}
                if not isinstance(detail.get("partId"), int) or not isinstance(detail.get("quantity"), int) \
                        or detail["quantity"] <= 0 or _positive_decimal(detail.get("unitPrice")) is None:
                    return {"error": "订单明细必须包含有效的 partId、正整数 quantity 和正数 unitPrice"}

        # 构建请求体（映射到 API 字段名，只包含非 None 字段）
        request_data = {}
        if expected_delivery_date is not None:
            request_data["expectedDeliveryDate"] = expected_delivery_date
        if remark is not None:
            request_data["remark"] = remark
        if order_detail is not None:
            request_data["orderDetail"] = order_detail

        request_data = _serialize_request(request_data)

        try:
            response = await http_client.put(
                f"/orders/update/{order_id}",
                json=request_data
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return {
                    "error": f"API error: code={result.get('code')}, message={result.get('message')}"
                }

            return result.get("data", {})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool(name=f"{GROUP_NAME}_search_details")
    async def search_order_details(
        part_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        ctx: Context = None,
    ) -> list:
        """
        搜索采购订单明细。

        支持按零部件名称、日期范围筛选，所有参数均为可选。
        返回的每条明细包含零部件详情（partDetail）及供应商信息（supplier）。

        Args:
            part_name: 零部件名称（模糊查询），可选
            start_date: 开始日期（yyyy-MM-dd 格式），可选
            end_date: 结束日期（yyyy-MM-dd 格式），可选
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        # 构建请求参数（过滤 None 值，映射到 API 字段名）
        request_params = {}
        if part_name is not None:
            request_params["partName"] = part_name
        if start_date is not None:
            request_params["startDate"] = start_date
        if end_date is not None:
            request_params["endDate"] = end_date

        try:
            response = await http_client.get(
                "/orders/search-details",
                params=request_params
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
