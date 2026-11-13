"""Server-owned MCP transport configuration; no silent direct fallback."""
import os
from urllib.parse import urlsplit


def mcp_server_config(erp_url, analysis_url, environment=None):
    env = os.environ if environment is None else environment
    enabled = env.get("MCP_GATEWAY_ENABLED", "false").lower() in {"true", "1", "yes"}
    urls = {"chip-supply-api": erp_url, "analysis": analysis_url}
    if not enabled:
        return {name: {"url": url, "transport": "streamable_http"} for name, url in urls.items()}
    base = env.get("MCP_GATEWAY_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise ValueError("MCP_GATEWAY_BASE_URL must be an HTTP(S) origin without a path")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote MCP gateways require HTTPS")
    key = env.get("MCP_GATEWAY_API_KEY", "")
    forbidden = {env.get(k) for k in ("DEEPSEEK_API_KEY", "LLM_GATEWAY_API_KEY", "LLM_GATEWAY_EVALUATION_KEY")}
    if not key or key in forbidden:
        raise ValueError("A dedicated MCP_GATEWAY_API_KEY is required")
    routes = {"chip-supply-api": "chipchain-erp", "analysis": "chipchain-charts"}
    return {name: {"url": f"{base}/mcp-servers/{routes[name]}" if url else url,
                   "transport": "streamable_http",
                   **({"headers": {"Authorization": "Bearer " + key}} if url else {})}
            for name, url in urls.items()}
