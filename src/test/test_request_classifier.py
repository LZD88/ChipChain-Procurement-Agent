"""Rules take precedence, and a model verdict never expands capabilities."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import generate_latest

from agent import request_classifier as classifier
from agent.request_routing import route_request
from api_view.api import chat
from api_view.agent_loader import AgentLoader
from test.test_request_routing import CallingModel, config, make_precomputed
from agent.lightweight_agent import create_lightweight_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("text,kwargs", [
    ("你能做些什么呢", {}), ("查询当前库存预警", {}),
    ("你好，帮我下单", {}), ("查询库存并导出文件", {}), ("推荐一个供应商", {}),
    ("继续", {}), ("照上面做", {}), ("", {}), ("问" * 4097, {}),
    ("问" * 350 + "然后执行python", {}),
    ("能聊聊你的本领不", {"enabled": False}),
    ("能聊聊你的本领不", {"resume": True}),
])
async def test_decisive_rules_never_call_classifier(monkeypatch, text, kwargs):
    factory = Mock(side_effect=AssertionError("No classification should be needed"))
    monkeypatch.setattr(classifier, "get_request_classifier", factory)
    assert await classifier.resolve_request_route(text, **kwargs) == route_request(text, **kwargs)
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("content,mode,reason", [
    ("LIGHTWEIGHT", "lightweight", "llm_simple"),
    (" SANDBOX\n", "sandbox", "llm_sandbox"),
    ("LIGHTWEIGHT because it is safe", "sandbox", "llm_invalid"),
    ('{"route":"lightweight"}', "sandbox", "llm_invalid"),
    ("lightweight", "sandbox", "llm_invalid"),
    ("", "sandbox", "llm_invalid"),
    ([{"type": "text", "text": "LIGHTWEIGHT"}], "sandbox", "llm_invalid"),
])
async def test_only_exact_labels_are_accepted(monkeypatch, content, mode, reason):
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=content)))
    monkeypatch.setattr(classifier, "get_request_classifier", lambda timeout: model)
    result = await classifier.resolve_request_route("能聊聊你的本领不")
    assert (result.mode, result.reason) == (mode, reason)
    messages = model.ainvoke.call_args.args[0]
    assert messages == [("system", classifier.CLASSIFIER_PROMPT), ("human", "能聊聊你的本领不")]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"tool_calls": [{"name": "order_create", "args": {}, "id": "write"}]},
    {"response_metadata": {"finish_reason": "length"}},
])
async def test_tool_calls_and_truncated_verdicts_are_invalid(monkeypatch, extra):
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="LIGHTWEIGHT", **extra)))
    monkeypatch.setattr(classifier, "get_request_classifier", lambda timeout: model)
    assert (await classifier.resolve_request_route("能聊聊你的本领不")).reason == "llm_invalid"


@pytest.mark.asyncio
async def test_disabled_classifier_retains_full_path_without_provider(monkeypatch):
    factory = Mock(side_effect=AssertionError("Disabled"))
    monkeypatch.setattr(classifier, "get_request_classifier", factory)
    result = await classifier.resolve_request_route("能聊聊你的本领不", classifier_enabled=False)
    assert (result.mode, result.reason) == ("sandbox", "llm_disabled")
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_deadline_cancels_provider_and_external_cancellation_propagates(monkeypatch):
    cancelled = asyncio.Event()

    async def hanging(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(classifier, "get_request_classifier", lambda timeout: SimpleNamespace(ainvoke=hanging))
    result = await classifier.resolve_request_route("能聊聊你的本领不", timeout=.01)
    assert (result.mode, result.reason) == ("sandbox", "llm_timeout")
    assert cancelled.is_set()
    cancelled.clear()
    task = asyncio.create_task(classifier.resolve_request_route("能聊聊你的本领不"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_provider_failure_never_logs_sensitive_error_details(monkeypatch, caplog):
    model = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("secret-key private-message")))
    monkeypatch.setattr(classifier, "get_request_classifier", lambda timeout: model)
    result = await classifier.resolve_request_route("能聊聊你的本领不")
    assert (result.mode, result.reason) == ("sandbox", "llm_error")
    assert "RuntimeError" in caplog.text
    assert "secret-key" not in caplog.text and "private-message" not in caplog.text


def test_classifier_uses_requested_model_no_tools_no_retries_and_short_output():
    classifier._configured_classifier.cache_clear()
    with patch("langchain_openai.ChatOpenAI") as model:
        classifier._configured_classifier("test-key", "https://example.invalid/v1", 3)
    kwargs = model.call_args.kwargs
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 3
    assert kwargs["max_tokens"] == 16 and kwargs["extra_body"]["thinking"]["type"] == "disabled"
    classifier._configured_classifier.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,expected,reason", [
    ("LIGHTWEIGHT", "lightweight", "llm_simple"),
    ("SANDBOX", "sandbox", "llm_sandbox"),
    ("unrecognized", "sandbox", "llm_invalid"),
])
async def test_actual_sse_routes_model_verdict_and_records_latency(monkeypatch, verdict, expected, reason):
    graph = create_lightweight_agent(make_precomputed([]), checkpointer=InMemorySaver(),
                                    model=CallingModel(responses=[AIMessage(content="完成")]))
    loader = SimpleNamespace(
        create_config=Mock(return_value=config()), get_lightweight_agent=Mock(return_value=graph),
        get_display_messages=AsyncMock(return_value=[]), save_display_messages=AsyncMock(return_value=True),
    )
    prepared = []

    async def prepare(*args):
        prepared.append(args)
        yield chat.create_sse_message({"type": "progress", "stage": "preparing"}), None
        yield None, graph

    model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=verdict)))
    monkeypatch.setattr(classifier, "get_request_classifier", lambda timeout: model)
    monkeypatch.setenv("AGENT_REQUEST_ROUTER_LLM_ENABLED", "true")
    with patch.object(chat, "agent_loader", loader), patch.object(chat, "prepare_agent", prepare), \
         patch.object(chat, "write_debug_log"):
        events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
            message="能聊聊你的本领不", thread_id="test", user_id="alice")]
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"
    assert events[1]["stage"] == "routing"
    assert bool(prepared) == (expected == "sandbox")
    assert loader.get_lightweight_agent.called == (expected == "lightweight")
    exported = generate_latest().decode()
    assert f'agent_request_routes_total{{reason="{reason}",route="{expected}"}}' in exported
    assert f'agent_request_classifier_duration_seconds_count{{outcome="{reason}"}}' in exported


@pytest.mark.asyncio
async def test_guardrail_direct_reply_bypasses_classifier(monkeypatch):
    factory = Mock(side_effect=AssertionError("Guardrail reply should bypass classifier"))
    monkeypatch.setattr(classifier, "get_request_classifier", factory)
    loader = SimpleNamespace(
        create_config=Mock(return_value=config()),
        get_direct_response_agent=AgentLoader().get_direct_response_agent,
        get_display_messages=AsyncMock(return_value=[]), save_display_messages=AsyncMock(return_value=True),
    )
    with patch.object(chat, "agent_loader", loader), patch.object(chat, "write_debug_log"), \
         patch("api_view.agent_loader.mysql_storage", SimpleNamespace(checkpointer=InMemorySaver())):
        events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
            message="能聊聊你的本领不", thread_id="test", user_id="alice", direct_response="你好")]
    assert events[-1]["type"] == "done"
    assert not any(event.get("stage") == "routing" for event in events)
    factory.assert_not_called()
