"""
自定义数据结构定义
包含运行时身份与 Web API 请求/响应模型。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import Field, BaseModel


@dataclass
class ProcurementContext:
    """
    运行时上下文，由调用方在 invoke 时传入。
    用于传递当前用户身份等基础信息。
    """
    user_id: str                # 必填，用户唯一标识
    username: str               # 必填，用户姓名/登录名
    guardrail_response: str | None = None  # 仅由服务端护栏设置，不持久化到状态


# ============================================================
# 对话相关模型
# ============================================================

class ChatRequest(BaseModel):
    """对话请求模型"""
    message: str = Field(..., description="用户消息")
    thread_id: Optional[str] = Field(None, description="会话 ID，为空则创建新会话")
    project_id: Optional[str] = Field(None, max_length=64, description="新会话所属项目；已有会话忽略此字段")
    model_config = {"extra": "forbid"}  # 用户身份仅从登录会话读取。


class Message(BaseModel):
    """消息模型"""
    id: str = Field(..., description="消息唯一标识")
    role: str = Field(..., description="消息角色: user/assistant/tool")
    content: str = Field("", description="消息内容")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="工具调用信息")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID")
    source: Optional[str] = Field(None, description="消息来源: main 或子代理名称")
    # 工具消息专属字段
    tool_name: Optional[str] = Field(None, description="工具名称")
    tool_status: Optional[str] = Field(None, description="工具状态: calling / done")
    text: Optional[str] = Field(None, description="工具结果文本")
    images: Optional[List[str]] = Field(None, description="工具结果图片列表")
    args: Optional[str] = Field(None, description="工具调用参数")


class ChatResponse(BaseModel):
    """对话响应模型"""
    thread_id: str = Field(..., description="会话 ID")
    messages: List[Message] = Field(default_factory=list, description="消息列表")


# ============================================================
# 历史记录相关模型
# ============================================================

class Session(BaseModel):
    """会话模型"""
    thread_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最后更新时间")
    message_count: int = Field(0, description="消息数量")
    project_id: Optional[str] = Field(None, description="所属项目 ID")
    pinned: bool = Field(False, description="是否置顶")


class ChatProject(BaseModel):
    """会话项目。"""
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    model_config = {"extra": "forbid"}


class ProjectUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    model_config = {"extra": "forbid"}


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=80)
    pinned: Optional[bool] = None
    project_id: Optional[str] = Field(None, max_length=64)
    model_config = {"extra": "forbid"}


class SessionListResponse(BaseModel):
    """会话列表响应模型"""
    sessions: List[Session] = Field(default_factory=list, description="会话列表")
    total: int = Field(0, description="总会话数")
    page: int = Field(1, description="当前页码")
    limit: int = Field(20, description="每页数量")


class SessionMessagesResponse(BaseModel):
    """会话消息历史响应模型"""
    thread_id: str = Field(..., description="会话 ID")
    messages: List[Message] = Field(default_factory=list, description="消息列表")


class DeleteSessionResponse(BaseModel):
    """删除会话响应模型"""
    success: bool = Field(True, description="是否成功")
    message: str = Field("会话已删除", description="响应消息")
