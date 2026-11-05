"""离线验证日志输出、重复配置及应用生命周期；不连接外部服务。"""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from textwrap import dedent
import unittest


SRC = Path(__file__).resolve().parents[1]


class LoggingTests(unittest.TestCase):
    def run_script(self, source, *, env=None, cwd=None):
        # 每个用例独立进程，避免修改 unittest 或第三方模块的全局日志状态。
        return subprocess.run(
            [sys.executable, "-B", "-c", dedent(source)],
            env={
                **os.environ,
                "PYTHONPATH": str(SRC),
                "APP_ENV": "development",
                "LOG_LEVEL": "INFO",
                "DEEPSEEK_API_KEY": "test-key",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
                **(env or {}),
            },
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    def test_default_output_is_concise_and_keeps_third_party_errors(self):
        result = self.run_script("""
            import logging
            from agent.logging_config import configure_logging
            configure_logging()
            logging.getLogger('agent.tools.mcp_client').info('tools-ready')
            logging.getLogger('api_view.agent_loader').debug('loader-detail')
            logging.getLogger('httpx').info('http-request-detail')
            logging.getLogger('httpx').warning('http-warning')
            logging.getLogger('agent.backends.custom_opensandbox').error('sandbox-failure')
        """)
        self.assertEqual(result.stdout, "")
        for message in ("tools-ready", "http-warning", "sandbox-failure"):
            self.assertEqual(result.stderr.count(message), 1)
        self.assertNotIn("loader-detail", result.stderr)
        self.assertNotIn("http-request-detail", result.stderr)

    def test_debug_configuration_can_be_repeated_without_duplicate_output(self):
        result = self.run_script("""
            import logging
            from agent.logging_config import configure_logging
            configure_logging()
            configure_logging()
            from agent.backends import custom_opensandbox
            custom_opensandbox.logger.debug('sandbox-detail')
            logging.getLogger('api_view.agent_loader').debug('loader-detail')
            logging.getLogger('httpx').info('http-request-detail')
        """, env={"LOG_LEVEL": " debug "})
        self.assertEqual(result.stderr.count("sandbox-detail"), 1)
        self.assertEqual(result.stderr.count("loader-detail"), 1)
        self.assertNotIn("http-request-detail", result.stderr)

    def test_importing_business_modules_does_not_configure_root_logger(self):
        result = self.run_script("""
            import logging
            from io import StringIO
            root = logging.getLogger()
            handler = logging.StreamHandler(StringIO())
            root.addHandler(handler)
            original_handlers = root.handlers[:]
            original_level = root.level
            from agent import main_agent
            from agent.backends import custom_opensandbox
            assert root.handlers == original_handlers
            assert root.level == original_level
            assert not custom_opensandbox.logger.handlers
            assert custom_opensandbox.logger.level == logging.NOTSET
        """)
        self.assertEqual(result.stdout, "")

    def test_production_keeps_console_and_appends_to_log_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "erp_agent.log"
            path.write_text("existing-entry\n", encoding="utf-8")
            result = self.run_script("""
                import logging
                from agent.logging_config import configure_logging
                configure_logging()
                configure_logging()
                logging.getLogger('agent.main_agent').info('生产日志')
            """, env={"APP_ENV": "production"}, cwd=directory)
            content = path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("existing-entry\n"))
        self.assertEqual(content.count("生产日志"), 1)
        self.assertEqual(result.stderr.count("生产日志"), 1)

    def test_timeout_and_error_are_each_reported_once(self):
        result = self.run_script("""
            from agent.logging_config import configure_logging
            configure_logging()
            from agent.backends.custom_opensandbox import OpenSandboxBackend
            response = OpenSandboxBackend._build_error_response(TimeoutError('timeout'), 5)
            assert response.exit_code == 124
            try:
                raise ValueError('bad-command-response')
            except ValueError as exc:
                response = OpenSandboxBackend._build_error_response(exc, 5)
            assert response.exit_code == 1
            assert 'bad-command-response' in response.output
        """)
        self.assertEqual(result.stderr.count("[WARNING]"), 1)
        self.assertEqual(result.stderr.count("[ERROR]"), 1)
        self.assertEqual(result.stderr.count("Traceback"), 1)

    def test_lifespan_initializes_and_cleans_up_even_on_error(self):
        result = self.run_script("""
            import asyncio
            from unittest.mock import AsyncMock, patch
            from api_view import web_main

            async def check():
                for fail in (False, True):
                    loader = AsyncMock()
                    with patch.object(web_main, 'agent_loader', loader):
                        try:
                            async with web_main.lifespan(web_main.app):
                                loader.initialize.assert_awaited_once()
                                loader.shutdown.assert_not_awaited()
                                if fail:
                                    raise RuntimeError('request-failed')
                        except RuntimeError as exc:
                            assert str(exc) == 'request-failed'
                    loader.shutdown.assert_awaited_once()
            asyncio.run(check())
        """)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("正在初始化采购助手"), 2)
        self.assertEqual(result.stderr.count("采购助手就绪"), 2)
        self.assertEqual(result.stderr.count("采购助手资源已释放"), 2)

    def test_precompute_logs_one_summary_and_preserves_extra_tools(self):
        result = self.run_script("""
            import asyncio
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, patch
            from agent.logging_config import configure_logging
            from agent import main_agent
            configure_logging()

            async def check():
                business = [SimpleNamespace(name=name) for name in (
                    'supplier_query', 'part_query', 'order_create', 'order_search_details',
                )]
                chart = SimpleNamespace(
                    name='generate_bar_chart',
                    args_schema={'type': 'object', 'properties': {'data': {'type': 'array'}}},
                )
                spreadsheet = SimpleNamespace(name='generate_spreadsheet')
                client = SimpleNamespace(get_tools=AsyncMock(side_effect=[business, [chart, spreadsheet]]))
                with patch('agent.tools.mcp_client.MultiServerMCPClient', return_value=client), \
                     patch('agent.tools.mcp_client.MCP_SERVER_CONFIG', {'analysis': {'url': 'http://offline.invalid'}}):
                    context = await main_agent.precompute_agent_context()
                assert len(context.analyst_mcp_tools) == 2
                assert len(context.order_mcp_tools) == 2
                assert context.chart_tool.name == 'generate_chart'
                assert context.extra_mcp_tools == [spreadsheet]
            asyncio.run(check())
        """)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("[INFO]"), 1)
        self.assertIn("分析 2，订单 2，可视化 1→1，独立工具 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
