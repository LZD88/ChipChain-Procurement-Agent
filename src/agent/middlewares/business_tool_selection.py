"""Reduce optional tool schemas for explicit ordinary procurement requests.

This only narrows tools already authorized by the role. It never grants tools,
executes actions, or changes approval and evidence validation.
"""
import re

from langchain.agents.middleware import AgentMiddleware
from deepagents.middleware.skills import SkillsMiddleware


def ordinary_procurement(messages):
    question = next((m.content for m in reversed(messages) if getattr(m, "type", None) == "human"), "")
    if not isinstance(question, str):
        return False
    # Delegation adds formatting preferences; mentioning a default chart type
    # there must not turn an ordinary comparison into a chart/report request.
    question = re.sub(r"【用户偏好】.*?(?=【|$)", "", question, flags=re.S)
    question = re.sub(r"(?:不要|无需|不需要|不生成)(?:生成)?(?:报告|图表|文件)", "", question)
    extensions = ("报告", "图表", "趋势", "市场", "外部", "历史", "搜索", "抓取", "文件", "技能",
                  "脚本", "代码", "python", "shell", "excel", "csv", "pdf", "下载", "保存", "记住",
                  "以后", "修改偏好", "查询偏好", "审计", "质保", "认证", "付款", "税费", "运费", "汇率", "权重")
    if any(word in question.lower() for word in extensions):
        return False
    return any(word in question for word in ("比价", "采购", "下单", "订单", "procurement_compare"))


class BusinessToolSelectionMiddleware(AgentMiddleware):
    def _select(self, request):
        if not ordinary_procurement(request.messages):
            return request
        # Include only role-owned business tools; preserve request_order_info and
        # order_update alongside create, and never introduce an absent tool.
        names = {"task", "procurement_compare", "order_create", "order_update", "request_order_info",
                 "order_search_details", "supplier_query", "supplier_get", "part_query", "part_search",
                 "part_by_supplier", "inventory_query", "inventory_warning"}
        available = {getattr(t, "name", None) for t in request.tools}
        question = next((m.content for m in reversed(request.messages) if getattr(m, "type", None) == "human"), "")
        if "procurement_compare" in available and "order_create" not in available and re.search(r"比价|推荐|只分析|分析采购", question):
            names = {"procurement_compare"}
        return request.override(tools=[t for t in request.tools if getattr(t, "name", None) in names])

    def wrap_model_call(self, request, handler):
        return handler(self._select(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._select(request))


class OnDemandSkillsMiddleware(SkillsMiddleware):
    @property
    def name(self):
        # Replace the SDK's skills slot rather than append a second index.
        return "SkillsMiddleware"

    def wrap_model_call(self, request, handler):
        if ordinary_procurement(request.messages):
            return handler(request)
        return super().wrap_model_call(request, handler)

    async def awrap_model_call(self, request, handler):
        if ordinary_procurement(request.messages):
            return await handler(request)
        return await super().awrap_model_call(request, handler)
