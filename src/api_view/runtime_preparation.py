"""Bounded, per-process preparation and leased user graphs.

This registry owns tasks; disconnecting an HTTP waiter cannot cancel a task
still owned by authentication or another waiter. It never invokes a graph.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import logging
import time

from agent.backends import sandbox_manager
from agent.backends.sandbox_healthcheck import check_sandbox
from agent.runtime_config import RuntimeConfig
from api_view.observability import (
    PREPARATION_DURATION, PREPARATION_WAIT, PREPARATION_STAGE,
    PREPARATION_SCHEDULE, RUNTIME_CACHE, preparation_measure,
)

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class GraphEntry:
    user: str
    graph: object
    proxy: object
    generation: int
    fingerprint: str
    used: float
    active: int = 0


class GraphLease:
    def __init__(self, registry, entry):
        self._registry = registry
        self._entry = entry
        self._released = False

    def __getattr__(self, name):
        return getattr(self._entry.graph, name)

    async def release(self):
        if not self._released:
            self._released = True
            await self._registry.release(self._entry)


class RuntimePreparation:
    def __init__(self, build, fingerprint, config=None):
        self.config = config or RuntimeConfig.from_env()
        self.build = build
        self.fingerprint = fingerprint
        self.entries: list[GraphEntry] = []
        self.pending: dict[str, asyncio.Task] = {}
        self.waiters: dict[str, int] = {}
        self.background: set[str] = set()
        self.slots = asyncio.Semaphore(self.config.prepare_concurrency)
        self.changed = asyncio.Condition()
        self.reserved = 0
        self.stopping = False
        self.reaper = None
        self.notifications = set()

    def _start_reaper(self):
        if self.reaper is None:
            self.reaper = asyncio.create_task(self._reap_idle(), name="graph-cache-expiry")

    async def _reap_idle(self):
        while not self.stopping:
            await asyncio.sleep(min(60, self.config.graph_idle_seconds))
            async with self.changed:
                for entry in list(self.entries):
                    if (not entry.active and not self.waiters.get(entry.user)
                            and time.monotonic() - entry.used >= self.config.graph_idle_seconds):
                        self.entries.remove(entry)
                        RUNTIME_CACHE.labels("graph", "invalidated").inc()
                self.changed.notify_all()

    def _compatible(self, entry, user, fingerprint):
        proxy = sandbox_manager.SANDBOX_BACKENDS.get(user)
        return (entry.user == user and entry.proxy is proxy
                and entry.generation == getattr(proxy, "generation", 0)
                and entry.fingerprint == fingerprint
                and (entry.active or time.monotonic() - entry.used < self.config.graph_idle_seconds))

    def _cached(self, user):
        if not self.config.graph_enabled:
            return None
        fingerprint = self.fingerprint()
        return next((e for e in self.entries if self._compatible(e, user, fingerprint)), None)

    def schedule(self, user):
        if self.stopping or not self.config.background_enabled:
            return
        self._start_reaper()
        if self._cached(user) is not None:
            PREPARATION_SCHEDULE.labels("ready").inc()
            return
        if user in self.pending:
            self.background.add(user)
            PREPARATION_SCHEDULE.labels("pending").inc()
            return
        if len(self.pending) >= self.config.max_pending:
            PREPARATION_SCHEDULE.labels("skipped_capacity").inc()
            return
        self.background.add(user)
        self._start(user, "auth")
        PREPARATION_SCHEDULE.labels("scheduled").inc()

    def _start(self, user, trigger):
        task = asyncio.create_task(self._prepare(user, trigger), name="user-environment-prepare")
        self.pending[user] = task

        def finished(completed):
            if self.pending.get(user) is completed:
                self.pending.pop(user, None)
                self.background.discard(user)
            if not completed.cancelled() and completed.exception() is not None:
                logger.warning("用户环境准备失败：%s", type(completed.exception()).__name__)
            # Wake capacity waiters after the registry slot is actually removed.
            notification = asyncio.create_task(self._notify())
            self.notifications.add(notification)
            notification.add_done_callback(self.notifications.discard)
        task.add_done_callback(finished)
        return task

    async def _notify(self):
        async with self.changed:
            self.changed.notify_all()

    async def _reserve(self, user):
        async with self.changed:
            while True:
                if self.stopping:
                    raise RuntimeError("采购分析环境正在关闭")
                if len(self.entries) + self.reserved < self.config.graph_max_users:
                    self.reserved += 1
                    return
                idle = [e for e in self.entries if not e.active
                        and (e.user == user or not self.waiters.get(e.user))]
                if idle:
                    entry = min(idle, key=lambda e: e.used)
                    self.entries.remove(entry)
                    RUNTIME_CACHE.labels("graph", "invalidated").inc()
                    continue
                await self.changed.wait()

    async def _prepare(self, user, trigger):
        # Deadline covers global queue and cache capacity as well as actual work.
        async with asyncio.timeout(self.config.prepare_timeout) as deadline:
            async with self.slots:
                with preparation_measure(PREPARATION_DURATION, trigger, deadline=deadline):
                    await self._reserve(user)
                    had_sandbox = user in sandbox_manager.SANDBOX_BACKENDS
                    old_binding = None
                    proxy = None
                    try:
                        if not had_sandbox:
                            old_binding = await sandbox_manager._sandbox_storage().get_sandbox(user)
                        with preparation_measure(PREPARATION_STAGE, "sandbox", deadline=deadline):
                            proxy = await sandbox_manager.ensure_sandbox_for_user(user)
                        fingerprint = self.fingerprint()
                        with preparation_measure(PREPARATION_STAGE, "graph", deadline=deadline):
                            graph = await self.build(user, proxy)
                        entry = GraphEntry(user, graph, proxy, getattr(proxy, "generation", 0),
                                           fingerprint, time.monotonic())
                        self.entries.append(entry)
                        return entry
                    except BaseException:
                        assigned = sandbox_manager.SANDBOX_BACKENDS.get(user)
                        if (not had_sandbox and assigned is not None
                                and assigned.id != (old_binding or {}).get("sandbox_id")):
                            await sandbox_manager.cleanup_user(user)
                        raise
                    finally:
                        self.reserved -= 1
                        await self._notify()

    async def acquire(self, user, endpoint="chat"):
        with preparation_measure(PREPARATION_WAIT, endpoint):
            async with asyncio.timeout(self.config.prepare_timeout):
                return await self._acquire(user, endpoint)

    async def _acquire(self, user, endpoint):
        if self.stopping:
            raise RuntimeError("采购分析环境正在关闭")
        self._start_reaper()
        self.waiters[user] = self.waiters.get(user, 0) + 1
        try:
            entry = self._cached(user)
            if entry is not None:
                entry.active += 1
                try:
                    if await check_sandbox(entry.proxy):
                        entry.used = time.monotonic()
                        RUNTIME_CACHE.labels("graph", "hit").inc()
                        return GraphLease(self, entry)
                except BaseException:
                    await self.release(entry)
                    raise
                await self.release(entry)
                entry.fingerprint = "invalid-health"
                RUNTIME_CACHE.labels("graph", "invalidated").inc()
            RUNTIME_CACHE.labels("graph", "miss").inc()
            while user not in self.pending and len(self.pending) >= self.config.max_pending:
                async with self.changed:
                    await self.changed.wait()
                if self.stopping:
                    raise RuntimeError("采购分析环境正在关闭")
            task = self.pending.get(user) or self._start(user, endpoint)
            entry = await asyncio.shield(task)
            entry.active += 1
            entry.used = time.monotonic()
            return GraphLease(self, entry)
        finally:
            remaining = self.waiters[user] - 1
            if remaining:
                self.waiters[user] = remaining
            else:
                self.waiters.pop(user, None)
                task = self.pending.get(user)
                if task is not None and user not in self.background:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def release(self, entry):
        entry.active -= 1
        entry.used = time.monotonic()
        if not self.config.graph_enabled and entry.active == 0 and not self.waiters.get(entry.user):
            if entry in self.entries:
                self.entries.remove(entry)
        await self._notify()

    async def logout(self, user):
        self.background.discard(user)
        task = self.pending.get(user)
        if task is not None and not self.waiters.get(user):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        self.stopping = True
        if self.reaper is not None:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
            self.reaper = None
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.notifications:
            await asyncio.gather(*list(self.notifications), return_exceptions=True)
        self.pending.clear()
        self.background.clear()
        self.entries.clear()
        await self._notify()
