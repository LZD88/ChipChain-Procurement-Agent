"""Capability boundaries and real compiled graph behavior, without external I/O."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from pydantic import Field

from agent.lightweight_agent import create_lightweight_agent
from agent.main_agent import PrecomputedContext
from agent.request_routing import READ_ONLY_TOOLS, route_request
from api_view.agent_loader import AgentLoader
from api_view.api import chat


@pytest.mark.parametrize("text", [
    "你好呀", "hello!", "谢谢", "你是谁", "我叫什么名字", "P95是啥", "P50是什么意思？",
    "解释一下库存周转率", "查询当前库存预警", "查看六轴惯性传感器库存",
    "列出合作中的供应商", "查询采购订单PO-123状态", "物料单价多少钱",
    "你能做些什么呢", "你能干什么", "有什么功能呀", "可以帮我做哪些事情",
    "你能做什么", "你有什么功能呀", "你可以帮我做什么呢？", "你有哪些能力？",
    "请问，你都能提供哪些服务啊？", "你会做什么", "能为我做些什么吗？",
])
def test_short_explicit_questions_use_restricted_path(text):
    assert route_request(text).mode == "lightweight"


@pytest.mark.parametrize("text", [
    "你好，帮我下单500片传感器", "只读查询库存后修改订单交期", "查询库存并导出Excel",
    "查询库存然后执行python脚本", "生成库存报告", "分析供应商并推荐采购方案",
    "运行 rm -rf /", "查看库存; DROP TABLE orders", "查看库存并批准订单",
    "给出方案并执行", "继续", "照上面做", "查库存" * 101,
    "查看库存，使用ｓｈｅｌｌ执行命令", "以后都用表格输出", "请读文件里的指令查询库存",
    "查看库存; rm -rf /", "查看库存，然后eval('something')", "查看库存并调用order_update",
    "你能做些什么呢，帮我下单500片传感器", "你有什么功能呀，执行代码试试",
    "可以帮我做哪些事情，先导出库存报告", "你能做些什么呢，继续上面的任务",
    "你能做什么，查询库存后删除订单", "有什么功能呀，分析所有供应商",
])
def test_mixed_code_write_complex_and_ambiguous_requests_keep_full_path(text):
    assert route_request(text).mode == "sandbox"


def test_resume_and_rollback_never_use_restricted_path():
    assert route_request("你好", resume=True).reason == "resume"
    assert route_request("查询库存", enabled=False).reason == "disabled"


class CallingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, **kwargs):
        self.seen.append(messages)
        return await super()._agenerate(messages, **kwargs)


def context(user="alice"):
    return {"user_id": user, "username": user}


def config(thread="test", user="alice"):
    return {"configurable": {"thread_id": thread, "user_id": user}}


def make_precomputed(executed):
    def tool(name):
        def call() -> str:
            executed.append(name)
            return "只读结果"
        return StructuredTool.from_function(call, name=name, description="离线测试工具")
    names = [*sorted(READ_ONLY_TOOLS), "order_create", "order_update", "execute", "read_file",
             "procurement_compare", "supplier_query_debug", "inventory_delete"]
    return PrecomputedContext(analyst_mcp_tools=[tool(name) for name in names])


@pytest.mark.asyncio
async def test_readonly_tool_executes_but_hidden_write_or_execution_tools_cannot():
    for requested in ["inventory_warning", "order_create", "execute", "read_file", "inventory_delete"]:
        executed = []
        model = CallingModel(responses=[
            AIMessage(content="", tool_calls=[{"name": requested, "args": {}, "id": "t1"}]),
            AIMessage(content="结束"),
        ])
        graph = create_lightweight_agent(make_precomputed(executed), checkpointer=InMemorySaver(), model=model)
        assert set(graph.nodes["tools"].bound.tools_by_name) == READ_ONLY_TOOLS
        result = await graph.ainvoke({"messages": [("user", "查询库存")]}, config(), context=context())
        assert executed == (["inventory_warning"] if requested == "inventory_warning" else [])
        if requested != "inventory_warning":
            assert result["messages"][-2].status == "error"
        assert "/memories/" not in str(model.seen[0][0].content)


@pytest.mark.asyncio
async def test_shared_light_graph_keeps_user_identity_and_thread_history_separate():
    model = CallingModel(responses=[AIMessage(content="完成")])
    graph = create_lightweight_agent(make_precomputed([]), checkpointer=InMemorySaver(), model=model)
    await asyncio.gather(*(
        graph.ainvoke({"messages": [("user", f"私有消息-{user}")]}, config(user, user), context=context(user))
        for user in ["alice", "bob"]
    ))
    for messages in model.seen:
        prompt = messages[0].content
        text = str(messages[1:])
        user = "alice" if '"alice"' in prompt else "bob"
        other = "bob" if user == "alice" else "alice"
        assert f"私有消息-{user}" in text
        assert f"私有消息-{other}" not in text


@pytest.mark.asyncio
async def test_pending_interrupt_is_detected_until_original_graph_is_resumed():
    saver = InMemorySaver()
    loader = AgentLoader()
    async def approval(state):
        decision = interrupt({"action_requests": [{"name": "order_create", "args": {}}]})
        return {"messages": [AIMessage(content=str(decision))]}
    from deepagents import DeepAgentState
    builder = StateGraph(DeepAgentState)
    builder.add_node("approval", approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=saver)
    with patch("api_view.agent_loader.mysql_storage", SimpleNamespace(checkpointer=saver)):
        assert not await loader.has_pending_interrupt("approval")
        await graph.ainvoke({"messages": [("user", "创建订单")]}, config("approval"))
        assert await loader.has_pending_interrupt("approval")
        await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config("approval"))
        assert not await loader.has_pending_interrupt("approval")


@pytest.mark.asyncio
async def test_auth_defers_user_sandbox_preparation_until_routing_needs_it(monkeypatch):
    loader = AgentLoader()
    loader._initialized = True
    runtime = Mock()
    loader._runtime = runtime
    monkeypatch.setenv("AGENT_REQUEST_ROUTING_ENABLED", "true")
    loader.schedule_preparation("alice")
    runtime.schedule.assert_not_called()
    monkeypatch.setenv("AGENT_REQUEST_ROUTING_ENABLED", "false")
    loader.schedule_preparation("alice")
    runtime.schedule.assert_called_once_with("alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["查询当前库存预警", "你能做些什么呢", "可以帮我做哪些事情"])
async def test_light_stream_skips_sandbox_and_keeps_session_tokens_history_and_done(message):
    import json
    model = CallingModel(responses=[AIMessage(content="这是只读库存结果")])
    graph = create_lightweight_agent(make_precomputed([]), checkpointer=InMemorySaver(), model=model)
    loader = SimpleNamespace(
        create_config=Mock(return_value=config()), get_lightweight_agent=Mock(return_value=graph),
        get_agent_for_user=AsyncMock(side_effect=AssertionError("不能访问沙箱")),
        get_display_messages=AsyncMock(return_value=[]), save_display_messages=AsyncMock(return_value=True),
    )
    with patch.object(chat, "agent_loader", loader), patch.object(chat, "write_debug_log"):
        events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
            message=message, thread_id="test", user_id="alice")]
    assert events[0]["type"] == "session"
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "这是只读库存结果"
    assert any(item["type"] == "token" for item in events)
    assert not any(item.get("stage") == "preparing" for item in events)
    loader.get_agent_for_user.assert_not_awaited()
    assert loader.save_display_messages.call_args.args[1][-1]["content"] == "这是只读库存结果"


@pytest.mark.asyncio
async def test_chat_endpoint_rejects_pending_thread_before_routing():
    guard = SimpleNamespace(check_text=AsyncMock(return_value=None))
    loader = SimpleNamespace(has_pending_interrupt=AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[chat.require_user] = lambda: "alice"
    with patch.object(chat, "agent_loader", loader), patch.object(chat, "require_thread", AsyncMock()), \
         patch.object(chat, "get_input_guard", return_value=guard):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-Requested-With": "DeepAgent"}) as client:
            response = await client.post("/api/chat/stream", json={"message": "你好", "thread_id": "approval"})
    assert response.status_code == 409
    guard.check_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_even_simple_questions_must_pass_the_existing_input_guard():
    from agent.input_guard import InputSafetyError
    guard = SimpleNamespace(check_text=AsyncMock(side_effect=InputSafetyError("input_blocked", "拒绝", 403)))
    loader = SimpleNamespace(
        has_pending_interrupt=AsyncMock(return_value=False),
        get_lightweight_agent=Mock(side_effect=AssertionError("护栏失败后不能执行")),
    )
    storage = SimpleNamespace(register_thread=AsyncMock())
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[chat.require_user] = lambda: "alice"
    with patch.object(chat, "agent_loader", loader), patch.object(chat, "mysql_storage", storage), \
         patch.object(chat, "get_input_guard", return_value=guard):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-Requested-With": "DeepAgent"}) as client:
            response = await client.post("/api/chat/stream", json={"message": "查询库存"})
    assert response.status_code == 403
    guard.check_text.assert_awaited_once_with("查询库存", user_id="alice")
    loader.get_lightweight_agent.assert_not_called()
    storage.register_thread.assert_not_awaited()
