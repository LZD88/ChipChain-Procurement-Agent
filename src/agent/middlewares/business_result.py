"""Render completed ordinary business results without another model round trip."""
import json

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

from agent.middlewares.business_tool_selection import ordinary_procurement


def _data(content):
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    try:
        result = json.loads(content)
    except (ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def comparison_answer(data):
    if not isinstance(data.get("candidates"), list) or "recommendedPartId" not in data:
        return None
    rows = data["candidates"]
    chosen = next((r for r in rows if r.get("partId") == data["recommendedPartId"] and r.get("eligible") is True), None)
    if data["recommendedPartId"] is not None and chosen is None:
        return None  # Inconsistent evidence must be handled as an error, not success.
    lines = ["规则引擎比价结果："]
    if chosen:
        lines.append(f"推荐 {chosen.get('supplierName')} 的 {chosen.get('partName')}，采购 {chosen.get('quantity')} 个，单价 {chosen.get('unitPrice')} 元，货款合计 {chosen.get('totalAmount')} 元，标准交期 {chosen.get('leadTimeDays')} 天。")
        lines.append("该候选通过本次硬约束校验，排序与评分来自规则引擎。")
    else:
        lines.append("没有候选满足全部硬约束，当前不推荐下单。")
    lines += ["", "| 供应商 | 单价（元） | 总价（元） | 交期（天） | 规则得分 | 校验结果 |", "|---|---:|---:|---:|---:|---|"]
    for row in rows:
        reason = "；".join(row.get("rejectedReasons", [])) or ("通过" if row.get("eligible") else "未通过")
        values = [row.get("supplierName"), row.get("unitPrice"), row.get("totalAmount"), row.get("leadTimeDays"), row.get("totalScore"), reason]
        lines.append("| " + " | ".join(str(v if v is not None else "未提供").replace("|", "／").replace("\n", " ") for v in values) + " |")
    if chosen:
        inventory = chosen.get("inventory") or {}
        lines.append(f"\n推荐物料当前库存：{inventory.get('currentQuantity', '未提供')}；安全库存：{inventory.get('safetyStock', '未提供')}。")
    lines.append("\n以上为 ERP 当前报价口径，未做外部市场核价。此分析未创建订单；下单仍需人工审批。")
    return "\n".join(lines)


class BusinessResultMiddleware(AgentMiddleware):
    def __init__(self, role):
        self.role = role

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        messages = state.get("messages", [])
        if not messages or not ordinary_procurement(messages) or not isinstance(messages[-1], ToolMessage):
            return None
        last = messages[-1]
        previous = next((m for m in reversed(messages[:-1]) if m.type == "ai"), None)
        # Do not short-circuit parallel tool batches or unfinished action plans.
        if not previous or len(previous.tool_calls) != 1 or previous.tool_calls[0]["id"] != last.tool_call_id:
            return None
        call = previous.tool_calls[0]
        name, answer = call["name"], None
        if self.role == "procurement-analyst" and name == "procurement_compare":
            data = _data(last.content)
            if last.status == "error" or (data and data.get("error")):
                answer = "规则引擎比价失败，工具返回异常，无法生成可靠推荐。请检查条件或稍后重试；本次未创建订单。"
            elif data:
                answer = comparison_answer(data)
        elif self.role == "main" and name == "task" and call["args"].get("subagent_type") == "procurement-analyst":
            if last.status != "error" and isinstance(last.content, str) and last.content.startswith("规则引擎比价"):
                answer = last.content
        elif self.role in {"procurement-order", "procurement-orchestrator"} and name in {"order_create", "order_update"}:
            data = _data(last.content)
            if last.status == "error" and isinstance(last.content, str) and last.content.startswith(f"User rejected the tool call for `{name}`"):
                answer = "人工审批已拒绝，本次订单操作未执行。"
            elif last.status != "error" and data and not data.get("error") and (data.get("orderNumber") or data.get("orderId")):
                label = "创建" if name == "order_create" else "更新"
                answer = f"采购单已{label}。订单号：{data.get('orderNumber', data.get('orderId'))}。订单状态以 ERP 返回为准：{data.get('status', '请在订单详情查看')}。"
        if answer is not None:
            return {"messages": [AIMessage(content=answer)], "jump_to": "end"}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)
