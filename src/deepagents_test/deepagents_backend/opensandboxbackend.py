"""OpenSandbox sandbox backend implementation."""

from contextlib import closing
from datetime import timedelta
from uuid import uuid4

import httpx
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from opensandbox import SandboxSync

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.config import ConnectionConfigSync
from opensandbox.models import NetworkPolicy, NetworkRule, WriteEntry
from opensandbox.models.execd import RunCommandOpts

from agent.env_utils import GLM_BASE_URL, GLM_API_KEY, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from pathlib import Path

from langchain_core.tools import tool

class OpenSandboxBackend(BaseSandbox):
    """OpenSandbox implementation conforming to SandboxBackendProtocol.

    Inherits all file operation helpers from BaseSandbox (ls, read_file,
    write_file, glob, grep, etc.) and only implements the three primitives
    that those helpers are built on: execute(), upload_files(), and
    download_files().
    """

    def __init__(
        self,
        *,
        sandbox: SandboxSync,
        timeout: int = 30 * 60,
    ) -> None:
        """Wrap an existing OpenSandbox sandbox.

        Args:
            sandbox: Existing OpenSandbox synchronous sandbox instance.
            timeout: Default command timeout in seconds used when execute()
                is called without an explicit timeout.
        """
        self._sandbox = sandbox
        self._default_timeout = timeout

    @property
    def id(self) -> str:
        """Return the OpenSandbox sandbox id."""
        return self._sandbox.id

    # ------------------------------------------------------------------
    # Primitive 1: execute
    # ------------------------------------------------------------------

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox.

        Args:
            command: Shell command string to execute.
            timeout: Maximum time in seconds to wait for completion.
                If None, uses the backend's default timeout.
                A timeout of 0 waits indefinitely.

        Returns:
            ExecuteResponse with combined stdout/stderr output and exit code.
        """
        effective_timeout = (
            timeout if timeout is not None else self._default_timeout
        )
        return self._execute(command, timeout=effective_timeout)

    def _execute(
        self,
        command: str,
        *,
        timeout: int,
    ) -> ExecuteResponse:
        """Run a command and wait for it to complete.

        OpenSandbox's synchronous ``commands.run`` blocks until a foreground
        command finishes and returns an ``Execution`` with ``exit_code`` set.
        A non-zero timeout is enforced server-side; ``timeout == 0`` waits
        indefinitely.
        """
        opts = (
            RunCommandOpts(timeout=timedelta(seconds=timeout))
            if timeout != 0
            else None
        )

        try:
            execution = self._sandbox.commands.run(command, opts=opts)
        except Exception as exc:
            return ExecuteResponse(
                output=f"Command execution failed: {exc}",
                exit_code=1,
                truncated=False,
            )

        return self._build_execute_response(execution)

    @staticmethod
    def _build_execute_response(execution) -> ExecuteResponse:
        """Convert an OpenSandbox execution result to ExecuteResponse."""
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        if execution.logs is not None:
            if execution.logs.stdout:
                stdout_parts.extend(entry.text for entry in execution.logs.stdout)
            if execution.logs.stderr:
                stderr_parts.extend(entry.text for entry in execution.logs.stderr)

        output = "".join(stdout_parts)

        if stderr_parts:
            stderr_text = "".join(stderr_parts).strip()
            if stderr_text:
                output += f"\n<stderr>{stderr_text}</stderr>"

        return ExecuteResponse(
            output=output,
            exit_code=execution.exit_code if execution.exit_code is not None else 0,
            truncated=False,
        )

    # ------------------------------------------------------------------
    # Primitive 2: download_files
    # ------------------------------------------------------------------

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox as bytes.

        Args:
            paths: List of absolute file paths inside the sandbox.

        Returns:
            One FileDownloadResponse per requested path, preserving order.
            Invalid paths return error="invalid_path"; missing files
            return error="file_not_found".
        """
        responses: list[FileDownloadResponse] = []

        for path in paths:
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(
                        path=path, content=None, error="invalid_path"
                    )
                )
                continue

            try:
                content = self._sandbox.files.read_bytes(path)
            except Exception as exc:
                error = (
                    "file_not_found"
                    if getattr(exc, "status_code", None) == 404
                    else f"download_failed: {exc}"
                )
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=error)
                )
            else:
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )

        return responses

    # ------------------------------------------------------------------
    # Primitive 3: upload_files
    # ------------------------------------------------------------------

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        """Upload files into the sandbox.

        Args:
            files: List of (absolute_path, content_bytes) tuples.

        Returns:
            One FileUploadResponse per requested file, preserving order.
            Invalid paths return error="invalid_path".
        """
        responses: list[FileUploadResponse] = []
        write_entries: list[WriteEntry] = []
        valid_indices: list[int] = []

        for i, (path, content) in enumerate(files):
            if not path.startswith("/"):
                responses.append(
                    FileUploadResponse(path=path, error="invalid_path")
                )
                continue

            write_entries.append(
                WriteEntry(path=path, data=content, mode=0o644)
            )
            valid_indices.append(i)
            responses.append(FileUploadResponse(path=path, error=None))

        if not write_entries:
            return responses

        try:
            self._sandbox.files.write_files(write_entries)
        except Exception as exc:
            # Mark all pending uploads as failed.
            for idx in valid_indices:
                responses[idx] = FileUploadResponse(
                    path=responses[idx].path,
                    error=f"upload_failed: {exc}",
                )

        return responses

def run_chat(agent):
    """循环读取输入，仅将主模型的正文增量输出到控制台。"""
    config = {"configurable": {"thread_id": uuid4().hex}}
    print("OpenSandBoxBackend 聊天已启动。输入 exit / quit / 退出结束，/new 开启新会话。")
    print("聊天按会话保存；虚拟文件在本次运行的所有会话间共享，退出程序后清空。\n")

    try:
        while True:
            user_input = input("你：").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "/exit", "/quit", "退出"}:
                break
            if user_input.lower() == "/new":
                config = {"configurable": {"thread_id": uuid4().hex}}
                print("已开启新会话。\n")
                continue

            print("助手：", end="", flush=True)
            try:
                # 每轮只提交新消息，历史由相同 thread_id 的检查点自动恢复。
                with closing(agent.stream(
                        {"messages": [{"role": "user", "content": user_input}]},
                        config=config,
                        stream_mode="messages",
                )) as stream:
                    for message, metadata in stream:
                        # messages 流还可能包含工具结果及中间件消息。
                        if isinstance(message, AIMessage) and metadata.get("langgraph_node") == "model":
                            if message.text:
                                print(message.text, end="", flush=True)
                print("\n")
            except Exception as error:
                print(f"\n本轮调用失败：{error}")
                print("可继续输入；如需清除本轮状态，请输入 /new。\n")
    except (EOFError, KeyboardInterrupt):
        print()

    print("聊天已结束。")


def build_file_transfer_tools(backend: "OpenSandboxBackend"):
    """把后端的 download_files / upload_files 原语暴露成智能体工具。"""

    @tool
    def download_file_to_local(sandbox_path: str, local_path: str) -> str:
        """将沙箱内的文件下载到本地文件系统。

        Args:
            sandbox_path: 沙箱内文件的绝对路径，例如 /tmp/result.csv
            local_path: 本地目标路径，例如 ./output/result.csv
        """
        responses = backend.download_files([sandbox_path])
        resp = responses[0]
        if resp.error:
            return f"下载失败：{resp.error}"
        if resp.content is None:
            return "下载失败：内容为空"
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
        return f"已下载 {sandbox_path} → {local_path}（{len(resp.content)} 字节）"

    @tool
    def upload_file_from_local(local_path: str, sandbox_path: str) -> str:
        """将本地文件上传到沙箱。

        Args:
            local_path: 本地文件路径，例如 ./data/input.csv
            sandbox_path: 沙箱内目标绝对路径，例如 /tmp/input.csv
        """
        source = Path(local_path)
        if not source.is_file():
            return f"上传失败：本地文件不存在 {local_path}"
        content = source.read_bytes()
        responses = backend.upload_files([(sandbox_path, content)])
        resp = responses[0]
        if resp.error:
            return f"上传失败：{resp.error}"
        return f"已上传 {local_path} → {sandbox_path}（{len(content)} 字节）"

    return [download_file_to_local, upload_file_from_local]


if __name__ == '__main__':
    _PYTHON_VERSION = "3.11"
    image = "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2"
    config = ConnectionConfigSync(
        domain="http://localhost:8080",
        api_key="2016",
        # use_server_proxy=True,
        request_timeout=timedelta(seconds=60),
        transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=20)),
    )
    sandbox = SandboxSync.create(
        image,
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": _PYTHON_VERSION},
        resource={"cpu": "2", "memory": "4Gi"},
        timeout=timedelta(hours=2),
        connection_config=config,
        network_policy=NetworkPolicy(  # 沙箱网络路由限制策略
            default_action="deny",
            egress=[
                NetworkRule(action="allow", target="pypi.org"),
                NetworkRule(action="allow", target="*.github.com"),
                NetworkRule(action="allow", target="pypi.tuna.tsinghua.edu.cn"),
            ]
        )
    )

    sandbox_backend = OpenSandboxBackend(sandbox=sandbox)
    file_tools = build_file_transfer_tools(sandbox_backend)

    model = init_chat_model("deepseek-flash", api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    agent = create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        backend=sandbox_backend,
        system_prompt=(
            "你是一个助手，请根据用户输入的指令进行相应的操作，默认用中文回答。\n"
            "当用户要求把沙箱里的文件取回本地时，调用 download_file_to_local；\n"
            "当用户要求把本地文件送进沙箱时，调用 upload_file_from_local。"
        ),
        tools=file_tools
    )
    run_chat(agent)



# harness-虚拟文件系统