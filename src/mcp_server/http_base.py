import logging
import asyncio
import random
import time
import uuid
from contextlib import asynccontextmanager
import httpx
from fastmcp import FastMCP

from agent.env_utils import JAVA_API_BASE_URL, JAVA_API_USERNAME, JAVA_API_PASSWORD
from agent.invocation_context import NonceReplayGuard
from mcp_server.decision_db import create_decision_storage

logger = logging.getLogger(__name__)


class AuthenticatedERPClient(httpx.AsyncClient):
    """最小权限 ERP 客户端：认证、追踪传播、超时、指数退避和熔断。"""

    def __init__(self, *args, username: str, password: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._username = username
        self._password = password
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()
        self._failure_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def _login(self, force: bool = False) -> None:
        if self._token and not force:
            return
        async with self._auth_lock:
            if self._token and not force:
                return
            response = await super().request(
                "POST", "/auth/login",
                json={"username": self._username, "password": self._password},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 200 or not payload.get("data", {}).get("token"):
                raise RuntimeError(f"ERP 服务账号认证失败: {payload.get('message', '未知错误')}")
            self._token = payload["data"]["token"]

    async def request(self, method, url, **kwargs):
        method = str(method).upper()
        is_login = str(url) == "/auth/login"
        if not is_login:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError("ERP 服务熔断保护中，请稍后重试")
            await self._login()
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._token}"
            trace_id = headers.setdefault("X-Trace-Id", "AGT-" + uuid.uuid4().hex[:20].upper())
            headers.setdefault("traceparent", f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01")
            kwargs["headers"] = headers
        headers = kwargs.get("headers", {}) or {}
        retryable = method in {"GET", "HEAD", "OPTIONS", "DELETE"} or (
            method in {"POST", "PUT", "PATCH"} and bool(headers.get("Idempotency-Key")))
        attempts = 3 if retryable and not is_login else 1
        last_error = None
        auth_refreshed = False
        for attempt in range(attempts):
            try:
                response = await super().request(method, url, **kwargs)
                if response.status_code == 401 and not is_login and not auth_refreshed:
                    await self._login(force=True)
                    kwargs["headers"]["Authorization"] = f"Bearer {self._token}"
                    auth_refreshed = True
                    response = await super().request(method, url, **kwargs)
                if response.status_code >= 500 and retryable and attempt + 1 < attempts:
                    await self._failure()
                    await asyncio.sleep((0.2 * (2 ** attempt)) + random.uniform(0, 0.1))
                    continue
                if response.status_code < 500:
                    await self._success()
                else:
                    await self._failure()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                await self._failure()
                if attempt + 1 >= attempts:
                    raise RuntimeError(f"ERP 请求失败（已重试 {attempts - 1} 次）: {exc}") from exc
                await asyncio.sleep((0.2 * (2 ** attempt)) + random.uniform(0, 0.1))
        raise RuntimeError(f"ERP 请求失败: {last_error}")

    async def _success(self):
        async with self._failure_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    async def _failure(self):
        async with self._failure_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 5:
                self._circuit_open_until = time.monotonic() + 30
                logger.warning("ERP 连续失败达到阈值，熔断 30 秒")

# 关键点：lifespan 返回的字典会通过 ctx.request_context.lifespan_context 传递给每个工具函数，
# 避免了全局变量或手动关闭的麻烦。

@asynccontextmanager
async def mcp_lifespan(server: FastMCP):
    """
    FastMCP 生命周期管理：初始化 / 关闭 HTTP 客户端

    Args:
        server: FastMCP 实例（由框架自动传入）
    """
    # 启动时创建 HTTP 客户端（连接池）
    if not JAVA_API_USERNAME or not JAVA_API_PASSWORD:
        raise RuntimeError("缺少 JAVA_API_USERNAME/JAVA_API_PASSWORD，无法以服务账号连接 ERP")
    http_client = AuthenticatedERPClient(
        base_url=JAVA_API_BASE_URL,
        timeout=httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        username=JAVA_API_USERNAME,
        password=JAVA_API_PASSWORD,
    )
    await http_client._login()
    decision_storage = await create_decision_storage()
    invocation_replay_guard = NonceReplayGuard()
    # 将 http_client 存入 lifespan_context，供工具函数通过 ctx 获取
    try:
        # 将 http_client 存入 lifespan_context，供工具函数通过 ctx 获取
        yield {
            "http_client": http_client,
            "decision_storage": decision_storage,
            "invocation_replay_guard": invocation_replay_guard,
        }
    finally:
        # 关闭时清理资源
        try:
            await http_client.aclose()
        except Exception:
            # 记录日志但不向上抛，避免遮蔽 yield 期间的真实业务异常
            logger.exception("关闭 HTTP 客户端时发生异常")
        await decision_storage.close()
