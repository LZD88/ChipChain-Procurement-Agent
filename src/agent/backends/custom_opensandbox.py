"""OpenSandbox 沙箱后端实现，遵循 SandboxBackendProtocol 协议。

仅使用 opensandbox 的异步客户端（Sandbox）。命令执行采用「后台启动 + 轮询」模式：
run(background=True) 立即返回 execution_id，随后按 sync_polling_interval 轮询
get_command_status 直到命令结束，期间在客户端侧兜底强制超时。

同步原语（execute / upload_files / download_files）是 BaseSandbox 抽象方法要求的
遗留入口，实际生产路径（deepagents 的 aexecute / aread / awrite 等）全部走异步方法。
"""
from __future__ import annotations
import asyncio
import logging
import time
from collections.abc import Callable
from typing import cast

from opensandbox import Sandbox
from opensandbox.exceptions import SandboxException
from opensandbox.models import WriteEntry
from opensandbox.models.execd import RunCommandOpts

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

SyncPollingInterval = float | Callable[[float], float]
PollingStrategy = Callable[[float], float]

logger = logging.getLogger(__name__)


class OpenSandboxBackend(BaseSandbox):
    """基于 OpenSandbox 的沙箱后端（异步）。

    继承 BaseSandbox 的文件操作方法，实现异步原语 aexecute、adownload_files、
    aupload_files，以及为满足抽象方法而保留的同步桥接原语。
    """

    def __init__(
            self,
            *,
            sandbox: Sandbox,
            timeout: int = 60 * 60,
            sync_polling_interval: SyncPollingInterval = 0.1,
    ) -> None:
        """创建一个包装已有 OpenSandbox 沙盒的后端实例。

        Args：
            sandbox：指向该沙箱的 OpenSandbox 异步客户端。
            timeout：调用 `aexecute()` 且未显式指定 `timeout` 时使用的默认命令超时时间（秒）。
                0 表示不限制超时。
            sync_polling_interval：后台执行路径上，轮询 OpenSandbox 命令完成状态的间隔时间（秒）；
                也可以是一个可调用对象，接收已执行的秒数并返回下一次轮询的延迟时间。
        """
        self._sandbox = sandbox
        self._default_timeout = timeout
        polling_strategy: PollingStrategy

        # 处理轮询策略
        if callable(sync_polling_interval):
            polling_strategy = cast("PollingStrategy", sync_polling_interval)
        else:
            def polling_strategy(_elapsed: float) -> float:
                return sync_polling_interval

        self._sync_polling_interval = polling_strategy
        logger.debug("OpenSandbox 后端就绪：sandbox=%s timeout=%ss", sandbox.id, timeout)

    @property
    def id(self) -> str:
        """返回 OpenSandbox 沙盒的 ID。"""
        return self._sandbox.id

    # 沙箱中非交互式 shell 不会加载 /etc/profile，需要手动注入环境变量
    SANDBOX_PATH = (
        "/opt/skills-venv/bin:"
        "/opt/go/1.25.5/bin:"
        "/opt/node/v22.2.0/bin:"
        "/usr/lib/jvm/java-21-openjdk-amd64/bin:"
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )

    # ------------------------------------------------------------------
    # 异步原语（生产路径）
    # ------------------------------------------------------------------

    async def aexecute(
            self,
            command: str,
            *,
            timeout: int | None = None,
    ) -> ExecuteResponse:
        """在沙盒内部执行一条 Shell 命令（后台启动 + 轮询）。

        Args：
            command：要执行的 Shell 命令字符串。
            timeout：等待命令完成的最大时间（秒）。
                如果为 None，则使用后端默认的超时时间；0 表示不限制超时。
        """
        effective_timeout = timeout if timeout is not None else self._default_timeout
        wrapped = self._wrap_command(command)
        return await self._aexecute_via_polling(wrapped, timeout=effective_timeout)

    async def _aexecute_via_polling(
            self,
            command: str,
            *,
            timeout: int,
    ) -> ExecuteResponse:
        """后台执行命令并轮询其状态，直到完成或超时。

        参考 DaytonaSandbox._execute_via_session_logs 的「后台 + 轮询」思路：
        run(background=True) 立即返回 execution_id，客户端按 sync_polling_interval
        轮询 get_command_status，`exit_code` 出现即完成；超时由客户端兜底 interrupt。
        """
        started_at = time.monotonic()
        execution = await self._sandbox.commands.run(
            command,
            opts=RunCommandOpts(background=True),
        )
        execution_id = execution.id
        if not execution_id:
            return self._build_error_response(
                RuntimeError("后台命令未返回 execution_id"),
                timeout,
            )

        try:
            while True:
                # 客户端侧兜底超时：到点即中断后台命令并返回 124。
                if timeout != 0 and time.monotonic() - started_at >= timeout:
                    logger.warning("沙箱命令超时：sandbox=%s execution=%s timeout=%ss", self.id, execution_id, timeout)
                    try:
                        await self._sandbox.commands.interrupt(execution_id)
                    except SandboxException:
                        pass
                    return ExecuteResponse(
                        output=f"命令在 {timeout} 秒后超时",
                        exit_code=124,
                        truncated=False,
                    )

                status = await self._sandbox.commands.get_command_status(execution_id)
                # 完成信号：有退出码，或服务端明确标记已停止。
                if status.exit_code is not None or status.running is False:
                    break

                elapsed = time.monotonic() - started_at
                await asyncio.sleep(self._sync_polling_interval(elapsed))

            logs = await self._sandbox.commands.get_background_command_logs(execution_id)
            output = logs.content or ""
            if status.error:
                output += f"\n<stderr>{status.error}</stderr>"

            logger.debug(
                "沙箱命令完成：sandbox=%s execution=%s exit_code=%s elapsed=%.2fs",
                self.id, execution_id, status.exit_code or 0, time.monotonic() - started_at,
            )
            return ExecuteResponse(
                output=output,
                exit_code=status.exit_code or 0,
                truncated=False,
            )
        except SandboxException:
            # 沙箱不可达/不健康/API 错误：必须上抛，让 SandboxHealthMiddleware
            # 与 sandbox_manager 检测到沙箱已失效并触发重建。
            raise
        except Exception as e:
            from agent.backends.sandbox_healthcheck import invalidate_health, is_infrastructure_error
            if is_infrastructure_error(e):
                invalidate_health(self)
            return self._build_error_response(e, timeout)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱下载指定文件（原生异步）。

        Args:
            paths: 沙箱中的绝对文件路径列表。

        Returns:
            与 paths 顺序对应的响应列表，包含文件内容或错误信息。
        """
        responses: list[FileDownloadResponse] = []

        for path in paths:
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            try:
                # read_bytes 直接返回 bytes，避免 str→bytes 的编解码往返。
                content = await self._sandbox.files.read_bytes(path)
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )
            except Exception as exc:
                from agent.backends.sandbox_healthcheck import invalidate_health, is_infrastructure_error
                if is_infrastructure_error(exc):
                    invalidate_health(self)
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )

        return responses

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙箱（原生异步）。

        Args:
            files: 每个元素为 (绝对路径, 文件内容字节) 的元组列表。

        Returns:
            与 files 顺序对应的响应列表，包含操作错误信息（成功时为 None）。
        """
        responses: list[FileUploadResponse] = []
        upload_entries: list[WriteEntry] = []

        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                # 将 bytes 转换为字符串用于写入
                if isinstance(content, bytes):
                    try:
                        content_str = content.decode("utf-8")
                    except UnicodeDecodeError:
                        content_str = content.decode("latin-1")
                else:
                    content_str = str(content)
                upload_entries.append(WriteEntry(path=path, data=content_str, mode=0o644))
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                responses.append(FileUploadResponse(path=path, error=str(e)))

        if upload_entries:
            try:
                await self._sandbox.files.write_files(upload_entries)
            except SandboxException:
                # 沙箱不可达：上抛，交由健康检查触发重建（与 aexecute 一致）。
                raise
            except Exception as e:
                # 其他写失败：将所有成功准备但未真正上传的条目标记为错误
                from agent.backends.sandbox_healthcheck import invalidate_health, is_infrastructure_error
                if is_infrastructure_error(e):
                    invalidate_health(self)
                for resp in responses:
                    if resp.error is None:
                        resp.error = f"upload_failed: {e}"

        return responses

    # ------------------------------------------------------------------
    # 同步桥接原语（BaseSandbox 抽象方法要求的遗留入口，生产路径不使用）
    # ------------------------------------------------------------------

    def execute(
            self,
            command: str,
            *,
            timeout: int | None = None,
    ) -> ExecuteResponse:
        """同步桥接：内部运行异步 aexecute。仅用于非事件循环的线程上下文。"""
        return asyncio.run(self.aexecute(command, timeout=timeout))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """同步桥接：内部运行异步 adownload_files。"""
        return asyncio.run(self.adownload_files(paths))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """同步桥接：内部运行异步 aupload_files。"""
        return asyncio.run(self.aupload_files(files))

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _wrap_command(self, command: str) -> str:
        # 非交互式 shell 不会 source /etc/profile，需要注入 PATH 确保 pip/python 可用
        return f'export PATH="{self.SANDBOX_PATH}:$PATH" && {command}'

    @staticmethod
    def _build_error_response(e: Exception, timeout: int) -> ExecuteResponse:
        """将非沙箱级异常转换为 ExecuteResponse，超时单独识别。"""
        error_msg = str(e)
        if "timeout" in error_msg.lower():
            logger.warning("命令在 %s 秒后超时", timeout)
            return ExecuteResponse(
                output=f"命令在 {timeout} 秒后超时",
                exit_code=124,
                truncated=False,
            )

        logger.error("执行沙箱命令失败", exc_info=e)
        return ExecuteResponse(
            output=f"执行命令时出错：{error_msg}",
            exit_code=1,
            truncated=False,
        )
