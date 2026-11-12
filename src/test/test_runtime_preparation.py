import asyncio
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.runtime_config import RuntimeConfig
from api_view.runtime_preparation import RuntimePreparation
from api_view import runtime_preparation as preparation
from api_view.observability import PREPARATION_DURATION, PREPARATION_WAIT


def sample(metric, suffix, labels):
    return next(s.value for s in metric.collect()[0].samples
                if s.name.endswith(suffix) and s.labels == labels)


class PreparationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.proxies = {}
        async def ensure(user):
            self.proxies.setdefault(user, SandboxBackendProxy(SimpleNamespace(id=user)))
            return self.proxies[user]
        self.ensure = self.stack.enter_context(patch.object(preparation.sandbox_manager,
            "ensure_sandbox_for_user", AsyncMock(side_effect=ensure)))
        self.stack.enter_context(patch.object(preparation.sandbox_manager, "SANDBOX_BACKENDS", self.proxies))
        self.stack.enter_context(patch.object(preparation.sandbox_manager, "_storage",
            SimpleNamespace(get_sandbox=AsyncMock(return_value=None))))
        self.stack.enter_context(patch.object(preparation, "check_sandbox", AsyncMock(return_value=True)))
        self.cleanup = self.stack.enter_context(patch.object(preparation.sandbox_manager,
            "cleanup_user", AsyncMock(side_effect=lambda user: self.proxies.pop(user, None))))
        self.build = AsyncMock(side_effect=lambda user, proxy: SimpleNamespace(user=user, proxy=proxy))
        self.version = "v1"
        self.runtime = RuntimePreparation(self.build, lambda: self.version)

    async def asyncTearDown(self):
        await self.runtime.close()

    async def wait_until(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(.001)

    async def test_auth_and_two_waiters_share_work_and_count_once(self):
        release = asyncio.Event()
        async def build(user, proxy):
            await release.wait()
            return SimpleNamespace(user=user)
        self.build.side_effect = build
        actual_labels = {"trigger": "auth", "status": "success"}
        wait_labels = {"endpoint": "chat", "outcome": "ready"}
        before_work = sample(PREPARATION_DURATION, "_count", actual_labels)
        before_wait = sample(PREPARATION_WAIT, "_count", wait_labels)
        self.runtime.schedule("alice")
        a = asyncio.create_task(self.runtime.acquire("alice"))
        b = asyncio.create_task(self.runtime.acquire("alice"))
        await self.wait_until(lambda: self.build.await_count == 1)
        release.set()
        first, second = await asyncio.gather(a, b)
        self.assertIs(first._entry.graph, second._entry.graph)
        self.assertEqual(sample(PREPARATION_DURATION, "_count", actual_labels) - before_work, 1)
        self.assertEqual(sample(PREPARATION_WAIT, "_count", wait_labels) - before_wait, 2)
        await first.release()
        await second.release()
        hot = await self.runtime.acquire("alice", "resume")
        self.assertEqual(self.build.await_count, 1)
        await hot.release()

    async def test_one_waiter_disconnect_does_not_cancel_another(self):
        release = asyncio.Event()
        cancelled = asyncio.Event()
        async def build(*args):
            try:
                await release.wait()
                return object()
            finally:
                cancelled.set()
        self.build.side_effect = build
        a = asyncio.create_task(self.runtime.acquire("alice"))
        b = asyncio.create_task(self.runtime.acquire("alice"))
        await self.wait_until(lambda: self.runtime.waiters.get("alice") == 2)
        a.cancel()
        await asyncio.gather(a, return_exceptions=True)
        self.assertFalse(cancelled.is_set())
        release.set()
        lease = await b
        await lease.release()

    async def test_orphan_cancel_and_logout_release_speculation(self):
        self.build.side_effect = lambda *args: None
        started = asyncio.Event()
        async def blocked(*args):
            started.set()
            await asyncio.Event().wait()
        self.build.side_effect = blocked
        waiter = asyncio.create_task(self.runtime.acquire("alice"))
        await started.wait()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        self.cleanup.assert_awaited_with("alice")
        await self.wait_until(lambda: not self.runtime.pending)
        started.clear()
        self.runtime.schedule("bob")
        await started.wait()
        await self.runtime.logout("bob")
        self.assertNotIn("bob", self.runtime.pending)

    async def test_background_capacity_and_foreground_bounded_wait(self):
        self.runtime.config = replace(RuntimeConfig(), prepare_concurrency=1, max_pending=1, prepare_timeout=.05)
        self.runtime.slots = asyncio.Semaphore(1)
        self.build.side_effect = AsyncMock(side_effect=lambda *args: asyncio.sleep(1))
        async def blocked(*args):
            await asyncio.Event().wait()
        self.build.side_effect = blocked
        self.runtime.schedule("a")
        self.runtime.schedule("b")
        self.assertEqual(set(self.runtime.pending), {"a"})
        with self.assertRaises(TimeoutError):
            await self.runtime.acquire("b")
        await self.wait_until(lambda: not self.runtime.pending)

    async def test_generation_fingerprint_expiry_and_idle_eviction(self):
        self.runtime.config = replace(RuntimeConfig(), graph_max_users=1)
        first = await self.runtime.acquire("alice")
        await first.release()
        self.version = "v2"
        second = await self.runtime.acquire("alice")
        self.assertIsNot(first._entry.graph, second._entry.graph)
        await second.release()
        self.proxies["alice"].replace_backend(SimpleNamespace(id="replacement"))
        third = await self.runtime.acquire("alice")
        self.assertEqual(third._entry.generation, 1)
        await third.release()
        third._entry.used -= 1801
        expired = await self.runtime.acquire("alice")
        self.assertIsNot(expired._entry.graph, third._entry.graph)
        await expired.release()
        other = await self.runtime.acquire("bob")
        self.assertEqual(len(self.runtime.entries), 1)
        self.assertIn("alice", self.proxies)
        self.cleanup.assert_not_awaited()
        await other.release()

    async def test_active_cache_bound_waits_without_eviction_or_overflow(self):
        self.runtime.config = replace(RuntimeConfig(), graph_max_users=1, prepare_timeout=.1)
        first = await self.runtime.acquire("alice")
        other = asyncio.create_task(self.runtime.acquire("bob"))
        await asyncio.sleep(.01)
        self.assertEqual(self.build.await_count, 1)
        self.assertFalse(other.done())
        self.assertEqual(len(self.runtime.entries), 1)
        await first.release()
        second = await other
        self.assertEqual(len(self.runtime.entries), 1)
        await second.release()

    async def test_failed_preparation_retries_and_disabled_cache_rebuilds(self):
        self.build.side_effect = RuntimeError("compile failed")
        with self.assertRaises(RuntimeError):
            await self.runtime.acquire("alice")
        await self.wait_until(lambda: not self.runtime.pending)
        self.build.side_effect = lambda *args: object()
        self.runtime.config = replace(RuntimeConfig(), graph_enabled=False, background_enabled=False)
        self.runtime.schedule("alice")
        self.assertFalse(self.runtime.pending)
        one = await self.runtime.acquire("alice")
        await one.release()
        two = await self.runtime.acquire("alice")
        self.assertIsNot(one._entry.graph, two._entry.graph)
        await two.release()

    async def test_global_queue_is_excluded_from_actual_work(self):
        self.runtime.slots = asyncio.Semaphore(0)
        before = sample(PREPARATION_DURATION, "_count", {"trigger": "auth", "status": "success"})
        self.runtime.schedule("a")
        await asyncio.sleep(.01)
        self.assertEqual(sample(PREPARATION_DURATION, "_count", {"trigger": "auth", "status": "success"}), before)
        self.runtime.slots.release()
        await self.wait_until(lambda: not self.runtime.pending)
        self.assertEqual(sample(PREPARATION_DURATION, "_count", {"trigger": "auth", "status": "success"}), before + 1)

    async def test_failed_graph_does_not_destroy_restored_user_files(self):
        preparation.sandbox_manager._storage.get_sandbox.return_value = {"sandbox_id": "alice"}
        self.build.side_effect = RuntimeError("compile failed")
        with self.assertRaises(RuntimeError):
            await self.runtime.acquire("alice")
        self.cleanup.assert_not_awaited()
        self.assertIn("alice", self.proxies)

    async def test_close_cancels_and_awaits_pending_before_releasing_references(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def build(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.build.side_effect = build
        self.runtime.schedule("alice")
        await entered.wait()
        await self.runtime.close()
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.runtime.pending or self.runtime.entries)
        self.cleanup.assert_awaited_with("alice")
        with self.assertRaises(RuntimeError):
            await self.runtime.acquire("alice")

    async def test_idle_reaper_preserves_active_and_removes_idle_only(self):
        lease = await self.runtime.acquire("alice")
        self.runtime.reaper.cancel()
        await asyncio.gather(self.runtime.reaper, return_exceptions=True)
        lease._entry.used -= 1801
        calls = 0
        async def next_tick(_):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError
        with patch.object(preparation.asyncio, "sleep", side_effect=next_tick):
            with self.assertRaises(asyncio.CancelledError):
                await self.runtime._reap_idle()
        self.assertIn(lease._entry, self.runtime.entries)
        await lease.release()
        lease._entry.used -= 1801
        calls = 0
        with patch.object(preparation.asyncio, "sleep", side_effect=next_tick):
            with self.assertRaises(asyncio.CancelledError):
                await self.runtime._reap_idle()
        self.assertNotIn(lease._entry, self.runtime.entries)
        self.cleanup.assert_not_awaited()

    async def test_background_deadline_is_failure_and_not_explicit_cancellation(self):
        self.runtime.config = replace(RuntimeConfig(), prepare_timeout=.01)
        async def blocked(*args):
            await asyncio.Event().wait()
        self.build.side_effect = blocked
        labels = {"trigger": "auth", "status": "failure"}
        before = sample(PREPARATION_DURATION, "_count", labels)
        self.runtime.schedule("alice")
        await self.wait_until(lambda: not self.runtime.pending)
        self.assertEqual(sample(PREPARATION_DURATION, "_count", labels), before + 1)
