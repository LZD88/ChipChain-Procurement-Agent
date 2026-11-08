"""Signed, short-lived context passed from Agent API wrappers to MCP."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_TTL_SECONDS = 60


class InvocationContextError(ValueError):
    pass


def invocation_secret() -> bytes:
    value = os.getenv("MCP_INVOCATION_SIGNING_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise InvocationContextError("MCP_INVOCATION_SIGNING_SECRET must be at least 32 bytes")
    return value.encode("utf-8")


def _claims_json(claims: dict[str, Any]) -> bytes:
    return json.dumps(
        claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def sign_invocation(
    *,
    user_id: str,
    thread_id: str,
    audience: str,
    correlation_id: str | None = None,
    trace_id: str | None = None,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    nonce: str | None = None,
    secret: bytes | None = None,
) -> dict[str, Any]:
    if not user_id or not thread_id or not audience:
        raise InvocationContextError("user, thread and audience are required")
    if ttl_seconds < 1 or ttl_seconds > 300:
        raise InvocationContextError("invocation TTL must be between 1 and 300 seconds")
    now = int(time.time()) if now is None else int(now)
    claims = {
        "v": 1,
        "sub": user_id,
        "thread": thread_id,
        "aud": audience,
        "correlation": correlation_id or "REQ-" + uuid.uuid4().hex[:20].upper(),
        "trace": trace_id or "AGT-" + uuid.uuid4().hex[:20].upper(),
        "iat": now,
        "exp": now + ttl_seconds,
        "nonce": nonce or secrets.token_urlsafe(24),
    }
    signature = hmac.new(secret or invocation_secret(), _claims_json(claims), hashlib.sha256).hexdigest()
    return {**claims, "sig": signature}


@dataclass
class NonceReplayGuard:
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def consume(self, nonce: str, expires_at: int) -> None:
        now = int(self.clock())
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry >= now}
            if nonce in self._seen:
                raise InvocationContextError("invocation envelope was already used")
            self._seen[nonce] = expires_at


def verify_invocation(
    envelope: dict[str, Any] | None,
    *,
    audience: str,
    now: int | None = None,
    secret: bytes | None = None,
    replay_guard: NonceReplayGuard | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise InvocationContextError("signed invocation envelope is required")
    required = {"v", "sub", "thread", "aud", "correlation", "trace", "iat", "exp", "nonce", "sig"}
    if set(envelope) != required:
        raise InvocationContextError("invocation envelope fields are invalid")
    signature = envelope["sig"]
    claims = {key: envelope[key] for key in required if key != "sig"}
    expected = hmac.new(secret or invocation_secret(), _claims_json(claims), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise InvocationContextError("invocation signature is invalid")
    current = int(time.time()) if now is None else int(now)
    if claims["v"] != 1 or not isinstance(claims["iat"], int) or not isinstance(claims["exp"], int):
        raise InvocationContextError("invocation envelope version or time is invalid")
    if claims["iat"] > current + 5 or claims["exp"] < current or claims["exp"] - claims["iat"] > 300:
        raise InvocationContextError("invocation envelope is expired or not yet valid")
    if claims["aud"] != audience:
        raise InvocationContextError("invocation audience does not match tool")
    if not all(isinstance(claims[key], str) and claims[key] for key in (
        "sub", "thread", "correlation", "trace", "nonce",
    )):
        raise InvocationContextError("invocation identity fields are invalid")
    if replay_guard is not None:
        replay_guard.consume(claims["nonce"], claims["exp"])
    return claims


def envelope_from_config(config: dict[str, Any], audience: str) -> dict[str, Any]:
    configurable = config.get("configurable", {})
    return sign_invocation(
        user_id=configurable.get("user_id", ""),
        thread_id=configurable.get("thread_id", ""),
        audience=audience,
        correlation_id=configurable.get("correlation_id"),
        trace_id=configurable.get("trace_id"),
    )
