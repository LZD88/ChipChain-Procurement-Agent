import asyncio
from collections import deque
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from agent.backends import sandbox_manager as manager
from agent.backends.sandbox_proxy import SandboxBackendProxy


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.storage = SimpleNamespace(get_sandbox=AsyncMock(return_value=None),
            bind_sandbox=AsyncMock(), delete_sandbox=AsyncMock())
        self.deleted = self.stack.enter_context(patch.object(manager, "_delete_sandbox", AsyncMock()))
        for name, value in (("_storage", self.storage), ("_stopping", False),
                            ("_ready", deque()), ("_creating", 0),
                            ("_replenish_task", None), ("_refill_requested", False),
                            ("SANDBOX_BACKENDS", {})):
            self.stack.enter_context(patch.object(manager, name, value))
        self.stack.enter_context(patch.dict("os.environ", {"SANDBOX_PREWARM_TARGET": "2", "SANDBOX_PREWARM_CONCURRENCY": "1"}))
        self.counter = 0
        async def create(*args, **kwargs):
            self.counter += 1
            await asyncio.sleep(0)
            self.assertLessEqual(len(manager._ready) + manager._creating, 2)
            return SimpleNamespace(id=f"pool-{self.counter}")
        self.setup = self.stack.enter_context(patch.object(manager, "setup_sandbox", AsyncMock(side_effect=create)))
        self.stack.enter_context(patch.object(manager, "check_sandbox", AsyncMock(return_value=True)))

    async def asyncTearDown(self):
        await manager.shutdown()

    async def test_two_users_claim_exclusively_and_replenish_immediately(self):
        await manager.pre_warm()
        self.assertEqual(len(manager._ready), 2)
        a, b = await asyncio.gather(manager.ensure_sandbox_for_user("a"), manager.ensure_sandbox_for_user("b"))
        self.assertNotEqual(a.id, b.id)
        await manager._replenish_task
        self.assertEqual(len(manager._ready), 2)
        self.assertEqual(self.setup.await_count, 4)

    async def test_binding_is_restored_before_pool_claim(self):
        await manager.pre_warm()
        self.storage.get_sandbox.return_value = {"sandbox_id": "persisted"}
        self.setup.side_effect = None
        self.setup.return_value = SimpleNamespace(id="persisted")
        result = await manager.ensure_sandbox_for_user("returning")
        self.assertEqual(result.id, "persisted")
        self.assertEqual(len(manager._ready), 2)
        self.setup.assert_awaited_with(manager.SANDBOX_CONFIG, sandbox_id="persisted")

    async def test_claim_after_worker_finished_still_replenishes(self):
        original = manager._fill_worker
        claimed = asyncio.Event()
        async def finish_then_claim():
            await original()
            if not claimed.is_set():
                claimed.set()
                await manager.ensure_sandbox_for_user("late-claim")
        with patch.object(manager, "_fill_worker", side_effect=finish_then_claim):
            await manager.pre_warm()
        self.assertEqual(len(manager._ready), 2)
        self.assertEqual(self.setup.await_count, 3)
        self.assertIn("late-claim", manager.SANDBOX_BACKENDS)

    async def test_failed_binding_restore_claims_ready_pool_without_cold_creation(self):
        await manager.pre_warm()
        self.storage.get_sandbox.return_value = {"sandbox_id": "expired"}
        self.setup.side_effect = TimeoutError("restore unavailable")
        with patch.object(manager, "SANDBOX_PREWARM_REPLENISH", False):
            result = await manager.ensure_sandbox_for_user("returning")
        self.assertTrue(result.id.startswith("pool-"))
        self.assertEqual(len(manager._ready), 1)
        self.assertEqual(self.setup.await_count, 3)  # initial two + reconnect only
        self.storage.bind_sandbox.assert_awaited_with("returning", result.id)

    async def test_old_sandbox_cleanup_is_bounded_after_claim(self):
        await manager.pre_warm()
        self.storage.get_sandbox.return_value = {"sandbox_id": "expired"}
        self.setup.side_effect = TimeoutError("restore unavailable")
        cancelled = asyncio.Event()
        async def stuck_delete(_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.deleted.side_effect = stuck_delete
        with patch.object(manager, "SANDBOX_PREWARM_REPLENISH", False), \
             patch.object(manager, "SANDBOX_CLEANUP_TIMEOUT_SECONDS", .01):
            result = await manager.ensure_sandbox_for_user("returning")
        self.assertTrue(cancelled.is_set())
        self.assertTrue(result.id.startswith("pool-"))
        self.deleted.side_effect = None

    async def test_failed_binding_does_not_publish_or_leak_claim(self):
        await manager.pre_warm()
        claimed_id = manager._ready[0].id
        self.storage.bind_sandbox.side_effect = RuntimeError("database down")
        with patch.object(manager, "_schedule_replenish"):
            with self.assertRaises(RuntimeError):
                await manager.ensure_sandbox_for_user("a")
        self.assertNotIn("a", manager.SANDBOX_BACKENDS)
        self.deleted.assert_awaited_with(claimed_id)

    async def test_disabled_and_legacy_no_replenish(self):
        with patch.dict("os.environ", {"SANDBOX_PREWARM_TARGET": "0"}):
            await manager.pre_warm()
            self.setup.assert_not_awaited()
        with patch.object(manager, "SANDBOX_PREWARM_REPLENISH", False):
            await manager.pre_warm()
            await manager.ensure_sandbox_for_user("a")
            self.assertTrue(manager._replenish_task.done())
            self.assertEqual(len(manager._ready), 1)

    async def test_shutdown_cancels_creation_and_cleans_ready_entries(self):
        await manager.pre_warm()
        ids = {p.id for p in manager._ready}
        await manager.shutdown()
        self.assertFalse(manager._ready)
        self.assertEqual(manager._creating, 0)
        self.assertTrue(ids.issubset({call.args[0] for call in self.deleted.await_args_list}))

    async def test_replacement_keeps_old_proxy_until_binding_succeeds(self):
        old = SandboxBackendProxy(SimpleNamespace(id="old"))
        manager.SANDBOX_BACKENDS["a"] = old
        self.storage.bind_sandbox.side_effect = RuntimeError("db failure")
        with patch.object(manager, "_schedule_replenish"):
            with self.assertRaises(RuntimeError):
                await manager._rebuild_sandbox("a", old)
        self.assertEqual(old.id, "old")
        self.assertNotIn("old", [c.args[0] for c in self.deleted.await_args_list])

    async def test_cancelled_fill_and_shutdown_claim_race_do_not_leak(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def setup(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.setup.side_effect = setup
        filling = asyncio.create_task(manager.pre_warm())
        await started.wait()
        await manager.shutdown()
        await asyncio.gather(filling, return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(manager._creating, 0)
        self.assertFalse(manager._ready)
        with self.assertRaises(RuntimeError):
            await manager.ensure_sandbox_for_user("late")

    async def test_creation_failure_is_bounded_and_leaves_on_demand_path(self):
        self.setup.side_effect = RuntimeError("unavailable")
        with patch.object(manager.asyncio, "sleep", AsyncMock()):
            await manager.pre_warm()
        self.assertEqual(self.setup.await_count, 3)
        self.assertFalse(manager._ready)
        self.assertEqual(manager._creating, 0)
        self.setup.side_effect = None
        self.setup.return_value = SimpleNamespace(id="on-demand")
        with patch.object(manager, "_schedule_replenish"):
            result = await manager.ensure_sandbox_for_user("a")
        self.assertEqual(result.id, "on-demand")

    async def test_shutdown_while_binding_cannot_publish_an_orphan(self):
        await manager.pre_warm()
        claimed_id = manager._ready[0].id
        binding = asyncio.Event()
        finish = asyncio.Event()
        async def blocked_bind(*args):
            binding.set()
            await finish.wait()
        self.storage.bind_sandbox.side_effect = blocked_bind
        with patch.object(manager, "_schedule_replenish"):
            claim = asyncio.create_task(manager.ensure_sandbox_for_user("late-bind"))
            await binding.wait()
            await manager.shutdown()
            finish.set()
            with self.assertRaises(RuntimeError):
                await claim
        self.assertNotIn("late-bind", manager.SANDBOX_BACKENDS)
        self.deleted.assert_any_await(claimed_id)
        self.storage.delete_sandbox.assert_awaited_with("late-bind")
