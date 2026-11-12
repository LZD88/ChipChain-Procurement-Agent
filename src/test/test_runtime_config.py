import asyncio
import unittest
from unittest.mock import patch
from prometheus_client import generate_latest
from agent.runtime_config import RuntimeConfig
from api_view import observability as metrics


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_and_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(RuntimeConfig.from_env(), RuntimeConfig())
        with patch.dict("os.environ", {
            "SANDBOX_HEALTH_CACHE_TTL_SECONDS": "0", "SANDBOX_PREWARM_TARGET": "0",
            "AGENT_GRAPH_CACHE_ENABLED": "false", "AGENT_BACKGROUND_PREPARE_ENABLED": "false",
        }, clear=True):
            cfg = RuntimeConfig.from_env()
            self.assertEqual(cfg.health_ttl, 0)
            self.assertFalse(cfg.graph_enabled or cfg.background_enabled)
        with patch.dict("os.environ", {"AGENT_REQUEST_ROUTING_ENABLED": "false"}, clear=True):
            self.assertFalse(RuntimeConfig.from_env().request_routing_enabled)
        with patch.dict("os.environ", {"AGENT_REQUEST_ROUTER_LLM_ENABLED": "false",
                                       "AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS": "5"}, clear=True):
            self.assertFalse(RuntimeConfig.from_env().request_router_llm_enabled)
            self.assertEqual(RuntimeConfig.from_env().request_router_timeout_seconds, 5)

    def test_invalid_and_boundary_settings(self):
        for settings in ({"SANDBOX_HEALTH_CACHE_TTL_SECONDS": "29"},
                         {"SANDBOX_PREWARM_TARGET": "5"},
                         {"SANDBOX_PREWARM_TARGET": "1", "SANDBOX_PREWARM_CONCURRENCY": "2"},
                         {"AGENT_PREPARE_MAX_PENDING": "1"},
                         {"AGENT_GRAPH_CACHE_ENABLED": "maybe"},
                         {"AGENT_REQUEST_ROUTING_ENABLED": "maybe"},
                         {"AGENT_REQUEST_ROUTER_LLM_ENABLED": "maybe"},
                         {"AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS": "0"},
                         {"AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS": "11"},
                         {"AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS": "nan"},
                         {"AGENT_GRAPH_CACHE_MAX_USERS": "0"}):
            with self.subTest(settings=settings), patch.dict("os.environ", settings, clear=True):
                with self.assertRaises(ValueError):
                    RuntimeConfig.from_env()
        for ttl in (30, 60):
            with patch.dict("os.environ", {"SANDBOX_HEALTH_CACHE_TTL_SECONDS": str(ttl)}, clear=True):
                self.assertEqual(RuntimeConfig.from_env().health_ttl, ttl)

    def test_measure_outcomes_and_export_labels(self):
        with patch.object(metrics.time, "perf_counter", side_effect=[10, 12]):
            with metrics.preparation_measure(metrics.PREPARATION_WAIT, "chat"):
                pass
        for error in (ValueError(), asyncio.CancelledError(), TimeoutError()):
            with self.assertRaises(type(error)):
                with metrics.preparation_measure(metrics.PREPARATION_WAIT, "resume"):
                    raise error
        exported = generate_latest().decode()
        self.assertIn('agent_preparation_wait_seconds_bucket{endpoint="chat",le="45.0",outcome="ready"}', exported)
        self.assertIn('endpoint="resume",outcome="timeout"', exported)
        for metric in (metrics.PREPARATION_DURATION, metrics.PREPARATION_WAIT,
                       metrics.PREPARATION_STAGE, metrics.RUNTIME_CACHE):
            for sample in metric.collect()[0].samples:
                self.assertFalse({"user_id", "thread_id", "sandbox_id", "token"} & sample.labels.keys())
