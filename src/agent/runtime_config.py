"""Per-process preparation limits. Reading configuration never exposes credentials."""
from dataclasses import dataclass
import os

from agent import env_utils as _env  # Load the project's .env before reading options.


def _integer(name, default, minimum, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} outside supported range")
    return value


def _boolean(name, default=True):
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be a boolean")
    return value in {"true", "1", "yes"}


@dataclass(frozen=True)
class RuntimeConfig:
    health_ttl: int = 45
    pool_target: int = 2
    pool_concurrency: int = 1
    background_enabled: bool = True
    prepare_concurrency: int = 2
    max_pending: int = 32
    graph_enabled: bool = True
    graph_max_users: int = 32
    graph_idle_seconds: int = 1800
    prepare_timeout: float = 300
    request_routing_enabled: bool = True
    request_router_llm_enabled: bool = True
    request_router_timeout_seconds: int = 5

    @classmethod
    def from_env(cls):
        ttl = _integer("SANDBOX_HEALTH_CACHE_TTL_SECONDS", 45, 0, 60)
        if ttl != 0 and ttl < 30:
            raise ValueError("SANDBOX_HEALTH_CACHE_TTL_SECONDS must be 0 or 30–60")
        target = _integer("SANDBOX_PREWARM_TARGET", 2, 0, 4)
        concurrency = _integer("SANDBOX_PREWARM_CONCURRENCY", 1, 1, 4)
        if target and concurrency > target:
            raise ValueError("SANDBOX_PREWARM_CONCURRENCY exceeds target")
        slots = _integer("AGENT_PREPARE_CONCURRENCY", 2, 1)
        pending = _integer("AGENT_PREPARE_MAX_PENDING", 32, 1)
        if pending < slots:
            raise ValueError("AGENT_PREPARE_MAX_PENDING must cover concurrency")
        return cls(
            health_ttl=ttl, pool_target=target, pool_concurrency=concurrency,
            background_enabled=_boolean("AGENT_BACKGROUND_PREPARE_ENABLED"),
            prepare_concurrency=slots, max_pending=pending,
            graph_enabled=_boolean("AGENT_GRAPH_CACHE_ENABLED"),
            graph_max_users=_integer("AGENT_GRAPH_CACHE_MAX_USERS", 32, 1),
            graph_idle_seconds=_integer("AGENT_GRAPH_CACHE_IDLE_SECONDS", 1800, 1),
            request_routing_enabled=_boolean("AGENT_REQUEST_ROUTING_ENABLED"),
            request_router_llm_enabled=_boolean("AGENT_REQUEST_ROUTER_LLM_ENABLED"),
            request_router_timeout_seconds=_integer("AGENT_REQUEST_ROUTER_TIMEOUT_SECONDS", 5, 1, 10),
        )
