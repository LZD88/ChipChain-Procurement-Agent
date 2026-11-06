import random
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastmcp import FastMCP, Context
from mcp_server.tools.procurement_tools import consume_decision, validate_decision

from typing import Optional, List

GROUP_NAME = "order"


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
        http_client = ctx.request_context.lifespan_context.get("http_client")

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
        decision_error = validate_decision(decision_id, policy_version, order_detail)
        if decision_error:
            return {"error": decision_error}

        # 构建请求体（映射到 API 字段名）
        request_data = {}
        request_data["orderNumber"] = order_number or _deterministic_order_number(decision_id)
        if expected_delivery_date is not None:
            request_data["expectedDeliveryDate"] = expected_delivery_date
        audit_remark = f"decisionId={decision_id}; policyVersion={policy_version}"
        request_data["remark"] = f"{audit_remark}; {remark}" if remark else audit_remark
        if order_detail is not None:
            request_data["orderDetail"] = order_detail

        request_data = _prepare_create_request(request_data)
        idempotency_key = _idempotency_key(decision_id, order_detail, expected_delivery_date)

        try:
            response = await http_client.post(
                "/orders/create",
                json=request_data,
                headers={"Idempotency-Key": idempotency_key},
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return {
                    "error": f"API error: code={result.get('code')}, message={result.get('message')}"
                }

            consume_decision(decision_id)
            return result.get("data", {})
        except Exception as e:
            return {"error": str(e)}

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
