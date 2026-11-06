import logging
import asyncio
from contextlib import asynccontextmanager
import httpx
from fastmcp import FastMCP

from agent.env_utils import JAVA_API_BASE_URL, JAVA_API_USERNAME, JAVA_API_PASSWORD

logger = logging.getLogger(__name__)


class AuthenticatedERPClient(httpx.AsyncClient):
    """使用最小权限服务账号访问 ERP，并在 Token 失效后只重试一次。"""

    def __init__(self, *args, username: str, password: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._username = username
        self._password = password
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()

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
        if str(url) != "/auth/login":
            await self._login()
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._token}"
            kwargs["headers"] = headers
        response = await super().request(method, url, **kwargs)
        if response.status_code == 401 and str(url) != "/auth/login":
            await self._login(force=True)
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._token}"
            kwargs["headers"] = headers
            response = await super().request(method, url, **kwargs)
        return response

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
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        username=JAVA_API_USERNAME,
        password=JAVA_API_PASSWORD,
    )
    await http_client._login()
    # 将 http_client 存入 lifespan_context，供工具函数通过 ctx 获取
    try:
        # 将 http_client 存入 lifespan_context，供工具函数通过 ctx 获取
        yield {"http_client": http_client}
    finally:
        # 关闭时清理资源
        try:
            await http_client.aclose()
        except Exception:
            # 记录日志但不向上抛，避免遮蔽 yield 期间的真实业务异常
            logger.exception("关闭 HTTP 客户端时发生异常")
