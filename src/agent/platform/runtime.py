"""Validated configuration snapshots, pinned for the duration of an invocation.

Nacos controls model parameters, never credentials, provider URLs, tools or grants.
Prompt publication remains owned by the existing MySQL release lifecycle.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
import tempfile

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


def is_enabled(name):
    return os.getenv(name, "false").lower() in {"true", "1", "yes"}


class ModelParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    model: str = Field(min_length=1, max_length=100)
    temperature: float = Field(ge=0, le=2)
    maxTokens: int = Field(ge=64, le=30000)

    @field_validator("model")
    @classmethod
    def allow_model(cls, value):
        allowed = {m.strip() for m in os.getenv("NACOS_ALLOWED_MODELS", "deepseek-flash,deepseek-chat").split(",")}
        if value not in allowed:
            raise ValueError("model is not in the server-owned allowlist")
        return value


class ModelProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    main: ModelParameters
    summary: ModelParameters


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schemaVersion: int = Field(default=1, ge=1, le=1)
    revision: int = Field(ge=1)
    models: ModelProfiles

    @property
    def fingerprint(self):
        value = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()


def defaults():
    return RuntimeSnapshot(revision=1, models=ModelProfiles(
        main=ModelParameters(model="deepseek-flash", temperature=.1, maxTokens=30000),
        summary=ModelParameters(model="deepseek-flash", temperature=.3, maxTokens=30000)))


PINNED_CONFIG: ContextVar[RuntimeSnapshot | None] = ContextVar("chipchain_model_config", default=None)


class NacosClient:
    def __init__(self, *, client=None):
        self.base = os.getenv("NACOS_SERVER_URL", "http://localhost:18848/nacos").rstrip("/")
        self.namespace = os.getenv("NACOS_NAMESPACE", "")
        self.group = os.getenv("NACOS_GROUP", "CHIPCHAIN")
        self.token = None
        self.token_expiry = 0
        self.http = client or httpx.AsyncClient(timeout=5, trust_env=False)

    async def close(self):
        await self.http.aclose()

    async def _access_token(self):
        if self.token and time.monotonic() < self.token_expiry:
            return self.token
        user, password = os.getenv("NACOS_USERNAME"), os.getenv("NACOS_PASSWORD")
        if not user or not password:
            raise ValueError("Nacos credentials are not configured")
        response = await self.http.post(self.base + "/v1/auth/login", data={"username": user, "password": password})
        response.raise_for_status()
        payload = response.json()
        self.token = payload["accessToken"]
        self.token_expiry = time.monotonic() + max(1, payload.get("tokenTtl", 300) - 30)
        return self.token

    async def config(self, data_id, content=None):
        for attempt in range(2):
            parameters = {"dataId": data_id, "group": self.group, "tenant": self.namespace,
                          "accessToken": await self._access_token()}
            if content is None:
                response = await self.http.get(self.base + "/v1/cs/configs", params=parameters)
            else:
                response = await self.http.post(self.base + "/v1/cs/configs", data={**parameters, "type": "json", "content": content})
            if response.status_code in {401, 403} and attempt == 0:
                self.token = None
                continue
            response.raise_for_status()
            if content is not None and response.text.strip().lower() != "true":
                raise ValueError("Nacos rejected configuration publication")
            return response.text


class RuntimeConfiguration:
    def __init__(self, *, client=None, cache_path=None):
        self.current = defaults()
        self.client = client
        self.cache_path = Path(cache_path) if cache_path else Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/state"))) / "ChipSupply/runtime-models.json"
        self.status = "disabled"
        self.last_success = None
        self.error_type = None

    def load_cache(self):
        if not is_enabled("NACOS_ENABLED"):
            return
        try:
            self.current = RuntimeSnapshot.model_validate_json(self.cache_path.read_text(encoding="utf-8"))
            self.status = "cached"
        except (OSError, ValueError):
            pass

    def accept(self, content):
        candidate = RuntimeSnapshot.model_validate_json(content)
        previous = self.current
        if candidate.revision < previous.revision or (candidate.revision == previous.revision and candidate != previous):
            raise ValueError("configuration revision must increase when content changes")
        # Store only validated public model parameters, never credentials.
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_path.parent,
                                         prefix=self.cache_path.name, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(candidate.model_dump_json(indent=2))
        try:
            temporary.replace(self.cache_path)
        finally:
            temporary.unlink(missing_ok=True)
        self.current = candidate
        self.status, self.error_type, self.last_success = "connected", None, time.time()
        return candidate

    async def refresh(self):
        if not is_enabled("NACOS_ENABLED"):
            self.status = "disabled"
            return
        try:
            if self.client is None:
                self.client = NacosClient()
            content = await self.client.config(os.getenv("NACOS_MODEL_DATA_ID", "chipchain-models.json"))
            self.accept(content)
        except Exception as exc:
            self.status, self.error_type = "degraded", type(exc).__name__

    def snapshot(self):
        return PINNED_CONFIG.get() or self.current

    def public(self):
        return {"status": self.status, "lastSuccess": self.last_success, "errorType": self.error_type,
                "revision": self.current.revision, "fingerprint": self.current.fingerprint,
                "models": self.current.models.model_dump()}

    async def close(self):
        if self.client:
            await self.client.close()


runtime_configuration = RuntimeConfiguration()


def pin_configuration(snapshot=None):
    return PINNED_CONFIG.set(snapshot or runtime_configuration.current)


def reset_configuration(token):
    PINNED_CONFIG.reset(token)
