"""技能上传的离线回归测试：根目录参考文件、嵌套技能和增量同步。"""

import os
from pathlib import Path
import re
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from deepagents.backends.protocol import (
    ExecuteResponse, FileDownloadResponse, FileUploadResponse, SandboxBackendProtocol,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
    from agent.backends import sandbox_setup
    from agent.middlewares import skills_sync
    from agent.tools.chart_generator import _build_tool_description


class SkillsUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.skills_dir = Path(directory.name)
        self.reference_path = "/skills/analyis_chart_params.md"
        self.expected = {
            self.reference_path: "# 图表参数\nline: {time, value}\n".encode(),
            "/skills/main/report/SKILL.md": b"# Main skill",
            "/skills/procurement/analysis/SKILL.md": b"# Procurement skill",
            "/skills/procurement/analysis/references/example data.csv": b"time,value\n1,2\n",
            "/skills/order/purchase/SKILL.md": b"# Order skill",
            "/skills/empty.md": b"",
        }
        for path, content in self.expected.items():
            local = self.skills_dir / path.removeprefix("/skills/")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
        for module in (sandbox_setup, skills_sync):
            patcher = patch.object(module, "LOCAL_SKILLS_DIR", self.skills_dir)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.remote = {}
        self.backend = AsyncMock(spec=SandboxBackendProtocol)
        self.backend.aexecute.side_effect = self.check_file
        self.backend.adownload_files.side_effect = lambda paths: [
            FileDownloadResponse(path=path, content=self.remote[path]) for path in paths
        ]
        self.backend.aupload_files.side_effect = self.upload_files

    def check_file(self, command, **_kwargs):
        args = shlex.split(command)
        self.assertEqual(args[:2], ["test", "-f"])
        self.assertEqual(len(args), 3)
        return ExecuteResponse(output="", exit_code=0 if args[2] in self.remote else 1)

    def upload_files(self, files):
        self.remote.update(files)
        return [FileUploadResponse(path=path) for path, _ in files]

    async def test_seed_uploads_root_references_and_all_nested_skills(self):
        await sandbox_setup._seed_files(self.backend)
        self.assertEqual(self.remote, self.expected)
        self.backend.aupload_files.assert_awaited_once()

    async def test_seed_skips_identical_files_and_refreshes_changed_reference(self):
        self.remote.update(self.expected)
        await sandbox_setup._seed_files(self.backend)
        self.backend.aupload_files.assert_not_awaited()

        self.remote[self.reference_path] = b"outdated reference"
        await sandbox_setup._seed_files(self.backend)
        self.backend.aupload_files.assert_awaited_once_with([
            (self.reference_path, self.expected[self.reference_path]),
        ])
        self.assertEqual(self.remote, self.expected)

    async def test_runtime_repairs_missing_reference_in_existing_sandbox(self):
        self.remote.update(self.expected)
        del self.remote[self.reference_path]
        middleware = skills_sync.SkillsSyncMiddleware(self.backend)

        notification = await middleware.abefore_agent({}, None)
        self.assertIn("analyis_chart_params.md", notification["messages"][0].content)
        self.backend.aupload_files.assert_awaited_once_with([
            (self.reference_path, self.expected[self.reference_path]),
        ])
        self.assertEqual(self.remote, self.expected)

        self.assertIsNone(await middleware.abefore_agent({}, None))
        self.assertEqual(self.backend.aupload_files.await_count, 1)

    async def test_runtime_uploads_new_files_and_modified_root_and_nested_files(self):
        middleware = skills_sync.SkillsSyncMiddleware(self.backend)
        await middleware._sync_files()
        self.assertEqual(self.remote, self.expected)
        self.backend.aupload_files.reset_mock()

        changes = {
            self.reference_path: b"updated chart reference",
            "/skills/procurement/analysis/SKILL.md": b"updated analysis skill",
            "/skills/new reference.md": b"new shared reference",
        }
        for path, content in changes.items():
            (self.skills_dir / path.removeprefix("/skills/")).write_bytes(content)

        updated = await middleware._sync_files()
        self.assertEqual(set(updated), {"analyis_chart_params.md", "procurement", "new reference.md"})
        self.assertEqual(dict(self.backend.aupload_files.call_args.args[0]), changes)
        self.assertEqual(self.remote, self.expected | changes)

    async def test_failed_sync_does_not_cache_or_report_success_and_can_retry(self):
        for failure in ("exception", "file_error", "missing_response"):
            with self.subTest(failure=failure):
                self.remote.clear()
                middleware = skills_sync.SkillsSyncMiddleware(self.backend)

                def fail_upload(files):
                    if failure == "exception":
                        raise RuntimeError("offline upload failure")
                    return [
                        FileUploadResponse(path=path, error="permission_denied")
                        for path, _ in files
                    ] if failure == "file_error" else []

                self.backend.aupload_files.side_effect = fail_upload
                with self.assertRaises(RuntimeError):
                    await middleware.abefore_agent({}, None)
                self.assertEqual(middleware._last_hashes, {})

                self.backend.aupload_files.side_effect = self.upload_files
                notification = await middleware.abefore_agent({}, None)
                self.assertIn("analyis_chart_params.md", notification["messages"][0].content)
                self.assertEqual(self.remote, self.expected)

    async def test_seed_rejects_failed_or_missing_upload_responses(self):
        for results in ([FileUploadResponse(path=self.reference_path, error="permission_denied")], []):
            with self.subTest(results=results):
                self.backend.aupload_files.side_effect = None
                self.backend.aupload_files.return_value = results
                with self.assertRaisesRegex(RuntimeError, "analyis_chart_params.md"):
                    await sandbox_setup._seed_files(self.backend)

    async def test_chart_tool_references_resolve_to_uploaded_files(self):
        await sandbox_setup._seed_files(self.backend)
        description = _build_tool_description({"line": object()})
        references = re.findall(r'/skills/[^"`\s)]+\.md', description)
        self.assertTrue(references)
        for path in references:
            self.assertEqual(self.remote[path], self.expected[self.reference_path])

    async def test_generation_resets_cached_hashes_and_concurrent_sync_is_shared(self):
        import asyncio
        from agent.backends.sandbox_proxy import SandboxBackendProxy
        proxy = SandboxBackendProxy(self.backend)
        middleware = skills_sync.SkillsSyncMiddleware(proxy)
        await asyncio.gather(middleware._sync_files(), middleware._sync_files())
        self.assertEqual(self.backend.aupload_files.await_count, 1)
        self.remote.clear()
        self.backend.aupload_files.reset_mock()
        proxy.replace_backend(self.backend)
        await middleware._sync_files()
        self.assertEqual(self.remote, self.expected)
        self.backend.aupload_files.assert_awaited_once()

    async def test_persisted_restore_tracks_content_success_and_generation(self):
        from types import SimpleNamespace
        from agent.backends.sandbox_proxy import SandboxBackendProxy
        from agent.middlewares.user_skills_restore import UserSkillsRestoreMiddleware
        proxy = SandboxBackendProxy(self.backend)
        middleware = UserSkillsRestoreMiddleware(proxy, ("skills",))
        item = SimpleNamespace(key="main/custom/SKILL.md", value={"content": "v1"})
        store = SimpleNamespace(asearch=AsyncMock(return_value=[item]))
        await middleware.sync(store)
        await middleware.sync(store)
        self.assertEqual(self.backend.aupload_files.await_count, 1)
        item.value = {"content": "v2"}
        self.backend.aupload_files.side_effect = lambda files: []
        with self.assertRaises(RuntimeError):
            await middleware.sync(store)
        self.backend.aupload_files.side_effect = self.upload_files
        await middleware.sync(store)
        self.assertEqual(self.remote["/skills/main/custom/SKILL.md"], b"v2")
        self.remote.clear()
        proxy.replace_backend(self.backend)
        await middleware.sync(store)
        self.assertEqual(self.remote["/skills/main/custom/SKILL.md"], b"v2")


if __name__ == "__main__":
    unittest.main()
