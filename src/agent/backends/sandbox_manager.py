"""
沙箱管理器 — Per-User 沙箱生命周期管理 + 预热机制。

每个用户维护一个独立的 OpenSandbox，同一个用户的多条对话线程共享。
启动时预热一个沙箱，第一个用户连接时直接认领，无需等待创建。

沙箱 ID 通过 MySQL 持久化，重启后可重连到已有沙箱。
"""
from __future__ import annotations

import asyncio
import logging

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.sandbox_setup import setup_sandbox
from agent.config import SANDBOX_CONFIG
from agent.env_utils import SANDBOX_PREWARM_REPLENISH
from agent.mysql_storage import MySQLStorage

logger = logging.getLogger(__name__)

# ---- 全局状态 ----

SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}

_warm_reserve: SandboxBackendProxy | None = None
_warm_lock = asyncio.Lock()

_storage: MySQLStorage | None = None

def _sandbox_storage() -> MySQLStorage:
    if _storage is None:
        raise RuntimeError("沙箱管理器未初始化，请先调用 initialize()")
    return _storage


async def initialize(storage: MySQLStorage) -> None:
    """使用应用启动阶段初始化的 MySQL 存储。"""
    global _storage
    _storage = storage


async def pre_warm() -> None:
    """启动时预热沙箱，首个用户连接时直接认领。失败不阻塞启动。"""
    global _warm_reserve
    logger.debug("正在预热沙箱...")
    try:
        sandbox_backend = await setup_sandbox(SANDBOX_CONFIG)
        _warm_reserve = SandboxBackendProxy(sandbox_backend)
        logger.info("预热沙箱就绪: %s", sandbox_backend.id)
    except Exception:
        logger.warning("预热沙箱失败，将在第一个用户连接时创建", exc_info=True)
        _warm_reserve = None


async def ensure_sandbox_for_user(user_id: str) -> SandboxBackendProxy:
    """
    获取或创建某个用户的沙箱

    1. 内存缓存命中 → ping 健康检查 → 失败则重建
    2. _warm_reserve 有货 → 认领分配给 user
    3. MySQL 有 sandbox_id → connect(id) → 失败则重建
    4. 无任何记录 → 创建新沙箱
    """
    # 状态 1: 内存缓存命中
    proxy = SANDBOX_BACKENDS.get(user_id)
    if proxy is not None:
        try:
            await proxy.aexecute("echo ok")
            logger.debug("用户 %s 命中沙箱缓存: %s", user_id, proxy.id)
            return proxy
        except Exception:
            logger.warning("用户 %s 的沙箱 %s 不可达，重建中...", user_id, proxy.id)
            return await _rebuild_sandbox(user_id, proxy)

    # 状态 2: 认领预热沙箱
    async with _warm_lock:
        global _warm_reserve
        if _warm_reserve is not None:
            proxy = _warm_reserve
            _warm_reserve = None
            sandbox_id = proxy.id

            SANDBOX_BACKENDS[user_id] = proxy
            await _sandbox_storage().bind_sandbox(user_id, sandbox_id)
            logger.info("用户 %s 认领了预热沙箱: %s", user_id, sandbox_id)
            return proxy

    # 走到这里说明预热沙箱已被之前的用户认领（_warm_reserve 为空），
    # 属于「有后续用户到来」。惰性补充：为下一位用户补一个预热沙箱。
    _schedule_replenish()

    # 状态 3: MySQL 重连
    doc = await _sandbox_storage().get_sandbox(user_id)
    if doc and doc.get("sandbox_id"):
        sandbox_id = doc["sandbox_id"]
        logger.debug("用户 %s 尝试重连沙箱: %s", user_id, sandbox_id)
        try:
            sandbox_backend = await setup_sandbox(SANDBOX_CONFIG, sandbox_id=sandbox_id)
        except Exception:
            logger.warning("重连沙箱 %s 失败，将创建新沙箱", sandbox_id)
            return await _rebuild_sandbox(user_id)

        try:
            await sandbox_backend.aexecute("echo ok")
        except Exception:
            logger.warning("已连接的沙箱 %s 不可达，创建新沙箱", sandbox_id)
            return await _rebuild_sandbox(user_id)

        proxy = SandboxBackendProxy(sandbox_backend)
        SANDBOX_BACKENDS[user_id] = proxy
        await _sandbox_storage().bind_sandbox(user_id, sandbox_id)
        logger.info("用户 %s 沙箱恢复完成: %s", user_id, sandbox_id)
        return proxy

    # 状态 4: 无记录，创建新沙箱
    return await _rebuild_sandbox(user_id)


async def recreate_user_sandbox(user_id: str) -> SandboxBackendProxy:
    """
    公开的沙箱重建方法，供 SandboxHealthMiddleware 在检测到沙箱不可用时调用。

    创建新沙箱（含 skills 播种 + venv），替换 proxy 内的 backend，
    删除旧沙箱，更新 MySQL 绑定。不包含 AGENTS.md 上传，由调用方负责。
    """
    return await _rebuild_sandbox(user_id, SANDBOX_BACKENDS.get(user_id))


async def cleanup_user(user_id: str) -> None:
    """销毁某用户的沙箱。"""
    proxy = SANDBOX_BACKENDS.pop(user_id, None)
    if proxy is not None:
        try:
            await _delete_sandbox(proxy.id)
        except Exception:
            logger.warning("删除沙箱 %s 失败", proxy.id, exc_info=True)

    await _sandbox_storage().delete_sandbox(user_id)
    logger.info("用户 %s 沙箱已清理", user_id)


async def shutdown() -> None:
    """清理所有沙箱（含预热），释放资源。"""
    global _warm_reserve, _storage

    if _warm_reserve is not None:
        try:
            await _delete_sandbox(_warm_reserve.id)
        except Exception:
            pass
        _warm_reserve = None

    for user_id in list(SANDBOX_BACKENDS):
        await cleanup_user(user_id)

    # 连接池由 AgentLoader 统一关闭。
    _storage = None


# ---- 内部函数 ----


async def _delete_sandbox(sandbox_id: str) -> None:
    """按 id 删除沙箱（异步，通过 SandboxManager）。"""
    from opensandbox import SandboxManager

    manager = await SandboxManager.create(connection_config=SANDBOX_CONFIG)
    try:
        await manager.kill_sandbox(sandbox_id)
    finally:
        await manager.close()


async def _rebuild_sandbox(
    user_id: str, existing_proxy: SandboxBackendProxy | None = None,
) -> SandboxBackendProxy:
    """创建新沙箱，替换旧 proxy（若有），删除旧沙箱，更新 MySQL 绑定。"""
    logger.debug("为用户 %s 创建新沙箱...", user_id)
    sandbox_backend = await setup_sandbox(SANDBOX_CONFIG)
    sandbox_id = sandbox_backend.id

    if existing_proxy is not None:
        existing_proxy.replace_backend(sandbox_backend)
    else:
        existing_proxy = SandboxBackendProxy(sandbox_backend)
        SANDBOX_BACKENDS[user_id] = existing_proxy

    # 删除 MySQL 中记录的旧沙箱（若存在）。
    old_doc = await _sandbox_storage().get_sandbox(user_id)
    if old_doc and old_doc.get("sandbox_id"):
        try:
            await _delete_sandbox(old_doc["sandbox_id"])
        except Exception:
            pass

    await _sandbox_storage().bind_sandbox(user_id, sandbox_id)

    logger.info("用户 %s 沙箱创建完成: %s", user_id, sandbox_id)
    return existing_proxy


def _schedule_replenish() -> None:
    """在有后续用户到来时，惰性调度后台补充预热（开关控制）。"""
    if SANDBOX_PREWARM_REPLENISH:
        asyncio.create_task(_replenish_warm())


async def _replenish_warm() -> None:
    """后台补充预热沙箱（惰性触发，供下一位用户认领）。"""
    global _warm_reserve
    if _warm_reserve is not None:
        return

    try:
        sandbox_backend = await setup_sandbox(SANDBOX_CONFIG)
        async with _warm_lock:
            if _warm_reserve is None:
                _warm_reserve = SandboxBackendProxy(sandbox_backend)
                logger.info("后台补充预热沙箱就绪: %s", sandbox_backend.id)
    except Exception:
        logger.warning("后台补充预热沙箱失败", exc_info=True)
