"""
历史记录 API 模块

提供会话列表查询、会话消息历史获取和会话删除接口
"""

import logging
import re
import uuid
from typing import Any, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from agent.schema import (
    Session,
    SessionListResponse,
    SessionMessagesResponse,
    DeleteSessionResponse,
    Message,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    SessionUpdateRequest,
)
from api_view.agent_loader import agent_loader
from api_view.auth import require_same_origin_request, require_thread, require_user
from agent.mysql_storage import mysql_storage

logger = logging.getLogger(__name__)


# 创建路由
router = APIRouter(dependencies=[Depends(require_same_origin_request)])


# ============================================================
# 辅助函数：从 LangChain 消息对象中提取数据
# ============================================================

def get_message_attr(msg: Any, attr: str, default: Any = None) -> Any:
    """从消息对象中获取属性值（兼容 dict 和对象格式）"""
    if isinstance(msg, dict):
        return msg.get(attr, default)
    else:
        return getattr(msg, attr, default)


def get_message_role(msg: Any) -> str:
    """获取消息的角色: user / assistant / tool"""
    role = get_message_attr(msg, 'role') or get_message_attr(msg, 'type')
    if role:
        if role in {'human', 'HumanMessageChunk'}: return 'user'
        if role in {'ai', 'AIMessageChunk'}: return 'assistant'
        if role == 'ToolMessageChunk': return 'tool'
        return role

    msg_type = type(msg).__name__
    if msg_type == 'HumanMessage': return 'user'
    elif msg_type == 'AIMessage': return 'assistant'
    elif msg_type == 'ToolMessage': return 'tool'
    elif msg_type == 'SystemMessage': return 'system'
    return get_message_attr(msg, 'type', 'unknown')


def get_message_content(msg: Any) -> str:
    """
    从消息对象中提取纯文本内容
    处理 content 可能是 str / list / dict 的情况
    """
    if isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = getattr(msg, "content", "")

    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif "text" in item:
                    text_parts.append(str(item["text"]))
                elif "content" in item:
                    text_parts.append(str(item["content"]))
            else:
                text_parts.append(str(item))
        return ''.join(text_parts)
    elif isinstance(content, dict):
        return content.get("text", str(content))
    else:
        return str(content) if content else ""


def extract_images_from_content(content) -> List[str]:
    """
    从消息 content 中提取图片 URL 列表
    支持结构化列表和 markdown 图片语法
    """
    images = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "image_url":
                    image_url = item.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url:
                        images.append(url)
                elif item.get("type") == "image":
                    data = item.get("data") or item.get("image_data", "")
                    if data:
                        images.append(data)
    elif isinstance(content, str):
        # 提取 markdown 图片语法
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', content):
            url = match.group(1)
            if url not in images:
                images.append(url)
        # 提取 data:image base64
        for match in re.finditer(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', content):
            url = match.group(0)
            if url not in images:
                images.append(url)

    return images


def serialize_messages_from_checkpoint(messages: list) -> list:
    """
    将 MySQL checkpoint 中存储的 LangChain 消息列表
    转换为前端需要的 Message 格式

    改进点：
    1. AIMessage 若有 tool_calls，拆分为「AI 文本」+「Tool 占位」两条消息，
       使前端展示与流式过程一致（文本和工具调用交替出现）
    2. ToolMessage 优先匹配前一条 AIMessage 中同名的 tool_calls 占位，
       填充 text/images 字段，避免重复创建 tool 消息

    Args:
        messages: LangChain 消息对象列表

    Returns:
        list: Message schema 兼容的字典列表
    """
    result = []
    for i, msg in enumerate(messages):
        role = get_message_role(msg)
        content = get_message_content(msg)

        if role == "assistant":
            # 先添加 AI 文本部分
            if content:
                result.append({
                    "id": f"msg-{i}",
                    "role": "assistant",
                    "content": content,
                    "source": "main",
                    "created_at": datetime.now(),
                })

            # 再为每个 tool_call 创建占位 tool 消息
            tool_calls = get_message_attr(msg, "tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    tc_name = tc.get("name", "未知工具") if isinstance(tc, dict) else getattr(tc, "name", "未知工具")
                    tc_args = ""
                    if isinstance(tc, dict):
                        tc_args = str(tc.get("args", ""))
                    else:
                        args_val = getattr(tc, "args", {})
                        tc_args = str(args_val) if args_val else ""

                    result.append({
                        "id": f"msg-{i}-tc-{tc_name}",
                        "role": "tool",
                        "tool_name": tc_name,
                        "args": tc_args,
                        "text": "",
                        "images": [],
                        "source": "main",
                        "tool_status": "calling",
                        "created_at": datetime.now(),
                    })

        elif role == "tool":
            # ToolMessage：尝试匹配前面的 tool 占位并填充结果
            tool_name = get_message_attr(msg, "name") or "未知工具"
            raw_content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            images = extract_images_from_content(raw_content)

            # 从后向前查找同名的空 tool 消息（calling 状态），填充结果
            found = False
            for prev in reversed(result):
                if (prev["role"] == "tool"
                        and prev["tool_name"] == tool_name
                        and prev["tool_status"] == "calling"
                        and not prev["text"]):
                    prev["text"] = content
                    prev["images"] = images
                    prev["tool_status"] = "done"
                    found = True
                    break

            if not found:
                result.append({
                    "id": f"msg-{i}",
                    "role": "tool",
                    "tool_name": tool_name,
                    "args": "",
                    "text": content,
                    "images": images,
                    "source": "main",
                    "tool_status": "done",
                    "created_at": datetime.now(),
                })

        elif role == "user":
            result.append({
                "id": f"msg-{i}",
                "role": "user",
                "content": content,
                "created_at": datetime.now(),
            })

    return result


# ============================================================
# API 路由
# ============================================================

@router.get("/history", response_model=SessionListResponse)
async def get_sessions(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    user_id: str = Depends(require_user),
):
    """获取会话列表"""
    try:
        all_thread_ids = await agent_loader.get_all_thread_ids(user_id)
        metadata = await mysql_storage.get_thread_metadata(user_id)

        sessions = []
        for thread_id in all_thread_ids:
            try:
                # 优先读取流式过程中保存的完整展示消息（含子代理消息），
                # 缺失时回退到 checkpoint 序列化（兼容旧会话数据）。
                serialized = await agent_loader.get_display_messages(thread_id)
                if not serialized:
                    messages = await agent_loader.get_current_messages(thread_id)
                    serialized = serialize_messages_from_checkpoint(messages)

                # 兼容修复前遗留的空记录：没有用户消息也没有 checkpoint 的会话不展示。
                if not serialized:
                    continue

                first_user_msg = next(
                    (m for m in serialized if m.get("role") == "user"),
                    None
                )
                if first_user_msg:
                    first_content = first_user_msg.get("content", "") or ""
                    title = first_content[:50] if first_content else "新会话"
                    if len(first_content) > 50:
                        title += "..."
                else:
                    title = "新会话"

                message_count = len(serialized)
                updated_at = await agent_loader.get_session_updated_at(thread_id)

                thread_meta = metadata.get(thread_id, {})
                sessions.append(Session(
                    thread_id=thread_id,
                    title=thread_meta.get("custom_title") or title,
                    created_at=thread_meta.get("created_at") or updated_at,
                    updated_at=updated_at,
                    message_count=message_count,
                    project_id=thread_meta.get("project_id"),
                    pinned=bool(thread_meta.get("pinned", False)),
                ))
            except Exception:
                logger.warning("跳过无法读取的会话：thread=%s", thread_id, exc_info=True)
                continue

        sessions.sort(key=lambda x: (x.pinned, x.updated_at), reverse=True)

        total = len(sessions)
        start = (page - 1) * limit
        end = start + limit
        paginated_sessions = sessions[start:end]

        return SessionListResponse(
            sessions=paginated_sessions,
            total=total,
            page=page,
            limit=limit
        )

    except Exception as e:
        logger.exception("获取会话列表失败：user=%s", user_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{thread_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(thread_id: str, user_id: str = Depends(require_thread)):
    """
    获取会话消息历史

    优先从 MySQL display_messages 集合读取（包含子代理消息），
    如不存在则回退到 checkpoint 序列化（兼容旧会话数据）。
    """
    try:
        # 优先从 MySQL 读取流式过程中保存的完整展示消息
        serialized = await agent_loader.get_display_messages(thread_id)

        if not serialized:
            # 回退到 checkpoint（兼容旧数据，不含子代理消息）
            messages = await agent_loader.get_current_messages(thread_id)
            serialized = serialize_messages_from_checkpoint(messages)

        message_list = []
        for item in serialized:
            message = Message(
                id=item["id"],
                role=item["role"],
                content=item.get("content", ""),
                created_at=item.get("created_at", datetime.now()),
                tool_calls=item.get("tool_calls"),
                tool_call_id=item.get("tool_call_id"),
                source=item.get("source"),
                tool_name=item.get("tool_name"),
                tool_status=item.get("tool_status"),
                text=item.get("text"),
                images=item.get("images"),
                args=item.get("args"),
            )
            message_list.append(message)

        return SessionMessagesResponse(
            thread_id=thread_id,
            messages=message_list
        )

    except Exception as e:
        logger.exception("获取会话消息失败：thread=%s", thread_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history/{thread_id}", response_model=DeleteSessionResponse)
async def delete_session(thread_id: str, user_id: str = Depends(require_thread)):
    """删除会话"""
    try:
        success = await agent_loader.delete_session(thread_id)

        if success:
            return DeleteSessionResponse(success=True, message="会话已删除")
        else:
            return DeleteSessionResponse(success=False, message="删除会话失败")

    except Exception as e:
        logger.exception("删除会话失败：thread=%s", thread_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/history/{thread_id}")
async def update_session(thread_id: str, body: SessionUpdateRequest,
                         user_id: str = Depends(require_thread)):
    """更新会话名称、置顶状态或所属项目。"""
    title = body.title.strip() if body.title is not None else None
    if body.title is not None and not title:
        raise HTTPException(status_code=422, detail="会话名称不能为空")
    success = await mysql_storage.update_thread_metadata(
        thread_id,
        user_id,
        title=title,
        pinned=body.pinned,
        project_id_marker="project_id" in body.model_fields_set,
        project_id=body.project_id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="会话或项目不存在")
    return {"success": True, "thread_id": thread_id}


@router.get("/projects")
async def get_projects(user_id: str = Depends(require_user)):
    """获取当前账号的会话项目。"""
    projects = await mysql_storage.list_projects(user_id)
    return {"projects": projects}


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreateRequest, user_id: str = Depends(require_user)):
    """创建会话项目；项目只用于组织会话，不改变业务权限。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    project_id = str(uuid.uuid4())
    await mysql_storage.create_project(project_id, user_id, name)
    return {"project_id": project_id, "name": name}


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdateRequest,
                         user_id: str = Depends(require_user)):
    """修改当前账号拥有的会话项目名称。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    if not await mysql_storage.update_project(project_id, user_id, name):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"success": True, "project_id": project_id, "name": name}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, user_id: str = Depends(require_user)):
    """删除项目，保留并解除项目内会话的归属。"""
    if not await mysql_storage.delete_project(project_id, user_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "success": True,
        "project_id": project_id,
        "message": "项目已删除，原有会话已移至独立会话",
    }
