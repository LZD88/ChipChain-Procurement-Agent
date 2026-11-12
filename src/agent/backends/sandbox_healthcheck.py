"""Bounded single-flight health probes; only successful results can be reused."""
import asyncio
from collections import OrderedDict
import time
import uuid
import httpx
from agent.runtime_config import RuntimeConfig
from api_view.observability import RUNTIME_CACHE

SANDBOX_HEALTH_TIMEOUT_SECONDS = 8
_success = OrderedDict()
_probes = {}
_MAX_RESULTS = 256


def _key(backend):
    # A concrete backend instance is a generation. Proxy and setup share that key.
    raw = getattr(backend, "_backend", backend)
    token = vars(raw).get("_health_generation")
    if token is None:
        token = uuid.uuid4().hex
        raw._health_generation = token
    return str(getattr(raw, "id", "anonymous")), token


def invalidate_health(backend_or_id):
    sandbox_id = backend_or_id if isinstance(backend_or_id, str) else _key(backend_or_id)[0]
    removed = False
    for key in list(_success):
        if key[0] == sandbox_id:
            del _success[key]
            removed = True
    for key, task in list(_probes.items()):
        if key[0] == sandbox_id:
            task.health_invalidated = True
            removed = True
    if removed:
        RUNTIME_CACHE.labels("health", "invalidated").inc()


def is_infrastructure_error(exc):
    if isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError)):
        return True
    from opensandbox.exceptions import SandboxException
    if isinstance(exc, SandboxException):
        status = getattr(exc, "status_code", None)
        return status is None or status in {404, 408} or status >= 500
    return False


async def _probe(backend, key):
    try:
        result = await asyncio.wait_for(backend.aexecute("echo ok", timeout=5),
                                        timeout=SANDBOX_HEALTH_TIMEOUT_SECONDS)
        ok = result.exit_code == 0 and result.output.strip() == "ok"
        if ok and not getattr(asyncio.current_task(), "health_invalidated", False):
            _success[key] = time.monotonic()
            _success.move_to_end(key)
            while len(_success) > _MAX_RESULTS:
                _success.popitem(last=False)
        else:
            _success.pop(key, None)
        return ok and not getattr(asyncio.current_task(), "health_invalidated", False)
    except Exception:
        _success.pop(key, None)
        return False


async def check_sandbox(backend) -> bool:
    key = _key(backend)
    ttl = RuntimeConfig.from_env().health_ttl
    if ttl and time.monotonic() - _success.get(key, float("-inf")) < ttl:
        RUNTIME_CACHE.labels("health", "hit").inc()
        return True
    RUNTIME_CACHE.labels("health", "miss").inc()
    task = _probes.get(key)
    if task is None:
        task = asyncio.create_task(_probe(backend, key))
        _probes[key] = task
        def finished(completed):
            if _probes.get(key) is completed:
                _probes.pop(key, None)
        task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _probes.get(key) is task:
            _probes.pop(key, None)
