"""
SandboxBackendProxy — 稳定句柄，内部 backend 可热替换。

显式代理 SandboxBackendProtocol 的文件和命令方法及 id property，
确保 Python MRO 不会找到协议层的 NotImplementedError 默认实现。
"""
from __future__ import annotations
from functools import wraps


def _guard_async(method):
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        from agent.backends.sandbox_healthcheck import invalidate_health, is_infrastructure_error
        try:
            return await method(self, *args, **kwargs)
        except Exception as exc:
            if is_infrastructure_error(exc):
                invalidate_health(self)
            raise
    return guarded

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)


class SandboxBackendProxy(SandboxBackendProtocol):
    """代理所有 SandboxBackendProtocol 方法到内部 backend，支持热替换。"""

    def __init__(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend
        self.generation = 0

    @property
    def id(self) -> str:
        return self._backend.id

    def replace_backend(self, backend: SandboxBackendProtocol) -> None:
        from agent.backends.sandbox_healthcheck import invalidate_health
        invalidate_health(self._backend)
        self._backend = backend
        self.generation += 1

    # ---- 同步方法 ----

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._backend.execute(command, timeout=timeout)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._backend.read(file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._backend.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return self._backend.edit(file_path, old_string, new_string, replace_all)

    def ls(self, path: str) -> LsResult:
        return self._backend.ls(path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._backend.glob(pattern, path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None,
             *, max_count: int | None = None) -> GrepResult:
        return self._backend.grep(pattern, path, glob, max_count=max_count)

    def delete(self, file_path: str) -> DeleteResult:
        return self._backend.delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._backend.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._backend.download_files(paths)

    # ---- 异步方法 ----

    @_guard_async
    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await self._backend.aexecute(command, timeout=timeout)

    @_guard_async
    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._backend.aread(file_path, offset, limit)

    @_guard_async
    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._backend.awrite(file_path, content)

    @_guard_async
    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return await self._backend.aedit(file_path, old_string, new_string, replace_all)

    @_guard_async
    async def als(self, path: str) -> LsResult:
        return await self._backend.als(path)

    @_guard_async
    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._backend.aglob(pattern, path)

    @_guard_async
    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None,
                    *, max_count: int | None = None) -> GrepResult:
        return await self._backend.agrep(pattern, path, glob, max_count=max_count)

    @_guard_async
    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._backend.adelete(file_path)

    @_guard_async
    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._backend.aupload_files(files)

    @_guard_async
    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._backend.adownload_files(paths)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._backend, name)
