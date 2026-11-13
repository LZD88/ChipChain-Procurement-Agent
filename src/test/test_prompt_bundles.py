from dataclasses import FrozenInstanceError

import pytest

from agent.prompt_releases.bundles import PromptBundleSnapshot, builtin_bundle, bundle_diff, deployment_fingerprint
from agent.prompt_releases.repository import bucket


def test_builtin_snapshot_is_immutable_and_canonical():
    original = builtin_bundle()
    with pytest.raises(FrozenInstanceError):
        original.version = "changed"
    changed_copy = original.roles
    changed_copy["main"] = "ignored"
    assert original.roles["main"] != "ignored"
    crlf = {key: value.replace("\n", "\r\n") for key, value in original.roles.items()}
    assert PromptBundleSnapshot.create("new", crlf).fingerprint == original.fingerprint
    revised = PromptBundleSnapshot.create("new", {**original.roles, "main": original.roles["main"] + "\n回答清晰。"})
    assert revised.fingerprint != original.fingerprint
    assert bundle_diff(original, revised)["main"]
    assert not bundle_diff(original, revised)["lightweight"]


@pytest.mark.parametrize("bad", ["{{run()}}", "${execute}", "{unknown_variable}", 'api_key="sk-' + "a" * 30 + '"'])
def test_rejects_executable_unknown_and_secret_content(bad):
    with pytest.raises(ValueError):
        PromptBundleSnapshot.create("bad", {**builtin_bundle().roles, "main": bad})


def test_missing_role_and_stable_deployment_fingerprint():
    with pytest.raises(ValueError):
        PromptBundleSnapshot.create("bad", {"main": "hello"})
    assert deployment_fingerprint() == deployment_fingerprint()
    assert bucket("Alice", "salt") == bucket("alice", "salt")
    assert len({bucket(str(i), "salt") for i in range(100)}) > 80


def test_deployment_tracks_code_tools_skills_model_and_protection(tmp_path, monkeypatch):
    from agent.prompt_releases import bundles
    monkeypatch.setenv('LLM_GATEWAY_ENABLED', 'false')
    monkeypatch.setenv('MCP_GATEWAY_ENABLED', 'false')
    root = tmp_path / 'src'
    monkeypatch.setattr(bundles, '__file__', str(root / 'agent/prompt_releases/bundles.py'))
    previous = deployment_fingerprint()
    for name in ('agent/graph.py', 'skills/analyst/config.yaml', 'mcp_server/tool.py'):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('changed', encoding='utf-8')
        current = deployment_fingerprint()
        assert current != previous
        previous = current
    monkeypatch.setenv('DEEPSEEK_BASE_URL', 'https://different.invalid')
    assert deployment_fingerprint() != previous
    previous = deployment_fingerprint()
    monkeypatch.setenv('LLM_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('LLM_GATEWAY_BASE_URL', 'http://localhost:18881/v1')
    monkeypatch.setenv('LLM_GATEWAY_API_KEY', 'test-only-gateway')
    assert deployment_fingerprint() != previous
    previous = deployment_fingerprint()
    monkeypatch.setenv('MCP_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('MCP_GATEWAY_BASE_URL', 'http://localhost:18881')
    assert deployment_fingerprint() != previous
    previous = deployment_fingerprint()
    monkeypatch.setattr(bundles, 'PROTECTION', bundles.PROTECTION + 'changed')
    assert deployment_fingerprint() != previous
