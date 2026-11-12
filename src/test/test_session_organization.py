"""会话项目、重命名与置顶元数据的单元测试。"""

import unittest
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from agent.mysql_storage import MySQLStorage
from agent.schema import ChatRequest, ProjectCreateRequest, SessionUpdateRequest
from api_view.api.chat import AgentIdleTimeout, with_idle_timeout


class FakeCursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.statements = []

    async def execute(self, sql, params=()):
        self.statements.append((sql, params))

    async def fetchone(self):
        return {"exists": 1}


class SessionOrganizationTests(unittest.IsolatedAsyncioTestCase):
    def test_project_and_session_models_reject_oversized_identifiers(self):
        self.assertEqual(ProjectCreateRequest(name="核心器件采购").name, "核心器件采购")
        with self.assertRaises(ValueError):
            ChatRequest(message="test", project_id="x" * 65)
        with self.assertRaises(ValueError):
            SessionUpdateRequest(project_id="x" * 65)

    async def test_metadata_update_persists_title_pin_and_project(self):
        storage = MySQLStorage()
        cursor = FakeCursor()

        @asynccontextmanager
        async def fake_cursor(**_kwargs):
            yield cursor

        storage.cursor = fake_cursor
        storage.project_belongs_to = AsyncMock(return_value=True)

        result = await storage.update_thread_metadata(
            "thread-1", "user-1", title="供应商比价", pinned=True,
            project_id_marker=True, project_id="project-1",
        )

        self.assertTrue(result)
        storage.project_belongs_to.assert_awaited_once_with("project-1", "user-1")
        sql, params = cursor.statements[0]
        self.assertIn("custom_title = %s", sql)
        self.assertIn("pinned = %s", sql)
        self.assertIn("project_id = %s", sql)
        self.assertEqual(params, ("供应商比价", 1, "project-1", "thread-1", "user-1"))

    async def test_foreign_project_is_rejected_before_update(self):
        storage = MySQLStorage()
        storage.project_belongs_to = AsyncMock(return_value=False)
        storage.cursor = AsyncMock(side_effect=AssertionError("不应写数据库"))

        result = await storage.update_thread_metadata(
            "thread-1", "user-1", project_id_marker=True, project_id="foreign-project",
        )

        self.assertFalse(result)
        storage.project_belongs_to.assert_awaited_once()

    async def test_project_rename_updates_only_owners_project(self):
        storage = MySQLStorage()
        cursor = FakeCursor()

        @asynccontextmanager
        async def fake_cursor(**_kwargs):
            yield cursor

        storage.cursor = fake_cursor
        result = await storage.update_project("project-1", "user-1", "网络采购中心")

        self.assertTrue(result)
        sql, params = cursor.statements[0]
        self.assertIn("updated_at = UTC_TIMESTAMP(6)", sql)
        self.assertEqual(params, ("网络采购中心", "project-1", "user-1"))

    async def test_user_thread_list_includes_sessions_without_checkpoints(self):
        storage = MySQLStorage()
        cursor = FakeCursor()

        async def fetchall():
            return [{"thread_id": "pending-thread"}]

        cursor.fetchall = fetchall

        @asynccontextmanager
        async def fake_cursor(**_kwargs):
            yield cursor

        storage.cursor = fake_cursor
        self.assertEqual(await storage.get_all_thread_ids("user-1"), ["pending-thread"])
        sql, params = cursor.statements[0]
        self.assertIn("FROM chat_threads", sql)
        self.assertNotIn("JOIN", sql)
        self.assertEqual(params, ("user-1",))

    async def test_agent_idle_timeout_returns_readable_error(self):
        async def stalled():
            await asyncio.sleep(0.05)
            yield {"type": "token"}

        with self.assertRaisesRegex(AgentIdleTimeout, "响应超时"):
            async for _ in with_idle_timeout(stalled(), seconds=0.001):
                pass

    async def test_delete_project_keeps_sessions_as_standalone(self):
        storage = MySQLStorage()
        cursor = FakeCursor()

        @asynccontextmanager
        async def fake_cursor(**kwargs):
            self.assertTrue(kwargs.get("transaction"))
            yield cursor

        storage.cursor = fake_cursor
        self.assertTrue(await storage.delete_project("project-1", "user-1"))

        self.assertEqual(len(cursor.statements), 3)
        self.assertIn("FOR UPDATE", cursor.statements[0][0])
        self.assertIn("SET project_id = NULL", cursor.statements[1][0])
        self.assertIn("DELETE FROM chat_projects", cursor.statements[2][0])
        for _, params in cursor.statements:
            self.assertEqual(params, ("project-1", "user-1"))


if __name__ == "__main__":
    unittest.main()
