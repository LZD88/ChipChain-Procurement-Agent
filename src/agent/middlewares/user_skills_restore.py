"""
技能恢复中间件

在每个 Agent 运行周期开始前，将 StoreBackend 中持久化的技能
恢复到沙箱 /skills/{scope}/{skill_name}/ 路径下，使子 Agent 可以通过
渐进式披露发现和使用。

与 SkillsSyncMiddleware 分工：
  - SkillsSyncMiddleware: 本地 src/skills/ → 沙箱（预置技能）
  - UserSkillsRestoreMiddleware: StoreBackend → 沙箱（持久化技能）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from langchain.agents.middleware import AgentMiddleware
import hashlib
from agent.backends.sandbox_sync import sync_lock, generation
from api_view.observability import PREPARATION_STAGE, preparation_measure


class UserSkillsRestoreMiddleware(AgentMiddleware):
    """从 StoreBackend 恢复持久化技能到沙箱的中间件。"""

    def __init__(self, backend, skills_namespace) -> None:
        """
        Args:
            backend: OpenSandboxBackend 实例，负责文件上传。
            skills_namespace: StoreBackend 中技能的命名空间元组。
        """
        super().__init__()
        self.backend = backend
        self.namespace = skills_namespace
        self._sync_lock = sync_lock(backend)
        self._generation = generation(backend)
        self._hashes = {}

    async def abefore_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """运行前：从 StoreBackend 读取持久化技能，上传到沙箱。"""
        await self.sync(runtime.store)
        return None

    async def sync(self, store):
        async with self._sync_lock:
            with preparation_measure(PREPARATION_STAGE, "sync"):
                current = generation(self.backend)
                if self._generation != current:
                    self._hashes.clear()
                    self._generation = current
                files = await self._collect_skills(store)
                hashes = {path: hashlib.sha256(content).hexdigest() for path, content in files}
                changed = [(path, content) for path, content in files
                           if self._hashes.get(path) != hashes[path]]
                if changed:
                    results = await self.backend.aupload_files(changed)
                    uploaded = {result.path for result in results if result.error is None}
                    if any(path not in uploaded for path, _ in changed):
                        raise RuntimeError("持久化技能恢复失败")
                self._hashes.update(hashes)

    def before_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """同步版本：不执行操作（技能恢复仅支持异步）。"""
        return None

    # --------------------- 内部方法 ---------------------
    async def _collect_skills(self, store) -> List[Tuple[str, bytes]]:
        """
        从 StoreBackend 收集所有持久化技能文件。

        StoreBackend key 格式: /{scope}/{skill_name}/...
        沙箱目标路径: /skills/{scope}/{skill_name}/...

        Returns:
            (沙箱路径, 文件内容字节) 的列表。
        """
        files: List[Tuple[str, bytes]] = []

        try:
            items = await store.asearch(self.namespace)
        except Exception:
            return files

        for item in items:
            key = str(item.key).lstrip("/")

            # key 格式: {scope}/{skill_name}/...
            # 映射到: /skills/{scope}/{skill_name}/...
            parts = key.split("/", 1)
            if len(parts) != 2:
                continue
            scope, rest = parts
            sandbox_path = f"/skills/{scope}/{rest}"

            content = item.value
            if isinstance(content, dict):
                content = content.get("content", "")
            if isinstance(content, str):
                content = content.encode("utf-8")
            if not content:
                continue

            files.append((sandbox_path, content))

        return files
