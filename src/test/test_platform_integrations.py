import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from agent.platform.runtime import (RuntimeConfiguration, RuntimeSnapshot, defaults, pin_configuration,
                                    reset_configuration, runtime_configuration, NacosClient)
from agent.platform.models import RuntimeChatOpenAI
from agent.platform.telemetry import LangfuseIntegration, TraceHandle, redact


def revised(revision=2, **changes):
    value = defaults().model_dump()
    value["revision"] = revision
    value["models"]["main"].update(changes)
    return value


def test_runtime_rejects_credentials_urls_unknown_models_and_bad_limits(tmp_path):
    runtime = RuntimeConfiguration(cache_path=tmp_path / "config.json")
    for changes in ({"api_key": "secret"}, {"base_url": "http://external"}, {"model": "unreviewed"},
                    {"maxTokens": 30001}, {"temperature": 3.0}, {"maxTokens": "1000"}):
        with pytest.raises(ValidationError):
            runtime.accept(json.dumps(revised(**changes)))
        assert runtime.current == defaults()
    accepted = runtime.accept(json.dumps(revised(temperature=.2)))
    with pytest.raises(ValueError):
        runtime.accept(json.dumps(revised(temperature=.4)))
    with pytest.raises(ValueError):
        runtime.accept(defaults().model_dump_json())
    assert runtime.current == accepted


@pytest.mark.asyncio
async def test_offline_and_invalid_nacos_preserve_last_good_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("NACOS_ENABLED", "true")
    client = SimpleNamespace(config=AsyncMock(side_effect=TimeoutError()), close=AsyncMock())
    runtime = RuntimeConfiguration(client=client, cache_path=tmp_path / "config.json")
    accepted = runtime.accept(json.dumps(revised(temperature=.4)))
    await runtime.refresh()
    assert runtime.status == "degraded" and runtime.current == accepted
    reloaded = RuntimeConfiguration(cache_path=runtime.cache_path)
    reloaded.load_cache()
    assert reloaded.current == accepted and reloaded.status == "cached"
    client.config.side_effect = None
    client.config.return_value = '{"revision":3,"dangerous":true}'
    await runtime.refresh()
    assert runtime.current == accepted
    assert "secret" not in json.dumps(runtime.public())


@pytest.mark.asyncio
async def test_pinned_requests_keep_configuration_across_concurrent_refresh(monkeypatch):
    old = defaults()
    new = RuntimeSnapshot.model_validate(revised(temperature=.6))
    monkeypatch.setattr(runtime_configuration, "current", old)
    model = RuntimeChatOpenAI(model="deepseek-flash", api_key="test", runtime_profile="main")
    bound = model.bind_tools([{"name": "read", "description": "read", "parameters": {"type": "object", "properties": {}}}])
    token = pin_configuration()
    try:
        runtime_configuration.current = new
        payload = model._get_request_payload("question")
        assert payload["temperature"] == old.models.main.temperature
        assert payload["model"] == "deepseek-flash"
        assert bound.kwargs["tools"][0]["function"]["name"] == "read"
        assert model._get_invocation_params()["temperature"] == old.models.main.temperature
    finally:
        reset_configuration(token)
    assert model._get_request_payload("question")["temperature"] == .6


@pytest.mark.asyncio
async def test_nacos_authentication_refreshes_once_without_leaking_token(monkeypatch):
    monkeypatch.setenv("NACOS_USERNAME", "operator")
    monkeypatch.setenv("NACOS_PASSWORD", "test-password")
    logins = 0
    gets = 0
    def respond(request):
        nonlocal logins, gets
        if request.url.path.endswith("/auth/login"):
            logins += 1
            return httpx.Response(200, json={"accessToken": f"test-{logins}", "tokenTtl": 300})
        gets += 1
        return httpx.Response(403 if gets == 1 else 200, text="configuration")
    client = NacosClient(client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert await client.config("models.json") == "configuration"
    assert logins == gets == 2
    await client.close()


def test_telemetry_redacts_nested_secrets_and_disabled_mode_is_local(monkeypatch):
    value = redact({"api_key": "private", "nested": {"authorization": "private"},
                    "text": "Bearer abc123 sk-abcdefghijklmnop12345 password=private api_key:private", "outputTokens": 12})
    assert "private" not in json.dumps(value)
    assert "abc123" not in json.dumps(value)
    assert value["outputTokens"] == 12
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    integration = LangfuseIntegration(); integration.initialize()
    handle = integration.begin(name="chat", request_id="request", thread_id="thread", user_id="user")
    assert handle.handler is None and handle.trace_id is None
    handle.finish("success")


def test_broken_observability_cannot_change_a_business_result():
    integration = LangfuseIntegration()
    class BrokenRoot:
        trace_id = "0" * 32
        def update(self, **kwargs):
            raise ConnectionError()
    handle = TraceHandle(integration, BrokenRoot())
    handle.finish("success", output="business result")
    assert integration.status == "degraded"
    handle.finish("success")


def test_integration_status_requires_permission_and_exposes_no_credentials(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api_view.api import integrations as api
    from api_view.auth import require_user
    grants = set()
    async def has_capability(actor, capability):
        return capability in grants
    monkeypatch.setattr(api, "mysql_storage", SimpleNamespace(has_capability=has_capability))
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    web = TestClient(app)
    assert web.get("/api/integrations/status").status_code == 401
    app.dependency_overrides[require_user] = lambda: "viewer"
    assert web.get("/api/integrations/status").status_code == 403
    grants.add("prompt:view")
    response = web.get("/api/integrations/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["promptAuthority"] == "project-mysql"
    assert not any(key in response.text.lower() for key in ("password", "secret_key", "access_token"))


def test_deepagent_content_blocks_keep_exact_prompt_version_association():
    from langchain_core.messages import SystemMessage, HumanMessage
    from agent.prompt_releases.bundles import builtin_bundle
    from agent.platform.telemetry import prompt_role
    bundle = builtin_bundle()
    for role in ("main", "procurement-analyst"):
        for content in (bundle.prompt(role), [{"type": "text", "text": bundle.prompt(role)}]):
            messages = [[SystemMessage(content=content), HumanMessage(content="question")]]
            assert prompt_role(bundle, messages) == role
    assert prompt_role(bundle, [[HumanMessage(content=bundle.prompt("main"))]]) is None
