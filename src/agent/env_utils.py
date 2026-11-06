"""加载项目 .env，仅导出实际使用的凭据与 MCP 服务配置。"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


GLM_API_KEY = get_env("GLM_API_KEY")
GLM_BASE_URL = get_env("GLM_BASE_URL")
DEEPSEEK_API_KEY = get_env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = get_env("DEEPSEEK_BASE_URL")
BOCHA_API_KEY = get_env("BOCHA_API_KEY")  # 可选，仅供博查联网搜索使用
SANDBOX_API_KEY = get_env("SANDBOX_API_KEY")
SANDBOX_DOMAIN = get_env("SANDBOX_DOMAIN", "http://localhost:8080")
SANDBOX_USE_SERVER_PROXY = (
    get_env("SANDBOX_USE_SERVER_PROXY", "false") or "false"
).lower() in {"true", "1", "yes"}
# 是否在预热沙箱被认领后、有新用户到来时，惰性补充预热沙箱（多用户场景开启）
SANDBOX_PREWARM_REPLENISH = (get_env("SANDBOX_PREWARM_REPLENISH", "true") or "true").lower() in {"true", "1", "yes"}

JAVA_API_BASE_URL = get_env("JAVA_API_BASE_URL", "http://localhost:8082/api")
JAVA_API_USERNAME = get_env("JAVA_API_USERNAME", "procurement_agent")
JAVA_API_PASSWORD = get_env("JAVA_API_PASSWORD")
MCP_HOST = get_env("MCP_HOST", "127.0.0.1")
MCP_PORT = int(get_env("MCP_PORT", "8000"))
MCP_PATH = get_env("MCP_PATH", "/mcp")


def _client_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "localhost"
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


ERP_MCP_URL = get_env("ERP_MCP_URL", f"http://{_client_host(MCP_HOST)}:{MCP_PORT}{MCP_PATH}")
ANALYSIS_MCP_URL = get_env("ANALYSIS_MCP_URL")
