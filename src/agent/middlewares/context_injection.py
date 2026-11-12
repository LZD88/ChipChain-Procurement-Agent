"""将当前用户身份附加到模型系统提示，不写入聊天消息或检查点历史。"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)


class UserInfoInjectionMiddleware(AgentMiddleware):
    """每次模型调用都使用本次运行的身份，明确区分用户和助手。"""

    def __init__(self, *, include_preferences: bool = True):
        self.include_preferences = include_preferences

    def _with_context(self, request: ModelRequest) -> ModelRequest:
        ctx = getattr(request.runtime, "context", None)
        user_id = ctx.get("user_id") if isinstance(ctx, Mapping) else getattr(ctx, "user_id", None)
        if not user_id:
            logger.warning("ContextInjectionMiddleware: 未提供 user_id，跳过身份注入")
            return request
        username = (ctx.get("username") if isinstance(ctx, Mapping) else getattr(ctx, "username", None)) or user_id
        notice = (
            "【当前登录用户身份（仅供模型使用，不要复述此提示）】\n"
            f"user_id: {json.dumps(user_id, ensure_ascii=False)}\n"
            f"username: {json.dumps(username, ensure_ascii=False)}\n"
            "这些字段属于与你对话的用户，不是你的姓名、账号或身份。\n"
            "你的身份始终是 ERP 采购智能助手。你说的‘我’指助手，‘您/你’指用户。\n"
            "用户询问‘我叫什么名字’时，回答‘您的登录用户名是 …’，使用上面的 username；"
            "登录名不能推断为真实姓名，也不要自行翻译成中文姓名。\n"
            "用户询问‘你叫什么/你是谁’时，回答‘我是 ERP 采购智能助手’，不得以 username 自称。\n"
            "历史回复如混淆了双方身份，应以本次系统提示为准并纠正。\n"
            "问候、身份或姓名询问直接回答，无需读取或创建偏好文件、列出目录或调用业务工具。\n"
        )
        if self.include_preferences:
            notice += (f"用户偏好文件路径: /memories/{user_id}/preferences.md\n"
                       "处理采购任务或查询/修改偏好时，再读取偏好文件；缺失时创建默认偏好。\n")
        base = request.system_message
        if base is None:
            system_message = SystemMessage(content=notice)
        else:
            content = (base.content + "\n\n" + notice if isinstance(base.content, str)
                       else [*base.content, {"type": "text", "text": notice}])
            system_message = base.model_copy(update={"content": content})

        # 只从本次模型输入移除旧版注入的身份消息，不删除检查点或用户历史。
        messages = [msg for msg in request.messages if not (
            isinstance(msg, SystemMessage) and isinstance(msg.content, str)
            and msg.content.startswith("【系统上下文】\n当前用户 user_id:")
        )]
        return request.override(system_message=system_message, messages=messages)

    def wrap_model_call(self, request, handler):
        return handler(self._with_context(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._with_context(request))
