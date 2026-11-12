"""
技能同步中间件

在每个 Agent 运行周期开始前，将本地 src/skills/ 下的技能文件与沙箱同步。
检测到变化时，向对话中插入系统通知，提醒 Agent 有新技能可用。
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from shlex import quote
from typing import Any, Dict, List, Optional, Tuple

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from deepagents.backends.sandbox import BaseSandbox  # 使用通用的沙箱后端协议

from agent.config import LOCAL_SKILLS_DIR, SANDBOX_SKILLS_ROOT
from agent.backends.sandbox_sync import sync_lock, generation
from api_view.observability import PREPARATION_STAGE, preparation_measure


class SkillsSyncMiddleware(AgentMiddleware):
    """技能文件同步中间件，依赖沙箱后端的文件操作能力。"""

    def __init__(self, backend: BaseSandbox) -> None:
        super().__init__()
        self.backend = backend
        # 缓存本地文件哈希，避免重复同步
        self._last_hashes: Dict[str, str] = {}
        self._sync_lock = sync_lock(backend)
        self._generation = generation(backend)

    # --------------------- 钩子 ---------------------
    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        # 同步版本不执行操作（技能同步仅支持异步，与 UserSkillsRestoreMiddleware 一致）。
        return None

    async def abefore_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        new_skills = await self._sync_files()
        if new_skills:
            return self._make_notification(new_skills)
        return None

    # --------------------- 文件同步 ---------------------
    async def _sync_files(self) -> List[str]:
        async with self._sync_lock:
            with preparation_measure(PREPARATION_STAGE, "sync"):
                current = generation(self.backend)
                if self._generation != current:
                    self._last_hashes.clear()
                    self._generation = current
                return await self._sync_files_locked()

    async def _sync_files_locked(self) -> List[str]:
        """扫描本地技能目录，将新增/修改的文件上传到沙箱。

        Returns:
            发生变化的技能目录或根目录参考文件名称列表。
        """
        local_skills_dir = Path(LOCAL_SKILLS_DIR)
        if not local_skills_dir.exists():
            return []

        updated_skills: List[str] = []
        files_to_upload: List[Tuple[str, bytes]] = []
        pending_hashes: Dict[str, str] = {}

        # 根目录的共享参考资料也必须同步，例如 analyis_chart_params.md。
        for local_file in sorted(local_skills_dir.rglob("*")):
            if not local_file.is_file():
                continue
            relative_path = local_file.relative_to(local_skills_dir)
            cache_key = relative_path.as_posix()
            sandbox_path = f"{SANDBOX_SKILLS_ROOT}/{cache_key}"
            local_content = local_file.read_bytes()
            local_hash = hashlib.md5(local_content).hexdigest()

            # 本地哈希未变，跳过
            if self._last_hashes.get(cache_key) == local_hash:
                continue

            # 对比沙箱文件（先 test -f 避免 download_files 对 404 打 ERROR）
            check = await self.backend.aexecute(f"test -f {quote(sandbox_path)}")
            if check.exit_code == 0:
                try:
                    results = await self.backend.adownload_files([sandbox_path])
                    if results and results[0].content is not None and not results[0].error:
                        remote_content = results[0].content
                        if isinstance(remote_content, str):
                            remote_content = remote_content.encode("utf-8")
                        remote_hash = hashlib.md5(remote_content).hexdigest()
                        if remote_hash == local_hash:
                            self._last_hashes[cache_key] = local_hash
                            continue
                except Exception:
                    pass  # 读取失败，需要上传

            files_to_upload.append((sandbox_path, local_content))
            pending_hashes[cache_key] = local_hash
            skill_name = relative_path.parts[0]
            if skill_name not in updated_skills:
                updated_skills.append(skill_name)

        if files_to_upload:
            results = await self.backend.aupload_files(files_to_upload)
            uploaded_paths = {result.path for result in results if result.error is None}
            failed_paths = [path for path, _ in files_to_upload if path not in uploaded_paths]
            if failed_paths:
                raise RuntimeError(f"沙箱技能文件同步失败: {', '.join(failed_paths)}")
            # 只有确认上传成功后才更新缓存，失败的文件下一次仍会重试。
            self._last_hashes.update(pending_hashes)

        return updated_skills

    # --------------------- 通知生成 ---------------------
    def _make_notification(self, skill_names: List[str]) -> Dict[str, Any]:
        skills_list = "\n".join(f"- {name}" for name in skill_names)
        notice = (
            f"[系统通知] 以下技能目录或参考文件已更新：\n{skills_list}\n"
            "请使用 `ls /skills/` 查看详情，对当前任务可能有帮助。"
        )
        return {"messages": [SystemMessage(content=notice)]}
