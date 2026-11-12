import asyncio
from types import SimpleNamespace
from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from api_view.api import auth
from api_view import auth as sessions


class PreparationAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.loader = SimpleNamespace(schedule_preparation=Mock(), logout_preparation=AsyncMock())
        self.storage = SimpleNamespace(create_user=AsyncMock(return_value=True),
            get_user=AsyncMock(return_value={"username": "alice", "password_hash": "test"}),
            create_auth_session=AsyncMock(), delete_auth_session=AsyncMock(),
            get_auth_user=AsyncMock(return_value={"username": "alice"}),
            has_auth_sessions=AsyncMock(return_value=False))
        self.stack.enter_context(patch.object(auth, "agent_loader", self.loader))
        for module in (auth, sessions):
            self.stack.enter_context(patch.object(module, "mysql_storage", self.storage))
        self.stack.enter_context(patch.object(auth, "hash_password", return_value="hash"))
        self.valid = self.stack.enter_context(patch.object(auth, "verify_password", return_value=True))
        app = FastAPI()
        app.include_router(auth.router, prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                  headers={"X-Requested-With": "DeepAgent"})

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_login_register_and_restore_schedule_without_awaiting(self):
        # Schedule is synchronous; any attempt to await setup would exceed timeout.
        for path, status in (("login", 200), ("register", 201)):
            result = await asyncio.wait_for(self.client.post(f"/api/auth/{path}",
                json={"username": "alice", "password": "test-password"}), .5)
            self.assertEqual(result.status_code, status)
            self.assertIn(sessions.COOKIE_NAME, result.cookies)
            self.assertEqual(result.json(), {"user": {"username": "alice"}})
        result = await self.client.get("/api/auth/me")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.loader.schedule_preparation.call_count, 3)

    async def test_invalid_credentials_and_expired_session_do_not_schedule(self):
        self.valid.return_value = False
        result = await self.client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
        self.assertEqual(result.status_code, 401)
        self.client.cookies.set(sessions.COOKIE_NAME, "expired", path="/api")
        self.storage.get_auth_user.return_value = None
        result = await self.client.get("/api/auth/me")
        self.assertEqual(result.status_code, 401)
        self.loader.schedule_preparation.assert_not_called()

    async def test_logout_preserves_preparation_for_another_valid_session(self):
        for remaining in (True, False):
            self.client.cookies.set(sessions.COOKIE_NAME, "valid", path="/api")
            self.storage.has_auth_sessions.return_value = remaining
            result = await self.client.post("/api/auth/logout")
            self.assertEqual(result.status_code, 204)
        self.loader.logout_preparation.assert_awaited_once_with("alice")
