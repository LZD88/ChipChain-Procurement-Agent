import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from agent.backends import sandbox_healthcheck as health
from agent.backends.sandbox_proxy import SandboxBackendProxy


class HealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        health._success.clear()
        health._probes.clear()
        self.backend = SimpleNamespace(id="health-test", aexecute=AsyncMock(
            return_value=SimpleNamespace(exit_code=0, output="ok")))

    async def test_cache_shared_by_raw_backend_and_proxy_and_expires(self):
        proxy = SandboxBackendProxy(self.backend)
        for ttl in (30, 45, 60):
            health._success.clear()
            self.backend.aexecute.reset_mock()
            with patch.dict("os.environ", {"SANDBOX_HEALTH_CACHE_TTL_SECONDS": str(ttl)}):
                with patch.object(health.time, "monotonic", return_value=100):
                    self.assertTrue(await health.check_sandbox(self.backend))
                with patch.object(health.time, "monotonic", return_value=100 + ttl - 1):
                    self.assertTrue(await health.check_sandbox(proxy))
                self.assertEqual(self.backend.aexecute.await_count, 1)
                with patch.object(health.time, "monotonic", return_value=100 + ttl):
                    self.assertTrue(await health.check_sandbox(proxy))
                self.assertEqual(self.backend.aexecute.await_count, 2)

    async def test_single_flight_and_zero_disables_reuse(self):
        await asyncio.gather(*(health.check_sandbox(self.backend) for _ in range(10)))
        self.assertEqual(self.backend.aexecute.await_count, 1)
        with patch.dict("os.environ", {"SANDBOX_HEALTH_CACHE_TTL_SECONDS": "0"}):
            await health.check_sandbox(self.backend)
            await health.check_sandbox(self.backend)
        self.assertEqual(self.backend.aexecute.await_count, 3)

    async def test_transport_invalidates_but_nonzero_does_not(self):
        proxy = SandboxBackendProxy(self.backend)
        await health.check_sandbox(proxy)
        self.backend.aexecute.return_value = SimpleNamespace(exit_code=2, output="bad user code")
        await proxy.aexecute("user-code")
        self.assertTrue(health._success)
        self.backend.aexecute.side_effect = ValueError("business refusal")
        with self.assertRaises(ValueError):
            await proxy.aexecute("business")
        self.assertTrue(health._success)
        self.backend.aexecute.side_effect = ConnectionError("gone")
        with self.assertRaises(ConnectionError):
            await proxy.aexecute("read")
        self.assertFalse(health._success)
        self.assertFalse(await health.check_sandbox(proxy))
        self.assertFalse(health._success)

    async def test_generation_replacement_cannot_use_stale_success(self):
        proxy = SandboxBackendProxy(self.backend)
        await health.check_sandbox(proxy)
        other = SimpleNamespace(id="health-test", aexecute=AsyncMock(
            return_value=SimpleNamespace(exit_code=1, output="failed")))
        proxy.replace_backend(other)
        self.assertEqual(proxy.generation, 1)
        self.assertFalse(await health.check_sandbox(proxy))
        other.aexecute.assert_awaited_once()

    async def test_transport_failure_during_probe_is_false_not_caller_cancellation(self):
        self.backend.aexecute.side_effect = ConnectionError("offline")
        self.assertFalse(await health.check_sandbox(SandboxBackendProxy(self.backend)))
        self.assertFalse(health._success)
