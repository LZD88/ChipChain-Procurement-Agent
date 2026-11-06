"""确定性的供应商比价与推荐规则，不把业务评分交给大模型自由发挥。"""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from threading import Lock
from typing import Any, Optional

from fastmcp import Context, FastMCP


POLICY_VERSION = "supplier-score-v1"
SCORE_WEIGHTS = {"price": 35, "leadTime": 25, "credit": 25, "inventoryMoq": 15}
CREDIT_SCORES = {"A+": Decimal("25"), "A": Decimal("25"), "B": Decimal("20"),
                 "C": Decimal("12"), "D": Decimal("5")}
TWO_PLACES = Decimal("0.01")
DECISION_TTL_SECONDS = 15 * 60
_DECISIONS: dict[str, dict] = {}
_DECISION_LOCK = Lock()


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def remember_decision(result: dict) -> None:
    """保存短时效决策快照，供人工批准后的写工具校验。"""
    recommended_id = result.get("recommendedPartId")
    recommended = next(
        (item for item in result.get("candidates", []) if item.get("partId") == recommended_id), None,
    )
    if recommended is None:
        return
    with _DECISION_LOCK:
        now = time.monotonic()
        expired = [key for key, value in _DECISIONS.items() if value["expiresAt"] <= now]
        for key in expired:
            _DECISIONS.pop(key, None)
        _DECISIONS[result["decisionId"]] = {
            "expiresAt": now + DECISION_TTL_SECONDS,
            "policyVersion": result["policyVersion"],
            "partId": recommended["partId"],
            "quantity": recommended["quantity"],
            "unitPrice": str(recommended["unitPrice"]),
            "used": False,
        }


def validate_decision(decision_id: str, policy_version: str, order_detail: list[dict]) -> str | None:
    """校验决策存在、未过期、未使用，且订单明细与推荐快照完全一致。"""
    with _DECISION_LOCK:
        snapshot = _DECISIONS.get(decision_id)
        if snapshot is None:
            return "采购决策不存在，请重新执行 procurement_compare"
        if snapshot["expiresAt"] <= time.monotonic():
            _DECISIONS.pop(decision_id, None)
            return "采购决策已过期，请重新比价"
        if snapshot["used"]:
            return "采购决策已用于创建订单，不允许重复使用"
        if snapshot["policyVersion"] != policy_version:
            return "采购决策规则版本不匹配"
        if len(order_detail) != 1:
            return "订单明细与采购决策快照不一致"
        detail = order_detail[0]
        price = _decimal(detail.get("unitPrice"))
        if (detail.get("partId") != snapshot["partId"]
                or detail.get("quantity") != snapshot["quantity"]
                or price is None or price != Decimal(snapshot["unitPrice"])):
            return "订单物料、数量或价格与采购决策快照不一致"
    return None


def consume_decision(decision_id: str) -> None:
    """ERP 创建成功后将决策标记为已使用，防止审批结果重放。"""
    with _DECISION_LOCK:
        if decision_id in _DECISIONS:
            _DECISIONS[decision_id]["used"] = True


def compare_candidates(
    *,
    part_name: str,
    quantity: int,
    parts: list[dict],
    suppliers: dict[int, dict],
    inventories: dict[int, dict],
    budget: Decimal | None = None,
    max_lead_time_days: int | None = None,
) -> dict:
    """按固定规则完成硬约束淘汰、分项评分和稳定排序。"""
    if not part_name.strip():
        raise ValueError("物料名称不能为空")
    if quantity <= 0:
        raise ValueError("采购数量必须大于 0")
    if budget is not None and budget <= 0:
        raise ValueError("预算必须大于 0")
    if max_lead_time_days is not None and max_lead_time_days <= 0:
        raise ValueError("最大交期必须大于 0")

    valid_prices = [price for part in parts if (price := _decimal(part.get("purchasePrice"))) is not None and price > 0]
    valid_leads = [Decimal(str(part["leadTimeDays"])) for part in parts
                   if isinstance(part.get("leadTimeDays"), int) and part["leadTimeDays"] > 0]
    min_price = min(valid_prices) if valid_prices else None
    min_lead = min(valid_leads) if valid_leads else None
    candidates = []

    for part in parts:
        part_id = part.get("id")
        supplier_id = part.get("supplierId")
        supplier = suppliers.get(supplier_id, {}) if isinstance(supplier_id, int) else {}
        inventory = inventories.get(part_id, {}) if isinstance(part_id, int) else {}
        price = _decimal(part.get("purchasePrice"))
        moq = part.get("minOrderQuantity")
        lead = part.get("leadTimeDays")
        total = price * quantity if price is not None and price > 0 else None
        rejected: list[str] = []

        if not isinstance(part_id, int):
            rejected.append("物料ID缺失")
        if not isinstance(supplier_id, int):
            rejected.append("供应商ID缺失")
        if not supplier:
            rejected.append("供应商资料缺失")
        elif supplier.get("status") != 1:
            rejected.append("供应商非合作状态")
        if price is None or price <= 0:
            rejected.append("采购价格缺失或无效")
        if not isinstance(moq, int) or moq <= 0:
            rejected.append("最小起订量缺失或无效")
        elif quantity < moq:
            rejected.append(f"采购数量低于MOQ（{moq}）")
        if not isinstance(lead, int) or lead <= 0:
            rejected.append("标准交期缺失或无效")
        elif max_lead_time_days is not None and lead > max_lead_time_days:
            rejected.append(f"标准交期超过要求（{lead}>{max_lead_time_days}天）")
        if budget is not None and total is not None and total > budget:
            rejected.append(f"总价超过预算（{_number(total)}>{_number(budget)}元）")
        if not inventory:
            rejected.append("库存数据缺失")
        elif not isinstance(inventory.get("currentQuantity"), int) or not isinstance(inventory.get("safetyStock"), int):
            rejected.append("库存数据不完整")

        price_score = Decimal("0") if not min_price or not price or price <= 0 else Decimal("35") * min_price / price
        lead_score = Decimal("0") if not min_lead or not isinstance(lead, int) or lead <= 0 else Decimal("25") * min_lead / Decimal(lead)
        credit = str(supplier.get("creditRating", "")).strip().upper()
        credit_score = CREDIT_SCORES.get(credit, Decimal("0"))
        moq_score = Decimal("0")
        if isinstance(moq, int) and moq > 0:
            moq_score = min(Decimal("10"), Decimal("10") * Decimal(quantity) / Decimal(moq))
        inventory_score = Decimal("5") if inventory else Decimal("0")
        inventory_moq_score = moq_score + inventory_score
        total_score = price_score + lead_score + credit_score + inventory_moq_score

        explanation = [
            f"价格竞争力 {_number(price_score)}/35",
            f"交期匹配 {_number(lead_score)}/25",
            f"供应商信用 {_number(credit_score)}/25（{credit or '未评级'}）",
            f"库存与MOQ适配 {_number(inventory_moq_score)}/15",
        ]
        if rejected:
            explanation.append("硬约束未通过：" + "；".join(rejected))
        else:
            explanation.append("全部硬约束通过，可进入人工审批")

        candidates.append({
            "partId": part_id,
            "partCode": part.get("partCode"),
            "partName": part.get("name"),
            "model": part.get("model"),
            "supplierId": supplier_id,
            "supplierName": supplier.get("name"),
            "creditRating": supplier.get("creditRating"),
            "supplierStatus": supplier.get("status"),
            "unitPrice": _number(price),
            "quantity": quantity,
            "totalAmount": _number(total),
            "minOrderQuantity": moq,
            "leadTimeDays": lead,
            "inventory": {
                "currentQuantity": inventory.get("currentQuantity"),
                "safetyStock": inventory.get("safetyStock"),
                "warehouseLocation": inventory.get("warehouseLocation"),
            } if inventory else None,
            "eligible": not rejected,
            "rejectedReasons": rejected,
            "scores": {
                "price": _number(price_score),
                "leadTime": _number(lead_score),
                "credit": _number(credit_score),
                "inventoryMoq": _number(inventory_moq_score),
            },
            "totalScore": _number(total_score),
            "explanation": explanation,
        })

    candidates.sort(key=lambda item: (
        0 if item["eligible"] else 1,
        -Decimal(str(item["totalScore"] or 0)),
        Decimal(str(item["totalAmount"])) if item["totalAmount"] is not None else Decimal("Infinity"),
        item["leadTimeDays"] if isinstance(item["leadTimeDays"], int) else 10**9,
        item["partId"] if isinstance(item["partId"], int) else 10**9,
    ))
    recommended = next((item for item in candidates if item["eligible"]), None)

    # 估算仅放宽预算时的最低可行预算，为无解场景给出可执行建议。
    budget_only = [item for item in candidates if item["totalAmount"] is not None
                   and all(reason.startswith("总价超过预算") for reason in item["rejectedReasons"])]
    minimum_feasible_budget = None if recommended or budget is None else min(
        (Decimal(str(item["totalAmount"])) for item in budget_only), default=None,
    )
    request = {
        "partName": part_name.strip(), "quantity": quantity,
        "budget": _number(budget), "maxLeadTimeDays": max_lead_time_days,
    }
    digest_source = {"policyVersion": POLICY_VERSION, "request": request, "candidates": candidates}
    decision_id = "DEC-" + hashlib.sha256(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16].upper()

    return {
        "decisionId": decision_id,
        "policyVersion": POLICY_VERSION,
        "scoreWeights": SCORE_WEIGHTS,
        "request": request,
        "hardConstraints": {
            "supplierMustBeActive": True,
            "quantityMustMeetMoq": True,
            "budgetIsBlocking": budget is not None,
            "leadTimeIsBlocking": max_lead_time_days is not None,
        },
        "candidates": candidates,
        "recommendedPartId": recommended["partId"] if recommended else None,
        "recommendedSupplierId": recommended["supplierId"] if recommended else None,
        "minimumFeasibleBudget": _number(minimum_feasible_budget),
        "recommendation": (
            "存在满足全部硬约束的候选，推荐结果必须以排序第一项为准"
            if recommended else "无候选满足全部硬约束，不得创建采购订单"
        ),
    }


async def _api_data(http_client, path: str, *, params: dict | None = None) -> Any:
    response = await http_client.get(path, params=params)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"ERP API 失败: code={payload.get('code')}, message={payload.get('message')}")
    return payload.get("data")


def register_procurement_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="procurement_compare")
    async def compare_procurement(
        part_name: str,
        quantity: int,
        budget: Optional[float] = None,
        max_lead_time_days: Optional[int] = None,
        ctx: Context = None,
    ) -> dict:
        """使用固定版本规则进行供应商比价和推荐，并返回可审计决策ID。

        AI 只能解释本工具结果，不得自行改写分数、淘汰原因或推荐候选。若
        recommendedPartId 为空，表示没有候选通过预算、交期、MOQ 与供应商状态等
        硬约束，此时不得创建订单。
        """
        try:
            http_client = ctx.request_context.lifespan_context.get("http_client")
            parts = await _api_data(http_client, "/parts/search", params={"name": part_name.strip()}) or []
            inventory_page = await _api_data(
                http_client, "/inventory/page", params={"current": 1, "size": 1000},
            ) or {}
            inventories = {
                item.get("partId"): item for item in inventory_page.get("records", [])
                if isinstance(item.get("partId"), int)
            }
            supplier_ids = sorted({part.get("supplierId") for part in parts if isinstance(part.get("supplierId"), int)})
            suppliers = {}
            for supplier_id in supplier_ids:
                suppliers[supplier_id] = await _api_data(http_client, f"/suppliers/get/{supplier_id}") or {}
            result = compare_candidates(
                part_name=part_name,
                quantity=quantity,
                budget=_decimal(budget),
                max_lead_time_days=max_lead_time_days,
                parts=parts,
                suppliers=suppliers,
                inventories=inventories,
            )
            remember_decision(result)
            return result
        except Exception as exc:
            return {"error": str(exc), "policyVersion": POLICY_VERSION, "recommendedPartId": None}
