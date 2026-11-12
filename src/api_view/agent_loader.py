"""
Agent 加载器

按请求能力选择只读轻量 graph 或用户级沙箱 DeepAgent。
启动时预计算工具；完整任务按需准备沙箱，关闭分流时认证后提前准备。
"""

from typing import Optional, List, Dict, Any
import asyncio
from contextlib import suppress
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from deepagents import DeepAgentState
from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END

from agent.main_agent import create_main_agent, precompute_agent_context, PrecomputedContext, agent_fingerprint
from agent.backends import sandbox_manager
from agent.mysql_storage import mysql_storage
from api_view.runtime_preparation import RuntimePreparation
from agent.lightweight_agent import create_lightweight_agent
from agent.runtime_config import RuntimeConfig
from api_view.observability import PREPARATION_SCHEDULE

logger = logging.getLogger(__name__)


# CheckpointTuple 到 StateSnapshot 的轻量适配器
@dataclass
class _StateSnapshot:
    values: dict
    config: dict
    created_at: datetime | str | None = None
    parent_config: dict | None = None
    metadata: dict | None = None


class AgentLoader:
    """
    Agent 加载器单例类

    负责管理 Agent 生命周期、MySQL 连接、沙箱管理和会话相关操作。
    用户 graph 按沙箱代际缓存，运行期间持有租用。
    """

    def __init__(self):
        self._initialized = False
        self._precomputed: PrecomputedContext | None = None
        self._prewarm_task: asyncio.Task | None = None
        self._runtime: RuntimePreparation | None = None
        self._lightweight_graph = None

    async def initialize(self):
        """
        启动时初始化：MySQL + 沙箱管理器 + 预计算 + 预热沙箱。

        预计算（MCP 工具和图表工具）执行一次，所有请求复用。
        沙箱预热作为后台任务启动；API 不等待预热完成即可开始监听。
        """
        if self._initialized and self._precomputed is not None:
            return

        try:
            # 1. MySQL 连接池、checkpoint 表及应用表
            await mysql_storage.initialize()
            logger.debug("MySQL 存储已就绪")

            # 2. 沙箱管理器复用同一存储
            await sandbox_manager.initialize(mysql_storage)

            # 3. 预计算 MCP 工具 + 图表工具
            self._precomputed = await precompute_agent_context()
            self._preparation_runtime()

            self._initialized = True
            # 4. 沙箱预热放到后台。OpenSandbox 变慢或暂时异常时，API 仍应先开始
            # 监听并返回健康状态，不能让整个聊天页面卡在连接阶段。
            if self._prewarm_task is None or self._prewarm_task.done():
                self._prewarm_task = asyncio.create_task(
                    self._prewarm_safely(), name="sandbox-prewarm",
                )
        except Exception:
            await mysql_storage.close()
            raise

    async def _prewarm_safely(self) -> None:
        try:
            await sandbox_manager.pre_warm()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("后台预热沙箱失败；API 保持可用，首次请求时将按需恢复")

    def _preparation_runtime(self):
        if self._runtime is None:
            self._runtime = RuntimePreparation(self._build_user_graph,
                                               lambda: agent_fingerprint(self._precomputed))
        return self._runtime

    def schedule_preparation(self, user_id):
        if self._initialized:
            try:
                if RuntimeConfig.from_env().request_routing_enabled:
                    # Login is not evidence that the user needs a sandbox.
                    PREPARATION_SCHEDULE.labels("deferred_routing").inc()
                    return
                self._preparation_runtime().schedule(user_id)
            except Exception:
                logger.exception("后台环境调度失败，前台仍可按需准备")

    async def logout_preparation(self, user_id):
        if self._runtime is not None:
            await self._runtime.logout(user_id)

    async def get_agent_for_user(self, user_id: str, endpoint="chat"):
        return await self._preparation_runtime().acquire(user_id, endpoint)

    def get_lightweight_agent(self):
        if self._lightweight_graph is None:
            self._lightweight_graph = create_lightweight_agent(
                self._precomputed, checkpointer=mysql_storage.checkpointer,
            )
        return self._lightweight_graph

    async def has_pending_interrupt(self, thread_id):
        """Do not replace an interrupted graph with a different request path."""
        checkpoint = await mysql_storage.checkpointer.aget_tuple(self.create_config(thread_id))
        return bool(checkpoint and any(
            # Stored checkpoint channel; inspecting it requires no sandbox graph.
            channel == "__interrupt__" for _task, channel, _value in checkpoint.pending_writes or []
        ))

    async def _build_user_graph(self, user_id, sandbox_backend):
        """Compile only after acquiring a compatible sandbox for this user."""
        config = self.create_config(user_id=user_id)
        return await create_main_agent(
            config,
            sandbox_backend=sandbox_backend,
            precomputed=self._precomputed,
        )

    def get_direct_response_agent(self, response: str):
        """护栏已批准的固定回复复用会话 checkpoint，无需创建沙箱或调用模型。"""
        async def reply(_state):
            return {"messages": [AIMessage(content=response)]}

        graph = StateGraph(DeepAgentState)
        graph.add_node("guardrail_reply", reply)
        graph.add_edge(START, "guardrail_reply")
        graph.add_edge("guardrail_reply", END)
        return graph.compile(checkpointer=mysql_storage.checkpointer)

    async def shutdown(self) -> None:
        """应用关闭时清理所有沙箱和 MySQL 连接池。"""
        try:
            if self._runtime is not None:
                await self._runtime.close()
                self._runtime = None
            if self._prewarm_task and not self._prewarm_task.done():
                self._prewarm_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._prewarm_task
            self._prewarm_task = None
            await sandbox_manager.shutdown()
        finally:
            await mysql_storage.close()
            self._initialized = False
            self._precomputed = None
            self._lightweight_graph = None

    def create_config(
        self,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id or str(uuid.uuid4()),
                **({"user_id": user_id} if user_id is not None else {}),
                **kwargs
            }
        }

    async def get_state_history(
        self,
        thread_id: str,
        limit: int = 50
    ) -> List[Any]:
        """直接通过 checkpointer 查询，不依赖 agent graph。"""
        config = self.create_config(thread_id)
        states = []
        async for checkpoint_tuple in mysql_storage.checkpointer.alist(config, limit=limit):
            snapshot = _StateSnapshot(
                values=checkpoint_tuple.checkpoint.get("channel_values", {}),
                config=checkpoint_tuple.config,
                created_at=checkpoint_tuple.checkpoint.get("ts"),
                parent_config=checkpoint_tuple.parent_config,
                metadata=checkpoint_tuple.metadata,
            )
            states.append(snapshot)
        return states

    async def get_current_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """直接通过 checkpointer 查询，不依赖 agent graph。"""
        config = self.create_config(thread_id)
        try:
            checkpoint_tuple = await mysql_storage.checkpointer.aget_tuple(config)
            if checkpoint_tuple and checkpoint_tuple.checkpoint:
                return checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
        except Exception:
            logger.exception("获取会话消息失败：thread=%s", thread_id)
        return []

    async def get_all_thread_ids(self, user_id: str) -> List[str]:
        return await mysql_storage.get_all_thread_ids(user_id)

    async def get_session_updated_at(self, thread_id: str) -> datetime:
        return await mysql_storage.get_session_updated_at(thread_id)

    async def delete_session(self, thread_id: str) -> bool:
        try:
            await mysql_storage.delete_session(thread_id)
            return True
        except Exception:
            logger.exception("删除会话失败：thread=%s", thread_id)
            return False

    async def save_display_messages(self, thread_id: str, messages: List[Dict[str, Any]]) -> bool:
        try:
            await mysql_storage.save_display_messages(thread_id, messages)
            return True
        except Exception:
            logger.exception("保存展示消息失败：thread=%s", thread_id)
            return False

    async def get_display_messages(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            return await mysql_storage.get_display_messages(thread_id)
        except Exception:
            logger.exception("读取展示消息失败：thread=%s", thread_id)
            return None


# 全局单例实例
agent_loader = AgentLoader()
