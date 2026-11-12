"""User-owned sandboxes and a bounded, exclusively assigned ready pool."""
from __future__ import annotations

import asyncio
from collections import deque
import logging
from weakref import WeakValueDictionary

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.sandbox_setup import setup_sandbox
from agent.backends.sandbox_healthcheck import check_sandbox, invalidate_health
from agent.config import SANDBOX_CONFIG
from agent.env_utils import SANDBOX_PREWARM_REPLENISH
from agent.runtime_config import RuntimeConfig
from agent.mysql_storage import MySQLStorage
from api_view.observability import POOL_READY, POOL_TARGET, POOL_CREATING, POOL_REPLENISH

logger = logging.getLogger(__name__)
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}
_ready: deque[SandboxBackendProxy] = deque()
_creating = 0
_warm_lock = asyncio.Lock()
_replenish_task: asyncio.Task | None = None
_refill_requested = False
SANDBOX_CLEANUP_TIMEOUT_SECONDS = 8
_storage: MySQLStorage | None = None
_stopping = False
_user_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _user_lock(user_id):
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _sandbox_storage():
    if _storage is None:
        raise RuntimeError("沙箱管理器未初始化，请先调用 initialize()")
    return _storage


def _pool_metrics():
    POOL_READY.set(len(_ready))
    POOL_CREATING.set(_creating)
    POOL_TARGET.set(RuntimeConfig.from_env().pool_target)


async def initialize(storage):
    global _storage, _stopping
    _storage = storage
    _stopping = False
    _pool_metrics()


async def pre_warm():
    """Initial fill is allowed even when post-claim replenishment is disabled."""
    global _replenish_task
    if _replenish_task is None or _replenish_task.done():
        _replenish_task = asyncio.create_task(_replenish_warm(), name="sandbox-pool-fill")
    await asyncio.shield(_replenish_task)


def _schedule_replenish():
    global _replenish_task, _refill_requested
    if _stopping or not SANDBOX_PREWARM_REPLENISH:
        return
    if _replenish_task is None or _replenish_task.done():
        _replenish_task = asyncio.create_task(_replenish_warm(), name="sandbox-pool-fill")
    else:
        _refill_requested = True


async def _fill_worker():
    global _creating
    failures = 0
    while not _stopping:
        async with _warm_lock:
            if len(_ready) + _creating >= RuntimeConfig.from_env().pool_target:
                return
            _creating += 1
            _pool_metrics()
        backend = None
        status = "failure"
        try:
            async with asyncio.timeout(RuntimeConfig.from_env().prepare_timeout):
                backend = await setup_sandbox(SANDBOX_CONFIG)
            status = "success"
            failures = 0
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:
            failures += 1
            logger.warning("预热池补充失败（第 %d 次）", failures, exc_info=True)
        finally:
            async with _warm_lock:
                if (backend is not None and status == "success" and not _stopping
                        and len(_ready) < RuntimeConfig.from_env().pool_target):
                    _ready.append(SandboxBackendProxy(backend))
                    backend = None  # atomic creating -> ready ownership transfer
                _creating -= 1
                _pool_metrics()
            POOL_REPLENISH.labels(status).inc()
            if backend is not None:
                await _discard(backend.id)
        if failures:
            if failures >= 3:
                return
            await asyncio.sleep(2 ** (failures - 1))


async def _replenish_warm():
    global _refill_requested
    while True:
        _refill_requested = False
        cfg = RuntimeConfig.from_env()
        workers = [asyncio.create_task(_fill_worker()) for _ in range(cfg.pool_concurrency)]
        try:
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        # A claim can arrive after the last worker returns but before gather
        # completes. Honour that request without starting a second pool owner.
        if not _refill_requested or _stopping or not SANDBOX_PREWARM_REPLENISH:
            return


async def ensure_sandbox_for_user(user_id):
    async with _user_lock(user_id):
        if _stopping:
            raise RuntimeError("采购分析环境正在关闭")
        return await _ensure_sandbox_for_user(user_id)


async def _ensure_sandbox_for_user(user_id):
    proxy = SANDBOX_BACKENDS.get(user_id)
    if proxy is not None:
        if await check_sandbox(proxy):
            return proxy
        return await _rebuild_sandbox(user_id, proxy)

    # Restore a user's files before claiming any anonymous pool resource.
    doc = await _sandbox_storage().get_sandbox(user_id)
    old_id = doc.get("sandbox_id") if doc else None
    if old_id:
        backend = None
        try:
            backend = await setup_sandbox(SANDBOX_CONFIG, sandbox_id=old_id)
            if not await check_sandbox(backend):
                raise RuntimeError("沙箱健康检查失败")
        except asyncio.CancelledError:
            if backend is not None and backend.id != old_id:
                await _discard(backend.id)
            raise
        except Exception:
            if backend is not None and backend.id != old_id:
                await _discard(backend.id)
            logger.warning("用户沙箱恢复失败，将替换环境", exc_info=True)
        else:
            return await _bind(user_id, SandboxBackendProxy(backend), old_id=old_id)
    return await _rebuild_sandbox(user_id)


async def _bind(user_id, new_proxy, *, old_id=None, existing_proxy=None):
    try:
        if _stopping:
            raise RuntimeError("采购分析环境正在关闭")
        storage = _sandbox_storage()
        await storage.bind_sandbox(user_id, new_proxy.id)
        if _stopping:
            if new_proxy.id != old_id:
                await storage.delete_sandbox(user_id)
            raise RuntimeError("采购分析环境正在关闭")
    except BaseException:
        if new_proxy.id != old_id:
            await _discard(new_proxy.id)
        raise
    if existing_proxy is not None:
        existing_proxy.replace_backend(new_proxy._backend)
        proxy = existing_proxy
    else:
        proxy = new_proxy
    SANDBOX_BACKENDS[user_id] = proxy
    if old_id and old_id != proxy.id:
        await _discard(old_id)
    return proxy


async def _rebuild_sandbox(user_id, existing_proxy=None):
    doc = await _sandbox_storage().get_sandbox(user_id)
    old_id = existing_proxy.id if existing_proxy is not None else (doc or {}).get("sandbox_id")
    async with _warm_lock:
        proxy = _ready.popleft() if _ready else None
        _pool_metrics()
    _schedule_replenish()
    if proxy is not None:
        try:
            healthy = await check_sandbox(proxy)
        except BaseException:
            await _discard(proxy.id)
            raise
        if not healthy:
            await _discard(proxy.id)
            proxy = None
    if proxy is None:
        proxy = SandboxBackendProxy(await setup_sandbox(SANDBOX_CONFIG))
    return await _bind(user_id, proxy, old_id=old_id, existing_proxy=existing_proxy)


async def recreate_user_sandbox(user_id):
    async with _user_lock(user_id):
        proxy = SANDBOX_BACKENDS.get(user_id)
        if proxy is not None and await check_sandbox(proxy):
            return proxy
        return await _rebuild_sandbox(user_id, proxy)


async def cleanup_user(user_id):
    async with _user_lock(user_id):
        proxy = SANDBOX_BACKENDS.pop(user_id, None)
        if proxy is not None:
            await _discard(proxy.id)
        await _sandbox_storage().delete_sandbox(user_id)


async def _discard(sandbox_id):
    invalidate_health(sandbox_id)
    try:
        await asyncio.wait_for(_delete_sandbox(sandbox_id), timeout=SANDBOX_CLEANUP_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("清理沙箱失败", exc_info=True)


async def _delete_sandbox(sandbox_id):
    from opensandbox import SandboxManager
    invalidate_health(sandbox_id)
    manager = await SandboxManager.create(connection_config=SANDBOX_CONFIG)
    try:
        await manager.kill_sandbox(sandbox_id)
    finally:
        await manager.close()


async def shutdown():
    global _storage, _stopping, _replenish_task
    _stopping = True
    if _replenish_task is not None:
        _replenish_task.cancel()
        await asyncio.gather(_replenish_task, return_exceptions=True)
        _replenish_task = None
    async with _warm_lock:
        unused = list(_ready)
        _ready.clear()
        _pool_metrics()
    for proxy in unused:
        await _discard(proxy.id)
    for user_id in list(SANDBOX_BACKENDS):
        await cleanup_user(user_id)
    _storage = None
