"""Coordinate mutable initialization state shared by graphs for one sandbox."""
import asyncio
from agent.backends.sandbox_proxy import SandboxBackendProxy


def sync_lock(backend):
    if isinstance(backend, SandboxBackendProxy):
        if not hasattr(backend, "_initialization_lock"):
            backend._initialization_lock = asyncio.Lock()
        return backend._initialization_lock
    return asyncio.Lock()


def generation(backend):
    value = getattr(backend, "generation", 0)
    return value if isinstance(value, int) else 0
