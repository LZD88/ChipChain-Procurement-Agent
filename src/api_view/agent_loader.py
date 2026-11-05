"""
Agent 加载器

使用 Graph Factory 模式管理 DeepAgent 实例，实现用户级沙箱隔离。
启动时预计算 MCP 工具和图表工具，每次请求基于 per-user 沙箱创建 agent graph。
"""

from typing import Optional, List, Dict, Any
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from agent.main_agent import create_main_agent, precompute_agent_context, PrecomputedContext
from agent.backends import sandbox_manager
from agent.mysql_storage import mysql_storage

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
    采用 Graph Factory 模式：每次请求基于 per-user 沙箱创建 agent graph。
    """

    def __init__(self):
        self._initialized = False
        self._precomputed: PrecomputedContext | None = None

    async def initialize(self):
        """
        启动时初始化：MySQL + 沙箱管理器 + 预计算 + 预热沙箱。

        预计算（MCP 工具和图表工具）执行一次，所有请求复用。
        预热沙箱在启动阶段完成（~15s），首个用户连接时零等待。
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

            # 4. 预热第一个沙箱（阻塞 ~15s，首个用户无需等待创建）
            await sandbox_manager.pre_warm()

            self._initialized = True
        except Exception:
            await mysql_storage.close()
            raise

    async def get_agent_for_user(self, user_id: str):
        """
        获取 per-user agent graph。

        每次请求调用：获取/创建 per-user 沙箱 → 创建 agent graph。
        同一用户的多个 thread 共享沙箱（缓存命中 < 0.1s）。

        Args:
            user_id: 用户唯一标识。

        Returns:
            CompiledStateGraph: 该用户的 agent graph。
        """
        sandbox_backend = await sandbox_manager.ensure_sandbox_for_user(user_id)
        config = self.create_config(user_id=user_id)
        return await create_main_agent(
            config,
            sandbox_backend=sandbox_backend,
            precomputed=self._precomputed,
        )
    async def shutdown(self) -> None:
        """应用关闭时清理所有沙箱和 MySQL 连接池。"""
        try:
            await sandbox_manager.shutdown()
        finally:
            await mysql_storage.close()
            self._initialized = False
            self._precomputed = None

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
