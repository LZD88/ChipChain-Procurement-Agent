"""
对话 API 模块

提供流式对话接口、中断恢复接口和会话状态查询接口
"""

import json
import asyncio
import uuid
import re
import os
import tempfile
import hashlib
import time
from contextlib import aclosing
from datetime import datetime
from typing import AsyncIterator

from deepagents import create_deep_agent, DeepAgentState
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from agent.schema import (
    ChatRequest,
    ChatResponse,
    Message,
)
from api_view.agent_loader import agent_loader
from api_view.runtime_preparation import GraphLease
from api_view.auth import require_same_origin_request, require_thread, require_user
from agent.mysql_storage import mysql_storage
from agent.decision_repository import DecisionRepository
from agent.input_guard import InputSafetyError, get_input_guard
from api_view.observability import StreamObservation, REQUEST_ROUTES, CLASSIFIER_DURATION
from agent.request_routing import route_request
from agent.request_classifier import resolve_request_route
from agent.runtime_config import RuntimeConfig


# 创建路由
router = APIRouter(dependencies=[Depends(require_same_origin_request)])

AGENT_PREPARATION_TIMEOUT_SECONDS = 300
AGENT_PREPARATION_PROGRESS_SECONDS = 10


class AgentIdleTimeout(RuntimeError):
    """Agent stopped producing stream events before the graph completed."""


async def with_idle_timeout(iterator, seconds: int = 90):
    """如果 Agent 长时间没有任何事件，结束流并向用户返回错误。"""
    async_iterator = iterator.__aiter__()
    while True:
        try:
            yield await asyncio.wait_for(async_iterator.__anext__(), timeout=seconds)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise AgentIdleTimeout("智能助手响应超时，请重试。会话和问题已为您保留。") from exc

# 调试日志文件路径（在项目根目录的 temp 文件夹下）
DEBUG_LOG_DIR = os.path.join(tempfile.gettempdir(), "deepagent_debug")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)


def get_debug_log_path(thread_id: str) -> str:
    """获取当前会话的调试日志文件路径"""
    safe_id = thread_id.replace("/", "_").replace("\\", "_")[:50]
    return os.path.join(DEBUG_LOG_DIR, f"stream_{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def write_debug_log(filepath: str, event_type: str, data: dict, raw_token: object = None):
    """写入调试日志"""
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {event_type}\n")
            f.write(f"  data: {json.dumps(data, ensure_ascii=False, default=str)[:2000]}\n")
            if raw_token is not None:
                token_info = {}
                if hasattr(raw_token, 'type'):
                    token_info['type'] = raw_token.type
                if hasattr(raw_token, 'name'):
                    token_info['name'] = raw_token.name
                if hasattr(raw_token, 'content'):
                    c = raw_token.content
                    if isinstance(c, str):
                        token_info['content_preview'] = c[:500]
                    elif isinstance(c, list):
                        token_info['content_len'] = len(c)
                        token_info['content_preview'] = str(c)[:500]
                    else:
                        token_info['content_preview'] = str(c)[:500]
                if hasattr(raw_token, 'tool_call_chunks') and raw_token.tool_call_chunks:
                    token_info['tool_call_chunks'] = str(raw_token.tool_call_chunks)[:500]
                if hasattr(raw_token, 'id'):
                    token_info['id'] = raw_token.id
                f.write(f"  token: {json.dumps(token_info, ensure_ascii=False, default=str)[:2000]}\n")
            f.write("\n")
    except Exception:
        pass  # 调试日志失败不影响主流程


def extract_subagent_name(namespace: tuple) -> str:
    """
    从 namespace 中提取子代理名称
    """
    for segment in namespace:
        if segment.startswith("tools:"):
            return segment.replace("tools:", "")
    return "main"


_SUBAGENT_TYPE_RE = re.compile(r'"subagent_type"\s*:\s*"([^"]+)"')


def parse_task_subagent_type(args_str: str) -> str | None:
    """从 task 工具的流式参数片段中解析被委派的子代理名称（subagent_type）。"""
    match = _SUBAGENT_TYPE_RE.search(args_str or "")
    return match.group(1) if match else None


def current_subagent_name(subagent_stack: list) -> str | None:
    """返回当前正在运行的子代理名称（栈顶即为最近入栈的子代理）。"""
    return subagent_stack[-1] if subagent_stack else None


def is_likely_uuid(text: str) -> bool:
    """
    判断文本是否大概率是 UUID（而非有意义的回复内容）
    例如：3576bba4-42e5-a769-c2f7-ea8444829951
    """
    # UUID 格式：8-4-4-4-12
    uuid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    return bool(re.match(uuid_pattern, text.strip()))


def extract_content_from_token(token) -> str:
    """
    从 token 中提取文本内容

    处理 token.content 可能是字符串、列表或其他类型的情况
    过滤掉明显是 UUID 或无关标识符的内容
    """
    if not hasattr(token, 'content'):
        return ""

    content = token.content

    # 如果 content 是字符串，直接返回
    if isinstance(content, str):
        # 过滤纯 UUID 内容
        if is_likely_uuid(content):
            return ""
        return content

    # 如果 content 是列表，尝试提取其中的文本
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                # 结构化内容：只提取 text 类型，忽略 image_url 等
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    if not is_likely_uuid(t):
                        text_parts.append(t)
                elif item_type == "image_url":
                    # 图片 URL 不提取为文本，由 serialize_tool_result 处理
                    pass
                elif "text" in item:
                    t = item["text"]
                    if not is_likely_uuid(str(t)):
                        text_parts.append(str(t))
                elif "content" in item:
                    text_parts.append(str(item["content"]))
            else:
                text_parts.append(str(item))
        return ''.join(text_parts)

    # 其他类型转换为字符串
    return str(content) if content is not None else ""


def serialize_tool_result(content) -> dict:
    """
    将工具执行结果序列化为前端可用的结构化数据

    处理 tool 消息的 content，可能是字符串、列表（包含文本和图片）
    提取文本和图片数据，返回结构化字典
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
        # 提取 markdown 图片语法
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', content):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)

    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    text_parts.append(t)
                elif item_type == "image_url":
                    image_url = item.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url:
                        result["images"].append(url)
                elif item_type == "image":
                    image_data = item.get("data", "") or item.get("image_data", "")
                    if image_data:
                        result["images"].append(image_data)
                elif "text" in item:
                    text_parts.append(item["text"])
                elif "content" in item:
                    text_parts.append(str(item["content"]))
                else:
                    text_parts.append(str(item))
            else:
                text_parts.append(str(item))
        result["text"] = ''.join(text_parts)
    else:
        result["text"] = str(content) if content is not None else ""

    # 从文本中提取所有图片 URL（包括 markdown 语法和直链）
    # 支持常见图片托管域名（即使 URL 不以图片扩展名结尾）
    if result["text"]:
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', result["text"]):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)
        # 也检测 data:image base64
        for match in re.finditer(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', result["text"]):
            url = match.group(0)
            if url not in result["images"]:
                result["images"].append(url)

    return result


def create_sse_message(data: dict) -> str:
    """创建 SSE 格式的消息"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ============================================================
# 中断恢复请求模型
# ============================================================

class ResumeRequest(BaseModel):
    """中断恢复请求体，resume 字段的格式取决于中断类型：
    - 数据补充中断: {"supplement": "用户自由文本输入"}
    - HITL 审批中断: {"decisions": [{"type": "approve"}]} 或 [{"type": "reject"}]
    """
    resume: dict
    model_config = {"extra": "forbid"}


def _approval_action(action: dict, thread_id: str, index: int) -> dict | None:
    if action.get("name") != "order_create":
        return None
    arguments = action.get("args") or action.get("arguments") or {}
    if not isinstance(arguments, dict):
        return None
    decision_id = arguments.get("decision_id") or arguments.get("decisionId")
    if not isinstance(decision_id, str) or not decision_id.startswith("DEC-"):
        return None
    identity = json.dumps(
        {"thread": thread_id, "index": index, "action": action.get("id"), "decision": decision_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    approval_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return {"approval_id": approval_id, "decision_id": decision_id}


async def record_hitl_approval_requests(
    repository: DecisionRepository,
    *,
    thread_id: str,
    user_id: str,
    action_requests: list[dict],
) -> int:
    """Persist owner-scoped approval requests before exposing the approval UI."""
    recorded = 0
    for index, action in enumerate(action_requests):
        approval = _approval_action(action, thread_id, index)
        if approval is None:
            continue
        evidence = await repository.get_for_owner(approval["decision_id"], user_id)
        if evidence is None or evidence.get("thread_id") != thread_id:
            raise RuntimeError("订单审批引用的采购决策不属于当前会话")
        await repository.append_event(
            approval["decision_id"],
            user_id,
            "APPROVAL_REQUESTED",
            actor_user_id=user_id,
            event_key=f"approval-request:{approval['approval_id']}",
            payload={
                "approvalRequestId": approval["approval_id"],
                "threadId": thread_id,
                "action": "order_create",
            },
        )
        recorded += 1
    return recorded


async def record_hitl_resume_events(
    repository: DecisionRepository,
    *,
    thread_id: str,
    user_id: str,
    decisions: list[dict],
) -> None:
    """Atomically de-duplicate approval/rejection audit events at the evidence boundary."""
    pending = await repository.pending_approvals_for_thread(
        thread_id,
        user_id,
        limit=max(1, len(decisions)),
    )
    if len(pending) != len(decisions):
        raise ValueError("审批请求不存在、已处理或与恢复数据不匹配")
    for approval, decision in zip(pending, decisions, strict=True):
        approval_id = (approval.get("payload") or {}).get("approvalRequestId")
        if not approval_id:
            raise ValueError("审批请求缺少可验证标识")
        decision_type = decision["type"]
        appended = await repository.append_event(
            approval["decision_id"],
            user_id,
            "APPROVED" if decision_type == "approve" else "REJECTED",
            actor_user_id=user_id,
            event_key=f"approval-resolution:{approval_id}",
            payload={
                "approvalRequestId": approval_id,
                "threadId": thread_id,
                "decision": decision_type,
            },
        )
        if not appended:
            raise ValueError("审批请求已处理，不允许重复恢复")


# ============================================================
# 流式对话核心逻辑
# ============================================================

async def prepare_agent(user_id: str, thread_id: str, endpoint="chat"):
    """Bound this request's acquisition; shared preparation owns its lifecycle."""
    yield create_sse_message({
        "type": "progress", "stage": "preparing",
        "message": "正在连接采购分析环境…", "thread_id": thread_id,
    }), None
    started = asyncio.get_running_loop().time()
    preparation = asyncio.create_task(agent_loader.get_agent_for_user(user_id, endpoint=endpoint))
    delivered = False
    try:
        async with asyncio.timeout(AGENT_PREPARATION_TIMEOUT_SECONDS):
            while not preparation.done():
                completed, _ = await asyncio.wait(
                    {preparation}, timeout=AGENT_PREPARATION_PROGRESS_SECONDS,
                )
                if not completed:
                    elapsed = int(asyncio.get_running_loop().time() - started)
                    yield create_sse_message({
                        "type": "progress", "stage": "preparing",
                        "message": f"正在连接并检查采购分析环境，已等待 {elapsed} 秒…",
                        "thread_id": thread_id,
                    }), None
            delivered = True
            yield None, preparation.result()
    except TimeoutError as exc:
        raise RuntimeError("分析环境连接超时，请稍后重试。会话和问题已为您保留。") from exc
    finally:
        if not preparation.done():
            preparation.cancel()
        await asyncio.gather(preparation, return_exceptions=True)
        if not delivered and not preparation.cancelled() and preparation.exception() is None:
            lease = preparation.result()
            if isinstance(lease, GraphLease):
                await lease.release()


async def stream_chat_response(
    message: str = None,
    thread_id: str = None,
    resume_data: dict = None,
    *,
    user_id: str,
    direct_response: str | None = None,
) -> AsyncIterator[str]:
    """
    流式生成对话响应，支持 Human-in-the-Loop 中断与恢复。

    两种调用模式：
    1. 初始对话 — 传入 message（用户消息）
    2. 中断恢复 — 传入 resume_data（Command.resume 的值）

    当 Agent 触发中断时（request_order_info 数据补充 / order_create|update HITL 审批），
    流会发送 interrupt 事件后结束。前端收集用户决策后通过 /resume 端点恢复。

    同时累积完整的展示消息列表（包含子代理消息），在流结束后存入 MySQL。
    """
    endpoint = "resume" if resume_data is not None else "chat"
    routing_config = RuntimeConfig.from_env()
    route = route_request(message, enabled=routing_config.request_routing_enabled,
                          resume=resume_data is not None)
    route_mode = "direct" if direct_response is not None and resume_data is None else route.mode
    observation = StreamObservation(endpoint, route=route_mode).start()
    agent_graph = None
    context = {"user_id": user_id, "username": user_id}
    if direct_response is not None:
        context["guardrail_response"] = direct_response
    config = agent_loader.create_config(thread_id, user_id=user_id)

    collected_content = ""
    # 工具调用表：按 LLM 的 tool_call_id 关联（ToolMessage.tool_call_id 用于结果匹配）
    tool_calls = {}
    # 当前消息内 tool_call index -> tool_call_id（args 流式片段只带 index 不带 id）
    index_to_call_id = {}
    # 子代理名称栈：task 工具声明子代理后入栈，结果返回后出栈（支持嵌套）
    subagent_stack = []

    # ---- 根据模式构建 input 和 display_messages ----
    if resume_data is not None:
        # 恢复模式：加载已有展示消息，用 Command(resume=...) 恢复
        existing = await agent_loader.get_display_messages(thread_id) or []
        display_messages = existing
        current_input = Command(resume=resume_data)
    else:
        # 包含已有历史；新会话由端点预存的问题只保留一份。
        current_input = {"messages": [{"role": "user", "content": message}]}
        display_messages = list(await agent_loader.get_display_messages(thread_id) or [])
        if not (display_messages and display_messages[-1].get("role") == "user"
                and display_messages[-1].get("content") == message):
            display_messages.append({
                "id": f"user-{uuid.uuid4()}",
                "role": "user",
                "content": message,
            })

    # 创建调试日志文件
    debug_log = get_debug_log_path(thread_id)

    def _last_display_is_assistant():
        return (display_messages and
                display_messages[-1]["role"] == "assistant")

    try:
        write_debug_log(debug_log, "STREAM_START", {
            "message": message,
            "thread_id": thread_id,
            "is_resume": resume_data is not None,
        })

        # 在耗时的沙箱/模型初始化之前把会话 ID 交给前端，左侧列表可立即同步。
        if resume_data is None:
            yield create_sse_message({
                "type": "session",
                "thread_id": thread_id,
            })

        if route_mode != "direct" and route.reason == "uncertain":
            if routing_config.request_router_llm_enabled:
                yield create_sse_message({
                    "type": "progress", "stage": "routing",
                    "message": "正在识别处理方式…", "thread_id": thread_id,
                })
            classification_started = time.perf_counter()
            try:
                route = await resolve_request_route(
                    message, enabled=routing_config.request_routing_enabled,
                    classifier_enabled=routing_config.request_router_llm_enabled,
                    timeout=routing_config.request_router_timeout_seconds,
                )
            except asyncio.CancelledError:
                CLASSIFIER_DURATION.labels("cancelled").observe(time.perf_counter() - classification_started)
                raise
            if routing_config.request_router_llm_enabled:
                CLASSIFIER_DURATION.labels(route.reason).observe(time.perf_counter() - classification_started)
            route_mode = route.mode
            observation.route = route_mode
        REQUEST_ROUTES.labels(route_mode, "guardrail" if route_mode == "direct" else route.reason).inc()

        if direct_response is not None and resume_data is None:
            agent_graph = agent_loader.get_direct_response_agent(direct_response)
        elif route.mode == "lightweight":
            agent_graph = agent_loader.get_lightweight_agent()
        else:
            async with aclosing(prepare_agent(user_id, thread_id, endpoint)) as preparation_stream:
                async for progress, prepared_graph in preparation_stream:
                    if progress is not None:
                        yield progress
                    else:
                        agent_graph = prepared_graph

        if direct_response is None:
            yield create_sse_message({
                "type": "progress",
                "stage": "reasoning",
                "message": ("正在处理您的问题…" if route.mode == "lightweight"
                            else "正在拆解需求，核验库存、预算、交期与供应商…"),
                "thread_id": thread_id,
            })

        # 流式调用 agent.astream()
        # stream_mode=["messages", "values"] — messages 流显示 + values 流检测中断
        async for chunk in with_idle_timeout(agent_graph.astream(
            input=current_input,
            config=config,
            context=context,
            stream_mode=["messages", "values"],
            subgraphs=True, # 启用子代理流式输出
            version="v2",
        )):
            chunk_type = chunk.get("type")

            # ---- 值流（中断检测，必须在 messages 处理之前）----
            if chunk_type == "values" and chunk.get("interrupts"):
                interrupts = chunk["interrupts"]
                write_debug_log(debug_log, "INTERRUPT_DETECTED", {
                    "count": len(interrupts),
                })

                for interrupt in interrupts:
                    interrupt_value = interrupt.value

                    if "action_requests" in interrupt_value:
                        # ---- 第 2 层：HITL 审批中断（interrupt_on 配置）----
                        await record_hitl_approval_requests(
                            DecisionRepository(mysql_storage),
                            thread_id=thread_id,
                            user_id=user_id,
                            action_requests=interrupt_value["action_requests"],
                        )
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "hitl_approval",
                            "action_requests": interrupt_value["action_requests"],
                            "review_configs": interrupt_value.get("review_configs", []),
                            "thread_id": thread_id,
                        })
                        write_debug_log(debug_log, "INTERRUPT_HITL", {
                            "actions": [a["name"] for a in interrupt_value["action_requests"]],
                        })

                    elif interrupt_value.get("type") == "order_info_request":
                        # ---- 第 1 层：数据补充中断（request_order_info 工具）----
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "order_info_supplement",
                            "missing_fields": interrupt_value["missing_fields"],
                            "collected_data": interrupt_value["collected_data"],
                            "thread_id": thread_id,
                        })
                        write_debug_log(debug_log, "INTERRUPT_SUPPLEMENT", {
                            "missing_fields": interrupt_value["missing_fields"],
                        })

                    else:
                        # 未知中断类型，透传原始值
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "unknown",
                            "interrupt_value": str(interrupt_value)[:2000],
                            "thread_id": thread_id,
                        })

                # 兜底：将所有 calling 状态的工具标记为 done
                for dm in display_messages:
                    if dm["role"] == "tool" and dm["tool_status"] == "calling":
                        dm["tool_status"] = "done"

                # 清理空的 assistant 消息
                cleaned = [
                    dm for dm in display_messages
                    if not (dm["role"] == "assistant" and not dm.get("content"))
                ]

                # 保存部分展示消息到 MySQL（中断前的消息状态）
                # 注意：resume 模式下 display_messages 已含历史，保存时会覆盖旧记录
                await agent_loader.save_display_messages(thread_id, cleaned)
                write_debug_log(debug_log, "SAVE_DISPLAY_INTERRUPT", {
                    "thread_id": thread_id,
                    "message_count": len(cleaned),
                })

                # 发送 done 事件标记流结束（前端由此知道可以展示中断 UI）
                yield create_sse_message({
                    "type": "done",
                    "thread_id": thread_id,
                    "content": collected_content,
                    "interrupted": True,
                })
                observation.finish("interrupted")
                return  # ← 结束本次流，等待前端 POST /resume

            # ---- 消息流（token / tool_call / tool_result）----
            if chunk_type != "messages":
                continue

            token, metadata = chunk["data"]
            # messages 流也包含中间件写入的系统消息。仅展示助手输出与工具结果。
            message_type = getattr(token, "type", None)
            if message_type not in {"ai", "AIMessageChunk", "tool", "ToolMessageChunk"}:
                continue
            namespace = chunk.get("ns", ())

            # 判断消息来源：子代理消息优先用 task 工具声明的子代理名称，
            # 否则退回从 namespace 提取（可能是内部运行 ID）。
            is_subagent = any(s.startswith("tools:") for s in namespace)
            if is_subagent:
                source = current_subagent_name(subagent_stack) or extract_subagent_name(namespace)
            else:
                source = "main"

            write_debug_log(debug_log, "RAW_CHUNK", {
                "ns": list(namespace),
                "source": source,
            }, raw_token=token)

            # 处理工具调用
            if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
                for tool_chunk in token.tool_call_chunks:
                    # 工具开始调用
                    if tool_chunk.get('name'):
                        # 使用 LLM 给出的 tool_call_id，保证与后续 ToolMessage.tool_call_id 一致
                        tool_id = tool_chunk.get('id') or str(uuid.uuid4())
                        new_tool = {
                            "id": tool_id,
                            "name": tool_chunk['name'],
                            "args": "",
                            "source": source,
                            "subagent_type": None
                        }
                        tool_calls[tool_id] = new_tool
                        if tool_chunk.get('index') is not None:
                            index_to_call_id[tool_chunk['index']] = tool_id

                        yield create_sse_message({
                            "type": "tool_start",
                            "tool_call_id": tool_id,
                            "tool_name": tool_chunk['name'],
                            "source": source
                        })
                        observation.tool(tool_chunk['name'], "started")

                        # 添加到展示消息
                        display_messages.append({
                            "id": tool_id,
                            "role": "tool",
                            "tool_name": tool_chunk['name'],
                            "args": "",
                            "text": "",
                            "images": [],
                            "source": source,
                            "tool_status": "calling"
                        })

                        write_debug_log(debug_log, "TOOL_START", {
                            "name": tool_chunk['name'],
                            "source": source,
                            "active_tools": len(tool_calls)
                        })

                    # 工具参数
                    if tool_chunk.get('args'):
                        args_str = tool_chunk['args']
                        # args 流式片段通常只带 index 不带 id，用 index 映射回正确工具
                        tool_id = tool_chunk.get('id') or index_to_call_id.get(tool_chunk.get('index'), "")
                        tool = tool_calls.get(tool_id)
                        if tool:
                            tool["args"] += args_str
                            # task 工具：解析被委派的子代理名称，供后续子代理消息标注来源
                            if tool["name"] == "task" and not tool["subagent_type"]:
                                subagent_type = parse_task_subagent_type(tool["args"])
                                if subagent_type:
                                    tool["subagent_type"] = subagent_type
                                    subagent_stack.append(subagent_type)

                        yield create_sse_message({
                            "type": "tool_args",
                            "tool_call_id": tool_id,
                            "args": args_str,
                            "source": source
                        })

                        # 更新展示消息中对应工具的 args（按工具 ID 精确匹配）
                        for dm in reversed(display_messages):
                            if dm["role"] == "tool" and dm["id"] == tool_id:
                                dm["args"] += args_str
                                break

            # 处理工具执行结果
            if message_type in {"tool", "ToolMessageChunk"}:
                tool_name = getattr(token, 'name', '未知工具')
                result_content = getattr(token, 'content', '')

                # 序列化工具结果为结构化数据
                serialized = serialize_tool_result(result_content)

                # 用 ToolMessage 自带的 tool_call_id 精确匹配对应工具，
                # 避免并行/嵌套工具调用时按栈顺序错配。
                tool_call_id = getattr(token, 'tool_call_id', '')
                tool = tool_calls.get(tool_call_id)
                tool_id = tool["id"] if tool else tool_call_id

                # task 工具完成：其声明的子代理已结束，弹出子代理名称栈
                if tool and tool.get("name") == "task" and tool.get("subagent_type") and subagent_stack:
                    subagent_stack.pop()

                yield create_sse_message({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "text": serialized["text"],
                    "images": serialized["images"],
                    "source": source
                })

                # 更新展示消息中对应工具的结果
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id:
                        dm["text"] = serialized["text"]
                        dm["images"] = serialized["images"]
                        dm["tool_status"] = "done"
                        break

                write_debug_log(debug_log, "TOOL_RESULT", {
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "text_preview": serialized["text"][:300],
                    "images_count": len(serialized["images"]),
                    "source": source,
                    "active_tools": len(tool_calls)
                })

                # 发送 tool_end 事件，标记工具调用结束
                yield create_sse_message({
                    "type": "tool_end",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "source": source
                })
                observation.tool(tool_name, "success")

                # 确保展示消息中的工具标记为 done（兜底）
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id and dm["tool_status"] == "calling":
                        dm["tool_status"] = "done"
                        break

            # 处理 AI 文本内容
            # 注意：ToolMessage（type == "tool"）的内容是工具执行结果，已在上面
            # tool_result 事件中处理。这里必须跳过，否则工具结果会被当作 AI 文本重复输出。
            content_text = extract_content_from_token(token)
            observation.token(token, content_text or "")
            has_tool_calls = hasattr(token, 'tool_call_chunks') and token.tool_call_chunks
            is_tool_result = message_type in {"tool", "ToolMessageChunk"}

            if content_text and not has_tool_calls and not is_tool_result:
                collected_content += content_text
                yield create_sse_message({
                    "type": "token",
                    "content": content_text,
                    "source": source
                })
                write_debug_log(debug_log, "TOKEN", {
                    "content": content_text[:200],
                    "source": source
                })

                # 更新展示消息：如果最后一条是 assistant 则追加，否则新建
                if _last_display_is_assistant():
                    display_messages[-1]["content"] += content_text
                    display_messages[-1]["source"] = source
                else:
                    display_messages.append({
                        "id": f"assistant-{uuid.uuid4()}",
                        "role": "assistant",
                        "content": content_text,
                        "source": source
                    })

        # ---- 流正常结束（无中断）----
        # 兜底：将所有 calling 状态的工具标记为 done
        for dm in display_messages:
            if dm["role"] == "tool" and dm["tool_status"] == "calling":
                dm["tool_status"] = "done"

        # 清理空的 assistant 消息
        display_messages = [
            dm for dm in display_messages
            if not (dm["role"] == "assistant" and not dm.get("content"))
        ]

        # 保存完整历史（包含本轮以及已有的用户、助手和子代理消息）。
        all_messages = display_messages
        await agent_loader.save_display_messages(thread_id, all_messages)
        write_debug_log(debug_log, "SAVE_DISPLAY", {
            "thread_id": thread_id,
            "total_count": len(all_messages)
        })

        # 流结束，发送 done 事件
        write_debug_log(debug_log, "STREAM_DONE", {
            "thread_id": thread_id,
            "total_content_len": len(collected_content)
        })

        yield create_sse_message({
            "type": "done",
            "thread_id": thread_id,
            "content": collected_content
        })
        observation.finish("success")

    except AgentIdleTimeout as e:
        # 模型可能在工具已经成功（尤其是审批后的写操作）后卡在最终总结阶段。
        # 此时不能把整轮伪装成失败并诱导用户重试写操作；保存已经产生的消息和
        # 工具结果，以可恢复的“部分完成”结束。完全没有输出时仍按真正超时处理。
        has_completed_tool = any(
            message.get("role") == "tool"
            and message.get("tool_status") == "done"
            and (message.get("text") or message.get("images"))
            for message in display_messages
        )
        if collected_content.strip() or has_completed_tool:
            for message in display_messages:
                if message.get("role") == "tool" and message.get("tool_status") == "calling":
                    message["tool_status"] = "ended"
            notice = (
                "\n\n> 本轮已取得的结果已保存，但最终总结生成超时。"
                "请先核对上方工具结果或决策证据；涉及写操作时不要直接重复提交。"
            )
            recovered_content = collected_content.rstrip() + notice
            display_messages.append({
                "id": f"assistant-{uuid.uuid4()}",
                "role": "assistant",
                "content": notice.strip(),
                "source": "system",
            })
            await agent_loader.save_display_messages(thread_id, [
                message for message in display_messages
                if not (message.get("role") == "assistant" and not message.get("content"))
            ])
            write_debug_log(debug_log, "STREAM_PARTIAL_TIMEOUT", {
                "thread_id": thread_id,
                "content_len": len(collected_content),
                "has_completed_tool": has_completed_tool,
            })
            yield create_sse_message({
                "type": "done",
                "thread_id": thread_id,
                "content": recovered_content,
                "partial": True,
                "reason": "idle_timeout",
            })
            observation.finish("failure")
            return
        if display_messages:
            await agent_loader.save_display_messages(thread_id, display_messages)
        write_debug_log(debug_log, "STREAM_ERROR", {"error": str(e)})
        yield create_sse_message({"type": "error", "message": str(e)})
        observation.finish("failure")
    except Exception as e:
        if display_messages:
            # 普通请求和审批恢复都保存已取得的消息，避免成功工具结果在异常后丢失。
            await agent_loader.save_display_messages(thread_id, display_messages)
        write_debug_log(debug_log, "STREAM_ERROR", {"error": str(e)})
        yield create_sse_message({
            "type": "error",
            "message": str(e) or "请求处理失败，请稍后重试。"
        })
        observation.finish("failure")
    finally:
        if isinstance(agent_graph, GraphLease):
            await agent_graph.release()
        # 客户端中途断开时异步生成器会直接关闭，也要归还活跃请求计数。
        observation.finish("cancelled")


# ============================================================
# API 端点
# ============================================================

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user_id: str = Depends(require_user)):
    """
    流式对话接口

    接收用户消息，返回 SSE 流式响应
    支持实时显示 AI 生成的内容和工具调用信息
    检测到 Human-in-the-Loop 中断时会发送 interrupt 事件
    """
    if request.thread_id:
        thread_id = request.thread_id
        await require_thread(thread_id, user_id)
        if await agent_loader.has_pending_interrupt(thread_id):
            raise HTTPException(409, detail="当前会话有待处理的确认事项，请先完成审批或补充信息。")

    # 在创建会话、记录消息、分配沙箱和调用业务 Agent 之前检查输入。
    try:
        direct_response = await get_input_guard().check_text(request.message, user_id=user_id)
    except InputSafetyError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None

    if not request.thread_id:
        thread_id = str(uuid.uuid4())
        try:
            await mysql_storage.register_thread(thread_id, user_id, request.project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        await agent_loader.save_display_messages(thread_id, [{
            "id": f"user-{uuid.uuid4()}",
            "role": "user",
            "content": request.message,
        }])

    return StreamingResponse(
        stream_chat_response(
            message=request.message, thread_id=thread_id, user_id=user_id,
            direct_response=direct_response,
        ),
        media_type="text/event-stream",  # text/json, text/html, image/jpg
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/chat/{thread_id}/resume")
async def chat_resume(thread_id: str, request: ResumeRequest, user_id: str = Depends(require_thread)):
    """
    中断恢复接口

    当中断发生后，前端收集用户决策/补充数据，通过此端点恢复 Agent 执行。
    request.resume 的格式取决于中断类型：
    - 数据补充: {"supplement": "用户输入的补充信息"}
    - HITL 审批: {"decisions": [{"type": "approve"}]} 或 [{"type": "reject"}]
    """
    try:
        await get_input_guard().check_resume(request.resume, user_id=user_id)
    except InputSafetyError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None

    decisions = request.resume.get("decisions")
    if decisions is not None:
        try:
            await record_hitl_resume_events(
                DecisionRepository(mysql_storage),
                thread_id=thread_id,
                user_id=user_id,
                decisions=decisions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    return StreamingResponse(
        stream_chat_response(thread_id=thread_id, resume_data=request.resume, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/chat/{thread_id}")
async def get_chat_state(thread_id: str, user_id: str = Depends(require_thread)):
    """获取会话状态"""
    try:
        messages = await agent_loader.get_current_messages(thread_id)

        message_list = []
        for i, msg in enumerate(messages):
            message = Message(
                id=f"msg-{i}",
                role=msg.get("role", "assistant"),
                content=msg.get("content", ""),
                created_at=datetime.now(),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
            message_list.append(message)

        return ChatResponse(
            thread_id=thread_id,
            messages=message_list
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/{thread_id}/history")
async def get_chat_history(
    thread_id: str,
    limit: int = Query(50, ge=1, le=100)
):
    """获取会话历史状态列表"""
    try:
        states = await agent_loader.get_state_history(thread_id, limit)

        return {
            "thread_id": thread_id,
            "states": [
                {
                    "config": state.config,
                    "values": state.values,
                    "created_at": state.created_at,
                    "parent_config": state.parent_config
                }
                for state in states
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
