"""
OpenSandbox 沙箱的初始化与文件播种模块。

职责:
1. 获取或创建 OpenSandbox 沙箱，包装为 OpenSandboxBackend。
2. 播种技能文件（技能包 SKILL.md 和根目录共享参考资料）。

注意：AGENTS.md 不在此处播种，由 create_main_agent 按沙箱代际和内容播种。
用户长期记忆（/memories/）由 CompositeBackend 路由到 StoreBackend 持久化。
运行时的增量技能同步由 SkillsSyncMiddleware 负责。
"""
import asyncio
from datetime import timedelta
import logging
from pathlib import Path
from shlex import quote
from textwrap import dedent
from typing import List, Tuple

from opensandbox import Sandbox

from agent.backends.custom_opensandbox import OpenSandboxBackend
from agent.backends.sandbox_healthcheck import check_sandbox
from agent.config import (
    LOCAL_SKILLS_DIR, SANDBOX_SKILLS_ROOT,
)


logger = logging.getLogger(__name__)

_PYTHON_VERSION = "3.11"


async def setup_sandbox(config, sandbox_id=None, image=None) -> OpenSandboxBackend:
    """
    获取或创建沙箱，播种基础文件。

    Args:
        config: ConnectionConfig（异步）配置。
        sandbox_id: 可选，要连接的现有沙箱 ID。
        image: 可选，创建新沙箱时使用的镜像。

    Returns:
        OpenSandboxBackend 实例。
    """
    if sandbox_id:
        logger.debug("连接已有沙箱：%s", sandbox_id)
        # The manager owns fallback order. Creating here would bypass its ready
        # pool and spend a cold-start timeout before trying an available reserve.
        sandbox = await asyncio.wait_for(
            Sandbox.connect(sandbox_id, connection_config=config), timeout=15,
        )

    if not sandbox_id:
        if not image:
            image = "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2"

        logger.debug("创建沙箱：image=%s", image)
        sandbox = await Sandbox.create(
            image,
            entrypoint=["/opt/opensandbox/code-interpreter.sh"],
            env={"PYTHON_VERSION": _PYTHON_VERSION},
            resource={"cpu": "2", "memory": "4Gi"},
            timeout=timedelta(hours=2),
            connection_config=config,
        )

    backend = OpenSandboxBackend(sandbox=sandbox)

    try:
        if not await check_sandbox(backend):
            raise RuntimeError("采购分析环境暂时无响应，请稍后重试。")
        # 准备目录、技能文件、Python venv 和依赖。
        await _ensure_dirs(backend)
        await _seed_files(backend)
        await _create_venv(backend)
    except (Exception, asyncio.CancelledError):
        # 只清理本次新建失败的沙箱，重连沙箱可能有用户文件，保留供修复。
        if not sandbox_id:
            try:
                await sandbox.kill()
            except Exception:
                logger.warning("清理初始化失败的沙箱 %s 失败", sandbox.id, exc_info=True)
        raise

    logger.debug("沙箱环境就绪：%s", sandbox.id)
    return backend


# skills 运行时依赖的目录（需在沙箱中预创建）
_SKILL_DIRS = ["/analysis/temp"]

# 所有 Python 依赖统一安装到此 venv，避开系统 Python 的 externally managed 限制
_VENV_PATH = "/opt/skills-venv"

# skills 运行时需要的 Python 第三方包
_PREINSTALL_PACKAGES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "requests": "requests",
    "beautifulsoup4": "bs4",
    "playwright": "playwright"
}


async def _ensure_dirs(backend: OpenSandboxBackend) -> None:
    """预创建 skills 运行所需的目录，避免 FileNotFoundError。"""
    for d in _SKILL_DIRS:
        await backend.aexecute(f"mkdir -p {d}")


# 清华公共 PyPI 镜像；实际连通性取决于沙箱的网络环境。
_PYPI_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple/"
# pip install 通用参数
_PIP_INSTALL_ARGS = f"-i {_PYPI_INDEX} --default-timeout=60 --no-input -q"


def _venv_bootstrap_command(versions_dir: str = "/opt/python/versions") -> str:
    """查找镜像自带的完整 Python；兼容 ARM/x86 和补丁版本变化。"""
    python = quote(f"{_VENV_PATH}/bin/python")
    pip = quote(f"{_VENV_PATH}/bin/pip")
    check = quote(
        "import sys, pip; "
        f"assert sys.prefix == {_VENV_PATH!r}; "
        "assert sys.prefix != sys.base_prefix"
    )
    probe = quote(
        "import sys, venv, ensurepip; "
        "print(getattr(sys, '_base_executable', sys.executable))"
    )
    # PATH 中的 python3 可能来自系统或上一次失败后留下的残缺 venv。
    return dedent(f"""\
        if {python} -c {check} >/dev/null 2>&1 && {pip} --version >/dev/null 2>&1; then
            printf '[INFO] 复用已有 Python venv\\n'
            exit 0
        fi
        skills_python=''
        for skills_candidate in {quote(versions_dir)}/cpython-{_PYTHON_VERSION}.*-linux-*/bin/python3 python3; do
            if skills_python=$("$skills_candidate" -c {probe} 2>/dev/null); then
                break
            fi
            skills_python=''
        done
        if [ -z "$skills_python" ]; then
            printf '未找到带 venv/ensurepip 的 Python。请检查镜像 /opt/python/versions，或为容器内 Python 安装匹配版本的 venv 组件。\\n' >&2
            exit 1
        fi
        printf '[INFO] 使用沙箱 Python: %s\\n' "$skills_python"
        "$skills_python" -m venv --clear {quote(_VENV_PATH)} || exit $?
        {python} -c {check} || exit $?
        {pip} --version
        """)


async def _execute_required(backend: OpenSandboxBackend, command: str, step: str,
                            timeout: int = 500) -> str:
    result = await backend.aexecute(command, timeout=timeout)
    if result.exit_code != 0:
        # 保留完整错误，失败的沙箱不能被放入预热池并分配给用户。
        raise RuntimeError(f"{step}失败（退出码 {result.exit_code}）:\n{result.output}")
    return result.output


async def _create_venv(backend: OpenSandboxBackend) -> None:
    """创建沙箱级 Python venv，并预装 skills 所需的第三方包。

    系统 Python 设置了 externally managed 限制（PEP 668），--system 安装会被拒绝。
    因此创建一个统一的 venv，并将 /opt/skills-venv/bin 注入 SANDBOX_PATH 最前面，
    所有 skill 的 python/pip 命令自动路由到 venv，无需改任何脚本。
    """
    output = await _execute_required(backend, _venv_bootstrap_command(), "创建 Python venv")
    if output.strip():
        logger.debug("Python venv 初始化输出：\n%s", output.strip())
    python = quote(f"{_VENV_PATH}/bin/python")

    # 用实际 import 校验，避免旧的 /tmp 标记把残缺环境误判为已安装。
    missing = []
    for package, module in _PREINSTALL_PACKAGES.items():
        check = await backend.aexecute(f"{python} -c {quote(f'import {module}')}", timeout=30)
        if check.exit_code != 0:
            missing.append(package)
    if missing:
        logger.info("沙箱安装 Python 依赖：%s", ", ".join(missing))
        await _execute_required(
            backend,
            f"{python} -m pip install {_PIP_INSTALL_ARGS} {' '.join(map(quote, missing))}",
            "安装沙箱 Python 依赖",
        )

    modules = ', '.join(_PREINSTALL_PACKAGES.values())
    await _execute_required(backend, f"{python} -c {quote(f'import {modules}')}",
                            "校验沙箱 Python 依赖", timeout=30)
    logger.debug("Python venv 和技能依赖就绪：%s", _VENV_PATH)


async def _seed_files(backend: OpenSandboxBackend) -> None:
    """
    将本地技能文件（/skills/ 下的 SKILL.md 等）播种到沙箱。

    AGENTS.md 不在此处播种（由 create_main_agent 按代际和内容播种）。
    只上传内容与沙箱不一致的文件，已一致的跳过，避免重复写入。
    """
    file_mapping: List[Tuple[Path, str]] = []

    # 从根目录递归扫描，同时包含共享参考文件和各技能包内的文件。
    skills_base = Path(LOCAL_SKILLS_DIR)
    if skills_base.exists():
        for local_file in sorted(skills_base.rglob("*")):
            if local_file.is_file():
                rel = local_file.relative_to(skills_base).as_posix()
                sandbox_path = f"{SANDBOX_SKILLS_ROOT}/{rel}"
                file_mapping.append((local_file, sandbox_path))

    # 收集需要上传的文件
    to_upload: List[Tuple[str, bytes]] = []
    for local_path, sandbox_path in file_mapping:
        local_content = local_path.read_bytes()
        # 用 test -f 检测文件是否存在（无 ERROR 日志），避免 download_files 对 404 打 ERROR
        check = await backend.aexecute(f"test -f {quote(sandbox_path)}")
        if check.exit_code == 0:
            try:
                results = await backend.adownload_files([sandbox_path])
                if results and results[0].content is not None and not results[0].error:
                    remote_content = results[0].content
                    if isinstance(remote_content, str):
                        remote_content = remote_content.encode("utf-8")
                    if remote_content == local_content:
                        continue
            except Exception:
                pass
        to_upload.append((sandbox_path, local_content))

    if to_upload:
        results = await backend.aupload_files(to_upload)
        uploaded_paths = {result.path for result in results if result.error is None}
        failed_paths = [path for path, _ in to_upload if path not in uploaded_paths]
        if failed_paths:
            raise RuntimeError(f"沙箱基础文件上传失败: {', '.join(failed_paths)}")
        logger.debug("沙箱基础文件同步完成：%d 个文件", len(to_upload))
    else:
        logger.debug("沙箱基础文件无需更新")
