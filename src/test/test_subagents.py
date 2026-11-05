"""子 Agent 直接绑定的离线回归测试，使用真实 DeepAgents 图和模拟模型/工具。"""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from deepagents.backends.protocol import (
    ExecuteResponse, FileDownloadResponse, FileUploadResponse, LsResult, SandboxBackendProtocol,
)
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
    from agent import main_agent


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class SubagentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executed = []
        names = [
            "supplier_query", "part_query", "part_search", "part_by_supplier",
            "inventory_warning", "order_create", "order_update", "order_search_details",
        ]
        self.tools = {name: self.make_tool(name) for name in names}
        self.precomputed = main_agent.PrecomputedContext(
            analyst_mcp_tools=[self.tools[name] for name in names[:5]],
            order_mcp_tools=[self.tools[name] for name in names[5:]],
            chart_tool=self.make_tool("generate_chart"),
        )
        self.model = ToolCallingModel(
            responses=[AIMessage(content="完成")],
            custom_get_token_ids=lambda text: list(range(len(text) // 4)),
        )
        self.storage = SimpleNamespace(initialize=AsyncMock(), checkpointer=InMemorySaver())
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.patch("DOWNLOAD_DIR", Path(directory.name))
        self.patch("mysql_storage", self.storage)
        self.patch("MAIN_MODEL", self.model)
        self.patch("SUMMARY_MODEL", self.model)
        self.patch("STORE", InMemoryStore())
        self.build = self.patch("create_deep_agent", wraps=main_agent.create_deep_agent)
        tracing_patch = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        tracing_patch.start()
        self.addCleanup(tracing_patch.stop)

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(main_agent, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def make_tool(self, name):
        def execute() -> str:
            self.executed.append(name)
            return "操作成功"
        return StructuredTool.from_function(execute, name=name, description="离线测试工具")

    async def create_graph(self, user_id="test-user"):
        sandbox = AsyncMock(spec=SandboxBackendProtocol)
        sandbox.aexecute.return_value = ExecuteResponse(output="ok", exit_code=0)
        sandbox.als.return_value = LsResult(entries=[])
        sandbox.adownload_files.side_effect = lambda paths: [
            FileDownloadResponse(path=path, error="file_not_found") for path in paths
        ]
        sandbox.aupload_files.side_effect = lambda files: [
            FileUploadResponse(path=path) for path, _ in files
        ]
        graph = await main_agent.create_main_agent(
            {"configurable": {"user_id": user_id}},
            sandbox_backend=sandbox,
            precomputed=self.precomputed,
        )
        return graph, self.build.call_args.kwargs, sandbox

    async def test_tools_bind_exact_objects_without_substring_matches(self):
        self.precomputed.analyst_mcp_tools.append(self.make_tool("supplier_query_debug"))
        self.precomputed.order_mcp_tools.append(self.make_tool("order_create_debug"))
        graph, arguments, _ = await self.create_graph()
        analyst, order = arguments["subagents"]
        self.assertEqual(len(analyst["tools"]), 8)
        self.assertEqual(len(order["tools"]), 5)
        for subagent in (analyst, order):
            bound = {tool.name: tool for tool in subagent["tools"]}
            self.assertFalse(any(name.endswith("_debug") for name in bound))
            for name in bound.keys() & self.tools.keys():
                self.assertIs(bound[name], self.tools[name])
        self.assertNotIn("order_create", {tool.name for tool in analyst["tools"]})
        self.assertNotIn("order_update", {tool.name for tool in analyst["tools"]})
        self.assertIn("task", graph.nodes["tools"].bound.tools_by_name)

    async def test_missing_required_tool_fails_during_graph_creation(self):
        self.precomputed.order_mcp_tools.remove(self.tools["order_update"])
        with self.assertRaisesRegex(KeyError, "order_update"):
            await self.create_graph()
        self.build.assert_not_called()

    async def test_requests_keep_separate_backends_and_subagent_settings(self):
        _, first, first_sandbox = await self.create_graph("first-user")
        _, second, second_sandbox = await self.create_graph("second-user")
        self.assertIs(first["backend"].default, first_sandbox)
        self.assertIs(second["backend"].default, second_sandbox)
        for arguments in (first, second):
            analyst, order = arguments["subagents"]
            self.assertEqual(analyst["skills"], ["/skills/procurement/"])
            self.assertEqual(order["skills"], ["/skills/order/"])
        for old, new in zip(first["subagents"], second["subagents"]):
            self.assertIsNot(old, new)
            for old_middleware, new_middleware in zip(old["middleware"], new["middleware"]):
                self.assertIsNot(old_middleware, new_middleware)
        first["subagents"][1]["interrupt_on"]["order_create"]["allowed_decisions"].clear()
        self.assertEqual(second["subagents"][1]["interrupt_on"]["order_create"]["allowed_decisions"], ["approve", "reject"])

    async def test_order_writes_wait_for_approval_and_respect_rejection(self):
        for tool_name in ("order_create", "order_update"):
            for decision in ("approve", "reject"):
                with self.subTest(tool=tool_name, decision=decision):
                    self.executed.clear()
                    self.model.i = 0
                    self.model.responses = [
                        AIMessage(content="", tool_calls=[{
                            "name": "task", "id": "delegate", "args": {
                                "subagent_type": "procurement-order", "description": "处理测试订单",
                            },
                        }]),
                        AIMessage(content="", tool_calls=[{"name": tool_name, "id": "write", "args": {}}]),
                        AIMessage(content="订单任务结束"),
                        AIMessage(content="处理完成"),
                    ]
                    graph, _, _ = await self.create_graph()
                    config = {"configurable": {"thread_id": f"{tool_name}-{decision}", "user_id": "test-user"}}
                    context = main_agent.ProcurementContext(user_id="test-user", username="测试用户")
                    result = await graph.ainvoke({"messages": [("user", "处理订单")]}, config, context=context)
                    self.assertEqual(self.executed, [])
                    interrupt = result["__interrupt__"][0].value
                    self.assertEqual(interrupt["action_requests"][0]["name"], tool_name)
                    self.assertEqual(interrupt["review_configs"][0]["allowed_decisions"], ["approve", "reject"])
                    resumed = await graph.ainvoke(
                        Command(resume={"decisions": [{"type": decision}]}), config, context=context,
                    )
                    self.assertFalse(resumed.get("__interrupt__"))
                    self.assertEqual(self.executed, [tool_name] if decision == "approve" else [])

    async def test_error_middleware_preserves_interrupts_and_handles_regular_errors(self):
        middleware = main_agent.ToolErrorMiddleware()
        request = SimpleNamespace(tool_call={"id": "test-call", "name": "task"})
        with self.assertRaises(GraphInterrupt):
            middleware.wrap_tool_call(request, Mock(side_effect=GraphInterrupt()))
        with self.assertRaises(GraphInterrupt):
            await middleware.awrap_tool_call(request, AsyncMock(side_effect=GraphInterrupt()))
        with self.assertLogs("agent.middlewares.tool_error", level="WARNING"):
            sync_result = middleware.wrap_tool_call(request, Mock(side_effect=RuntimeError("测试异常")))
            async_result = await middleware.awrap_tool_call(request, AsyncMock(side_effect=RuntimeError("测试异常")))
        for result in (sync_result, async_result):
            self.assertEqual(result.status, "error")
            self.assertEqual(result.tool_call_id, "test-call")
            self.assertIn("测试异常", result.content)


if __name__ == "__main__":
    unittest.main()
