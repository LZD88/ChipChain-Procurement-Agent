import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from agent.middlewares.business_result import BusinessResultMiddleware, comparison_answer
from agent.evaluation.prompt_candidate_runner import run_candidate_case, IsolatedBusinessTools, build_isolated_graph
from agent.evaluation.rule_runner import load_default_inputs
from agent.prompt_releases.bundles import builtin_bundle
from agent.prompt_releases.candidate_suite import candidate_suite


class Model(FakeMessagesListChatModel):
    calls: int = 0
    def bind_tools(self, tools, **kwargs):
        return self
    def _generate(self, *args, **kwargs):
        self.calls += 1
        return super()._generate(*args, **kwargs)


def completed(name, data, question="比价采购传感器", calls=1):
    return {"messages": [HumanMessage(content=question), AIMessage(content="", tool_calls=[{"name":name,"args":{},"id":str(i)} for i in range(calls)]), ToolMessage(content=json.dumps(data,ensure_ascii=False),name=name,tool_call_id="0")]}


@pytest.mark.asyncio
async def test_main_comparison_needs_two_model_calls_and_streams_final_answer():
    fixture=load_default_inputs()[1]["successful-recommendation"]
    model=Model(responses=[AIMessage(content="",tool_calls=[{"name":"task","id":"task","args":{"subagent_type":"procurement-analyst","description":"【用户偏好】图表类型默认bar【需求正文】比价传感器10个，只分析不下单"}}]),AIMessage(content="",tool_calls=[{"name":"procurement_compare","id":"compare","args":{"part_name":"传感器","quantity":10,"budget":1500}}])])
    business=IsolatedBusinessTools(fixture)
    graph=build_isolated_graph(builtin_bundle(),"main",model,business.tools())
    chunks=[c async for c in graph.astream({"messages":[("user","只分析采购传感器10个，预算1500")]},{"configurable":{"thread_id":"two-calls"}},stream_mode=["messages","values"],subgraphs=True,context={"user_id":"qa","username":"qa"})]
    finals=[c[2]["messages"][-1] for c in chunks if c[1]=="values" and isinstance(c[2].get("messages",[None])[-1],AIMessage)]
    assert model.calls == 2
    assert any("规则引擎比价结果" in str(m.content) and "供应商一" in str(m.content) for m in finals)
    streamed=[c[2][0] for c in chunks if c[1]=="messages"]
    assert any("规则引擎比价结果" in str(m.content) for m in streamed)
    assert [e["tool"] for e in business.events] == ["procurement_compare"]


def test_incomplete_or_parallel_evidence_never_short_circuits():
    middleware=BusinessResultMiddleware("procurement-analyst")
    assert middleware.before_model(completed("procurement_compare",{"recommendedPartId":1}),None) is None
    assert middleware.before_model(completed("procurement_compare",{"recommendedPartId":1,"candidates":[{"partId":2,"eligible":True}]}),None) is None
    assert middleware.before_model(completed("procurement_compare",{"recommendedPartId":None,"candidates":[]},calls=2),None) is None
    assert middleware.before_model(completed("procurement_compare",{"recommendedPartId":None,"candidates":[]},question="生成比价深度报告"),None) is None


def test_order_errors_or_comparison_cannot_be_presented_as_write_success():
    middleware=BusinessResultMiddleware("procurement-order")
    for name,data in [("order_create",{"error":"already claimed","orderId":1}),("order_create",{"status":"unknown"}),("procurement_compare",{"recommendedPartId":None,"candidates":[]})]:
        assert middleware.before_model(completed(name,data,question="创建采购订单"),None) is None
    success=middleware.before_model(completed("order_create",{"orderId":1,"orderNumber":"ERP-1","status":"待审核"},question="创建采购订单"),None)
    assert success["jump_to"]=="end" and "ERP-1" in success["messages"][0].content


def test_analysis_preserves_rule_values_rejections_and_no_candidate():
    data={"recommendedPartId":None,"candidates":[{"supplierName":"A","unitPrice":100,"totalAmount":1000,"leadTimeDays":7,"totalScore":98.5,"eligible":False,"rejectedReasons":["总价超过预算"]}]}
    text=comparison_answer(data)
    assert "当前不推荐下单" in text and "98.5" in text and "总价超过预算" in text
    assert "未创建订单" in text


def test_rejection_ends_with_explicit_non_execution():
    state=completed("order_create",{},question="创建采购订单")
    state["messages"][-1]=ToolMessage(name="order_create",tool_call_id="0",status="error",
        content="User rejected the tool call for `order_create` with id 0. The tool was not executed.")
    result=BusinessResultMiddleware("procurement-order").before_model(state,None)
    assert result["jump_to"]=="end" and "未执行" in result["messages"][0].content
