"""Server-owned model transport. Never forward provider credentials to a gateway.

Enabling the gateway requires its own key. There is no implicit direct fallback:
an unavailable gateway must not silently bypass authentication or rate limits.
"""
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


def gateway_enabled(environment=None):
    env = os.environ if environment is None else environment
    return env.get("LLM_GATEWAY_ENABLED", "false").lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    api_key: str = field(repr=False)
    via_gateway: bool = False


def model_endpoint(environment=None, *, purpose="web"):
    env = os.environ if environment is None else environment
    enabled = gateway_enabled(env)
    if enabled:
        base = env.get("LLM_GATEWAY_BASE_URL", "").rstrip("/")
        key_name = "LLM_GATEWAY_EVALUATION_KEY" if purpose == "evaluation" else "LLM_GATEWAY_API_KEY"
        key = env.get(key_name, "")
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path != "/v1"):
            raise ValueError("LLM_GATEWAY_BASE_URL must be an HTTP(S) URL ending in /v1")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model gateways require HTTPS")
        if not key or key == env.get("DEEPSEEK_API_KEY"):
            raise ValueError("A dedicated LLM_GATEWAY_API_KEY is required")
        return ModelEndpoint(base, key, True)
    return ModelEndpoint(env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                         env.get("DEEPSEEK_API_KEY", ""))


def transport_policy(endpoint):
    # A streamed response or tool plan must never be replayed by SDK retries.
    return {"timeout": 45, "max_retries": 0} if endpoint.via_gateway else {}
