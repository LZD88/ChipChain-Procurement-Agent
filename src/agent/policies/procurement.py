"""Pure implementation of the deterministic supplier score policy."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.policies.registry import PolicyDefinition, register_policy


POLICY_VERSION = "supplier-score-v1"
SCORE_WEIGHTS = {"price": 35, "leadTime": 25, "credit": 25, "inventoryMoq": 15}
CREDIT_SCORES = {
    "A+": Decimal("25"),
    "A": Decimal("25"),
    "B": Decimal("20"),
    "C": Decimal("12"),
    "D": Decimal("5"),
}
TWO_PLACES = Decimal("0.01")


class ProcurementRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    part_name: str
    quantity: int
    budget: Decimal | None = None
    max_lead_time_days: int | None = None


class ProcurementSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    parts: list[dict[str, Any]] = Field(default_factory=list)
    suppliers: dict[int, dict[str, Any]] = Field(default_factory=dict)
    inventories: dict[int, dict[str, Any]] = Field(default_factory=dict)


class ProcurementDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decisionId: str
    policyVersion: str
    scoreWeights: dict[str, int]
    request: dict[str, Any]
    hardConstraints: dict[str, bool]
    candidates: list[dict[str, Any]]
    recommendedPartId: int | None
    recommendedSupplierId: int | None
    minimumFeasibleBudget: float | None
    recommendation: str


def parse_decimal(value: Any) -> Decimal | None:
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


def evaluate_supplier_score_v1(
    request: ProcurementRequest,
    source: ProcurementSource,
) -> ProcurementDecision:
    """Evaluate supplier candidates without I/O or mutable global state."""
    part_name = request.part_name
    quantity = request.quantity
    budget = request.budget
    max_lead_time_days = request.max_lead_time_days
    parts = source.parts
    suppliers = source.suppliers
    inventories = source.inventories

    if not part_name.strip():
        raise ValueError("物料名称不能为空")
    if quantity <= 0:
        raise ValueError("采购数量必须大于 0")
    if budget is not None and budget <= 0:
        raise ValueError("预算必须大于 0")
    if max_lead_time_days is not None and max_lead_time_days <= 0:
        raise ValueError("最大交期必须大于 0")

    valid_prices = [
        price
        for part in parts
        if (price := parse_decimal(part.get("purchasePrice"))) is not None and price > 0
    ]
    valid_leads = [
        Decimal(str(part["leadTimeDays"]))
        for part in parts
        if isinstance(part.get("leadTimeDays"), int) and part["leadTimeDays"] > 0
    ]
    min_price = min(valid_prices) if valid_prices else None
    min_lead = min(valid_leads) if valid_leads else None
    candidates: list[dict[str, Any]] = []

    for part in parts:
        part_id = part.get("id")
        supplier_id = part.get("supplierId")
        supplier = suppliers.get(supplier_id, {}) if isinstance(supplier_id, int) else {}
        inventory = inventories.get(part_id, {}) if isinstance(part_id, int) else {}
        price = parse_decimal(part.get("purchasePrice"))
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
        elif not isinstance(inventory.get("currentQuantity"), int) or not isinstance(
            inventory.get("safetyStock"), int
        ):
            rejected.append("库存数据不完整")

        price_score = (
            Decimal("0")
            if not min_price or not price or price <= 0
            else Decimal("35") * min_price / price
        )
        lead_score = (
            Decimal("0")
            if not min_lead or not isinstance(lead, int) or lead <= 0
            else Decimal("25") * min_lead / Decimal(lead)
        )
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
        Decimal(str(item["totalAmount"]))
        if item["totalAmount"] is not None
        else Decimal("Infinity"),
        item["leadTimeDays"] if isinstance(item["leadTimeDays"], int) else 10**9,
        item["partId"] if isinstance(item["partId"], int) else 10**9,
    ))
    recommended = next((item for item in candidates if item["eligible"]), None)

    budget_only = [
        item
        for item in candidates
        if item["totalAmount"] is not None
        and all(reason.startswith("总价超过预算") for reason in item["rejectedReasons"])
    ]
    minimum_feasible_budget = None if recommended or budget is None else min(
        (Decimal(str(item["totalAmount"])) for item in budget_only),
        default=None,
    )
    request_payload = {
        "partName": part_name.strip(),
        "quantity": quantity,
        "budget": _number(budget),
        "maxLeadTimeDays": max_lead_time_days,
    }
    digest_source = {
        "policyVersion": POLICY_VERSION,
        "request": request_payload,
        "candidates": candidates,
    }
    decision_id = "DEC-" + hashlib.sha256(
        json.dumps(
            digest_source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16].upper()

    return ProcurementDecision.model_validate({
        "decisionId": decision_id,
        "policyVersion": POLICY_VERSION,
        "scoreWeights": SCORE_WEIGHTS,
        "request": request_payload,
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
            if recommended
            else "无候选满足全部硬约束，不得创建采购订单"
        ),
    })


def compare_candidates(
    *,
    part_name: str,
    quantity: int,
    parts: list[dict],
    suppliers: dict[int, dict],
    inventories: dict[int, dict],
    budget: Decimal | None = None,
    max_lead_time_days: int | None = None,
) -> dict[str, Any]:
    request = ProcurementRequest(
        part_name=part_name,
        quantity=quantity,
        budget=budget,
        max_lead_time_days=max_lead_time_days,
    )
    source = ProcurementSource(
        parts=parts,
        suppliers=suppliers,
        inventories=inventories,
    )
    return evaluate_supplier_score_v1(request, source).model_dump()


register_policy(PolicyDefinition(version=POLICY_VERSION, evaluator=compare_candidates))
