"""启动前后端，校验本次实例就绪，并在退出时回收整个进程组。"""

import errno
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from dotenv import load_dotenv


# Windows 后台启动且输出重定向到文件时，stdout 仍可能继承 GBK 编码。
# 子进程日志中包含无法用 GBK 表示的字符时，日志转发线程不应因此退出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")
BACKEND_PORT = 8090
FRONTEND_PORT = 3000
LAUNCH_ENV = "DEEPAGENT_LAUNCH_ID"
LAUNCH_HEADER = "X-DeepAgent-Launch-Id"


class StartupError(RuntimeError):
    """服务无法启动或运行期间提前退出。"""


def _url_ready(url, timeout=3):
    """不使用系统代理检查本地依赖是否已响应。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _find_vbox_manage():
    configured = os.getenv("VBOX_MANAGE", "").strip()
    candidates = [
        configured,
        shutil.which("VBoxManage"),
        str(Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Oracle" / "VirtualBox" / "VBoxManage.exe"),
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def ensure_sandbox_runtime():
    """按需启动本地 VirtualBox OpenSandbox 虚拟机并等待健康检查。

    只有显式配置 SANDBOX_VM_NAME 时才管理虚拟机；远程 OpenSandbox、Docker
    或外部托管场景保持原有行为。虚拟机是独立基础设施，退出 Web 启动器时不关闭。
    """
    vm_name = os.getenv("SANDBOX_VM_NAME", "").strip()
    sandbox_domain = os.getenv("SANDBOX_DOMAIN", "http://localhost:8080").rstrip("/")
    health_url = f"{sandbox_domain}/health"
    if not vm_name or _url_ready(health_url):
        if vm_name:
            print(f"[Sandbox] OpenSandbox is ready at {sandbox_domain}")
        return

    if os.name != "nt":
        raise StartupError(
            f"OpenSandbox is unavailable at {sandbox_domain}. "
            "SANDBOX_VM_NAME automatic startup is currently supported on Windows."
        )

    vbox_manage = _find_vbox_manage()
    if not vbox_manage:
        raise StartupError(
            "OpenSandbox is unavailable and VBoxManage was not found. "
            "Install VirtualBox or set VBOX_MANAGE to VBoxManage.exe."
        )

    info = subprocess.run(
        [vbox_manage, "showvminfo", vm_name, "--machinereadable"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if info.returncode != 0:
        raise StartupError(f"VirtualBox VM {vm_name!r} was not found.")

    if 'VMState="running"' not in info.stdout:
        print(f"[Sandbox] Starting VirtualBox VM {vm_name!r} in headless mode...")
        started = subprocess.run(
            [vbox_manage, "startvm", vm_name, "--type", "headless"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if started.returncode != 0:
            detail = (started.stderr or started.stdout).strip()
            raise StartupError(f"Could not start VirtualBox VM {vm_name!r}: {detail}")
    else:
        print(f"[Sandbox] VirtualBox VM {vm_name!r} is already running")

    max_wait = int(os.getenv("SANDBOX_VM_START_TIMEOUT", "180"))
    started_at = time.monotonic()
    next_log = started_at
    while time.monotonic() - started_at < max_wait:
        if _url_ready(health_url, timeout=5):
            print(f"[Sandbox] OpenSandbox is ready at {sandbox_domain}")
            return
        now = time.monotonic()
        if now >= next_log:
            print(
                f"[Sandbox] Waiting for OpenSandbox... "
                f"({int(now - started_at)}s / {max_wait}s)"
            )
            next_log = now + 5
        time.sleep(1)
    raise StartupError(
        f"OpenSandbox at {sandbox_domain} was not healthy within {max_wait} seconds."
    )

def check_ports_available():
    """在启动任何子进程前检查 IPv4/IPv6，免误连已有服务。"""
    occupied = []
    for name, port in (("Backend", BACKEND_PORT), ("Frontend", FRONTEND_PORT)):
        for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::1")):
            try:
                # 通配地址 bind 成功不代表环回地址上没有服务（尤其启用
                # SO_REUSEADDR 时）。先检查启动器实际访问的本地地址。
                loopback = "127.0.0.1" if family == socket.AF_INET else "::1"
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(0.3)
                    if connection.connect_ex((loopback, port)) == 0:
                        occupied.append(f"{name}: {port}")
                        break
                with socket.socket(family, socket.SOCK_STREAM) as probe:
                    # 与开发服务器一致，允许重启时复用 TIME_WAIT 中的地址。
                    if os.name == "posix":
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if family == socket.AF_INET6:
                        probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    probe.bind((host, port))
                    probe.listen(1)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    occupied.append(f"{name}: {port}")
                    break
                if family == socket.AF_INET6 and exc.errno in (
                    errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.ENOPROTOOPT,
                ):
                    continue
                raise StartupError(f"Cannot check port {port}: {exc}") from exc
    if occupied:
        raise StartupError(
            f"Ports already in use ({', '.join(occupied)}). "
            "Stop the previous launcher/services before restarting. "
            "On macOS: lsof -nP -iTCP:8090 -iTCP:3000 -sTCP:LISTEN"
        )


def _start_process(name, command, cwd, env):
    options = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
    }
    proc = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, **options,
    )

    def output_logs():
        try:
            for line in proc.stdout:
                print(f"[{name}] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
        finally:
            proc.stdout.close()

    threading.Thread(target=output_logs, daemon=True).start()
    return proc


def _service_env(launch_id):
    env = os.environ.copy()
    env[LAUNCH_ENV] = launch_id
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_backend(launch_id):
    print(f"[Backend] Starting FastAPI at http://127.0.0.1:{BACKEND_PORT}")
    env = _service_env(launch_id)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return _start_process(
        "Backend",
        [sys.executable, "-m", "uvicorn", "api_view.web_main:app",
         "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        PROJECT_ROOT, env,
    )


def _check_running(name, proc):
    code = proc.poll()
    if code is not None:
        raise StartupError(f"{name} exited with code {code}. Check its logs above.")


def _wait_for_service(name, proc, url, launch_id, max_wait, *, backend=False, dependencies=()):
    """必须收到本次进程的 HTTP 响应；健康的旧服务不能使检查通过。"""
    print(f"[System] Waiting for {name.lower()} to be ready...")
    # localhost 的就绪检查不应绕到用户配置的 HTTP 代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    next_log = started + 5
    while time.monotonic() - started < max_wait:
        for other_name, other_proc in ((name, proc), *dependencies):
            _check_running(other_name, other_proc)
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    response_launch_id = response.headers.get(LAUNCH_HEADER)
                    if not response_launch_id:
                        raise StartupError(
                            f"{url} did not return the {LAUNCH_HEADER} header; "
                            "cannot verify this launch. Check that the service reads "
                            f"{LAUNCH_ENV} and returns it in the response header."
                        )
                    if response_launch_id != launch_id:
                        raise StartupError(
                            f"{url} belongs to another service, not this launch. "
                            "Stop the existing service before restarting."
                        )
                    ready = True
                    if backend:
                        data = json.load(response)
                        ready = isinstance(data, dict) and data.get("status") == "healthy"
                    if ready:
                        for other_name, other_proc in ((name, proc), *dependencies):
                            _check_running(other_name, other_proc)
                        print(f"[System] {name} is ready!")
                        return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
        now = time.monotonic()
        if now >= next_log:
            print(f"[System] Waiting for {name.lower()}... ({int(now - started)}s / {max_wait}s)")
            next_log = now + 5
    _check_running(name, proc)
    raise StartupError(f"{name} was not ready within {max_wait} seconds. Check its logs above.")


def check_backend_ready(proc, launch_id, max_wait=180):
    _wait_for_service(
        "Backend", proc, f"http://127.0.0.1:{BACKEND_PORT}/health",
        launch_id, max_wait, backend=True,
    )


def run_frontend(launch_id):
    npm = shutil.which("npm")
    if npm is None:
        raise StartupError("npm was not found. Install Node.js and add npm to PATH.")
    frontend_dir = PROJECT_ROOT / "frontend"
    env = _service_env(launch_id)
    if not (frontend_dir / "node_modules").is_dir():
        print("[Frontend] Installing dependencies...")
        install = _start_process("Frontend", [npm, "install"], frontend_dir, env)
        try:
            code = install.wait(timeout=180)
            if code != 0:
                raise StartupError(f"npm install failed with code {code}.")
        except subprocess.TimeoutExpired as exc:
            raise StartupError("npm install did not finish within 180 seconds.") from exc
        finally:
            stop_process(install)

    print(f"[Frontend] Starting Vite at http://127.0.0.1:{FRONTEND_PORT}")
    return _start_process(
        "Frontend",
        [npm, "run", "dev", "--", "--host", "127.0.0.1",
         "--port", str(FRONTEND_PORT), "--strictPort"],
        frontend_dir, env,
    )


def stop_process(proc, grace=10):
    """只清理本启动器创建的进程组，包括 npm/Vite 和 Uvicorn reload 子进程。"""
    if os.name == "posix":
        # 即使直接子进程已退出，进程组仍可能包含残留的孙进程。
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.wait()
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            proc.poll()  # 回收直接子进程，防止僵尸进程让进程组一直存在。
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                proc.wait()
                return
            time.sleep(0.1)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
    elif proc.poll() is None:
        # Windows 的 terminate() 同样不会自动结束 npm/Vite 的子进程。
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=5)


def main():
    print("[System] Starting DeepAgent services...")
    backend_proc = None
    frontend_proc = None
    previous_handlers = {}

    def signal_handler(signum, frame):
        raise KeyboardInterrupt

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(sig, signal_handler)
            previous_handlers[sig] = previous
        check_ports_available()
        ensure_sandbox_runtime()
        launch_id = uuid.uuid4().hex
        backend_proc = run_backend(launch_id)
        check_backend_ready(backend_proc, launch_id)
        frontend_proc = run_frontend(launch_id)
        _wait_for_service(
            "Frontend", frontend_proc, f"http://127.0.0.1:{FRONTEND_PORT}/",
            launch_id, 30, dependencies=(("Backend", backend_proc),),
        )

        print(
            f"[System] Ready — Frontend: http://127.0.0.1:{FRONTEND_PORT} | "
            f"API docs: http://127.0.0.1:{BACKEND_PORT}/docs | Ctrl+C to stop"
        )
        while True:
            _check_running("Backend", backend_proc)
            _check_running("Frontend", frontend_proc)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    finally:
        # 清理期间忽略重复的 Ctrl+C，避免再次打断而留下子进程。
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        processes = [proc for proc in (frontend_proc, backend_proc) if proc is not None]
        if processes:
            print("\n[System] Stopping services...")
            failures = []
            for proc in processes:
                try:
                    stop_process(proc)
                except Exception as exc:
                    failures.append(proc.pid)
                    print(f"[ERROR] Could not stop process group {proc.pid}: {exc}")
            if not failures:
                print("[System] All services stopped")
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)


if __name__ == "__main__":
    sys.exit(main())
