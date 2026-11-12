"""验证冷启动期间的进度、超时和客户端断开，不访问外部服务。"""

import asyncio
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from langgraph.checkpoint.memory import InMemorySaver
from api_view.agent_loader import AgentLoader

from api_view.api import chat
from agent.backends import sandbox_setup
from agent.backends import sandbox_healthcheck
from agent.backends import sandbox_manager


class AgentPreparationTests(unittest.IsolatedAsyncioTestCase):
    def patches(self, prepare, *, timeout=1):
        loader = SimpleNamespace(
            create_config=Mock(return_value={}),
            get_agent_for_user=prepare,
            get_display_messages=AsyncMock(return_value=[]),
            save_display_messages=AsyncMock(return_value=True),
            get_direct_response_agent=Mock(side_effect=AgentLoader().get_direct_response_agent),
        )
        stack = ExitStack()
        stack.enter_context(patch.object(chat, "agent_loader", loader))
        stack.enter_context(patch.object(chat, "AGENT_PREPARATION_TIMEOUT_SECONDS", timeout))
        stack.enter_context(patch.object(chat, "AGENT_PREPARATION_PROGRESS_SECONDS", .005))
        stack.enter_context(patch.object(chat, "write_debug_log"))
        return stack

    async def test_approved_reply_skips_sandbox_and_keeps_checkpoint_and_history(self):
        prepare = AsyncMock(side_effect=AssertionError("不应连接沙箱"))
        storage = SimpleNamespace(checkpointer=InMemorySaver())
        with self.patches(prepare) as stack:
            stack.enter_context(patch("api_view.agent_loader.mysql_storage", storage))
            loader = chat.agent_loader
            loader.create_config.return_value = {"configurable": {"thread_id": "greeting"}}
            loader.get_display_messages.return_value = [
                {"id": "old-user", "role": "user", "content": "之前的问题"},
                {"id": "old-reply", "role": "assistant", "content": "之前的回复"},
                {"id": "new-user", "role": "user", "content": "你好"},
            ]
            events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
                message="你好", thread_id="greeting", user_id="qa",
                direct_response="你好！有什么可以帮你的吗？",
            )]
            prepare.assert_not_awaited()
            self.assertEqual(events[-1]["type"], "done")
            self.assertEqual(events[-1]["content"], "你好！有什么可以帮你的吗？")
            self.assertFalse(any(item["type"] == "progress" for item in events))
            saved = loader.save_display_messages.call_args.args[1]
            self.assertEqual(len(saved), 4)
            self.assertEqual(saved[-2]["id"], "new-user")
            graph = loader.get_direct_response_agent("后续回复")
            state = await graph.aget_state(loader.create_config.return_value)
            messages = state.values["messages"]
            self.assertEqual(messages[-1].content, events[-1]["content"])
            state = await graph.ainvoke({"messages": [{"role": "user", "content": "下一条"}]},
                                       loader.create_config.return_value)
            self.assertEqual(len(state["messages"]), 4)

    async def test_health_check_rejects_nonzero_and_bounds_hung_http(self):
        backend = SimpleNamespace(aexecute=AsyncMock(return_value=SimpleNamespace(
            exit_code=124, output="命令超时")))
        self.assertFalse(await sandbox_healthcheck.check_sandbox(backend))
        cancelled = asyncio.Event()

        async def hung(*_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        backend.aexecute = hung
        with patch.object(sandbox_healthcheck, "SANDBOX_HEALTH_TIMEOUT_SECONDS", .01):
            self.assertFalse(await sandbox_healthcheck.check_sandbox(backend))
        self.assertTrue(cancelled.is_set())

    async def test_reconnected_user_binds_actual_replacement_sandbox(self):
        storage = SimpleNamespace(
            get_sandbox=AsyncMock(return_value={"sandbox_id": "expired"}),
            bind_sandbox=AsyncMock(),
        )
        replacement = SimpleNamespace(id="replacement")
        with patch.object(sandbox_manager, "SANDBOX_BACKENDS", {}), \
             patch.object(sandbox_manager, "_ready", __import__("collections").deque()), \
             patch.object(sandbox_manager, "_storage", storage), \
             patch.object(sandbox_manager, "_schedule_replenish"), \
             patch.object(sandbox_manager, "setup_sandbox", AsyncMock(return_value=replacement)), \
             patch.object(sandbox_manager, "check_sandbox", AsyncMock(return_value=True)):
            proxy = await sandbox_manager.ensure_sandbox_for_user("qa")
        self.assertEqual(proxy.id, "replacement")
        storage.bind_sandbox.assert_awaited_once_with("qa", "replacement")

    async def test_parallel_threads_initialize_one_shared_environment(self):
        storage = SimpleNamespace(get_sandbox=AsyncMock(return_value=None), bind_sandbox=AsyncMock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def setup(_config):
            started.set()
            await release.wait()
            return SimpleNamespace(id="shared-environment")

        with patch.object(sandbox_manager, "SANDBOX_BACKENDS", {}), \
             patch.object(sandbox_manager, "_ready", __import__("collections").deque()), \
             patch.object(sandbox_manager, "_storage", storage), \
             patch.object(sandbox_manager, "_schedule_replenish"), \
             patch.object(sandbox_manager, "setup_sandbox", AsyncMock(side_effect=setup)) as prepare, \
             patch.object(sandbox_manager, "check_sandbox", AsyncMock(return_value=True)):
            first = asyncio.create_task(sandbox_manager.ensure_sandbox_for_user("parallel-qa"))
            await started.wait()
            second = asyncio.create_task(sandbox_manager.ensure_sandbox_for_user("parallel-qa"))
            await asyncio.sleep(0)
            release.set()
            a, b = await asyncio.wait_for(asyncio.gather(first, second), 1)
            self.assertIs(a, b)
            self.assertEqual(prepare.await_count, 1)
            storage.bind_sandbox.assert_awaited_once_with("parallel-qa", "shared-environment")

    async def test_one_users_initialization_does_not_block_another(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def setup(user_id):
            if user_id == "slow-user":
                started.set()
                await release.wait()
            return user_id

        with patch.object(sandbox_manager, "_ensure_sandbox_for_user", setup):
            slow = asyncio.create_task(sandbox_manager.ensure_sandbox_for_user("slow-user"))
            await started.wait()
            try:
                result = await asyncio.wait_for(sandbox_manager.ensure_sandbox_for_user("other-user"), .1)
                self.assertEqual(result, "other-user")
            finally:
                release.set()
                await slow

    async def test_slow_preparation_keeps_sending_progress_and_completes(self):
        async def chunks(**_kwargs):
            if False:
                yield

        async def prepare(_user_id, **_kwargs):
            await asyncio.sleep(.03)
            return SimpleNamespace(astream=chunks)

        with self.patches(prepare):
            events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
                message="采购核验", thread_id="preparation-test", user_id="qa",
            )]
        progress = [item for item in events if item.get("stage") == "preparing"]
        self.assertGreaterEqual(len(progress), 2)
        self.assertEqual(events[-1]["type"], "done")
        self.assertFalse(any(item["type"] == "error" for item in events))

    async def test_preparation_timeout_cancels_task_and_reports_error(self):
        cancelled = asyncio.Event()

        async def prepare(_user_id, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with self.patches(prepare, timeout=.025):
            events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
                message="采购核验", thread_id="timeout-test", user_id="qa",
            )]
        self.assertTrue(cancelled.is_set())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("分析环境连接超时", events[-1]["message"])

    async def test_disconnecting_during_preparation_cancels_task(self):
        cancelled = asyncio.Event()

        async def prepare(_user_id, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with self.patches(prepare):
            stream = chat.stream_chat_response(
                message="采购核验", thread_id="disconnect-test", user_id="qa",
            )
            await anext(stream)  # session
            await anext(stream)  # 初始进度
            await anext(stream)  # 初始化等待期间的进度
            await stream.aclose()
        self.assertTrue(cancelled.is_set())

    async def test_cancelled_setup_kills_only_a_new_sandbox(self):
        for sandbox_id in (None, "existing-user-sandbox"):
            sandbox = SimpleNamespace(id="sandbox-test", kill=AsyncMock())
            with patch.object(sandbox_setup.Sandbox, "create", AsyncMock(return_value=sandbox)), \
                 patch.object(sandbox_setup.Sandbox, "connect", AsyncMock(return_value=sandbox)), \
                 patch.object(sandbox_setup, "check_sandbox", AsyncMock(return_value=True)), \
                 patch.object(sandbox_setup, "_ensure_dirs", AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await sandbox_setup.setup_sandbox(object(), sandbox_id=sandbox_id)
            self.assertEqual(sandbox.kill.await_count, 0 if sandbox_id else 1)

    async def test_failed_reconnect_does_not_cold_create_before_pool_fallback(self):
        for failure in (TimeoutError(), ConnectionError("unreachable")):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(sandbox_setup.Sandbox, "connect", AsyncMock(side_effect=failure)), \
                 patch.object(sandbox_setup.Sandbox, "create", AsyncMock()) as create:
                with self.assertRaises(type(failure)):
                    await sandbox_setup.setup_sandbox(object(), sandbox_id="expired")
                create.assert_not_awaited()

    async def test_stream_releases_real_graph_lease_after_completion(self):
        from api_view.runtime_preparation import GraphEntry, GraphLease
        async def chunks(**kwargs):
            if False:
                yield
        registry = SimpleNamespace(release=AsyncMock())
        entry = GraphEntry("qa", SimpleNamespace(astream=chunks), object(), 0, "v1", 0)
        lease = GraphLease(registry, entry)
        with self.patches(AsyncMock(return_value=lease)):
            events = [json.loads(item[5:]) async for item in chat.stream_chat_response(
                message="采购核验", thread_id="lease-complete", user_id="qa")]
        self.assertEqual(events[-1]["type"], "done")
        registry.release.assert_awaited_once_with(entry)

    async def test_disconnected_waiter_preserves_shared_background_preparation(self):
        from api_view.runtime_preparation import RuntimePreparation
        from agent.backends.sandbox_proxy import SandboxBackendProxy
        loader = AgentLoader()
        release = asyncio.Event()
        entered = asyncio.Event()
        async def build(user, proxy):
            entered.set()
            await release.wait()
            return object()
        loader._runtime = RuntimePreparation(build, lambda: "test")
        proxy = SandboxBackendProxy(SimpleNamespace(id="existing"))
        with patch.object(chat, "agent_loader", loader), \
             patch.object(chat, "AGENT_PREPARATION_PROGRESS_SECONDS", .005), \
             patch.object(sandbox_manager, "SANDBOX_BACKENDS", {"qa": proxy}), \
             patch.object(sandbox_manager, "ensure_sandbox_for_user", AsyncMock(return_value=proxy)):
            loader._runtime.schedule("qa")
            stream = chat.prepare_agent("qa", "shared-disconnect")
            await anext(stream)
            await anext(stream)
            await entered.wait()
            await stream.aclose()
            self.assertFalse(loader._runtime.pending["qa"].done())
            release.set()
            await loader._runtime.pending["qa"]
            self.assertEqual(len(loader._runtime.entries), 1)
            await loader._runtime.close()


if __name__ == "__main__":
    unittest.main()
