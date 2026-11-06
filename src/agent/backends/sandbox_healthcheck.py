"""短时限探测沙箱；同时限制命令和底层 HTTP 等待。"""
import asyncio

SANDBOX_HEALTH_TIMEOUT_SECONDS = 8


async def check_sandbox(backend) -> bool:
    try:
        result = await asyncio.wait_for(
            backend.aexecute("echo ok", timeout=5),
            timeout=SANDBOX_HEALTH_TIMEOUT_SECONDS,
        )
        return result.exit_code == 0 and result.output.strip() == "ok"
    except Exception:
        return False
