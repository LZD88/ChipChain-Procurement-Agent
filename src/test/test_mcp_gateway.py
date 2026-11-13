import importlib.util
from pathlib import Path
import pytest
from agent.platform.mcp_gateway import mcp_server_config


def environment():
    return {"MCP_GATEWAY_ENABLED": "true", "MCP_GATEWAY_BASE_URL": "http://localhost:18881",
            "MCP_GATEWAY_API_KEY": "mcp-only", "LLM_GATEWAY_API_KEY": "web-only",
            "LLM_GATEWAY_EVALUATION_KEY": "eval-only", "DEEPSEEK_API_KEY": "provider-only",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "HIGRESS_MCP_ERP_UPSTREAM": "http://10.0.2.2:8000/mcp",
            "HIGRESS_MCP_ANALYSIS_UPSTREAM": "https://charts.example/provider/mcp"}


def test_mcp_gateway_routes_both_servers_with_dedicated_server_side_headers():
    config = mcp_server_config("http://localhost:8000/mcp", "https://charts.example/provider/mcp", environment())
    assert config["chip-supply-api"]["url"] == "http://localhost:18881/mcp-servers/chipchain-erp"
    assert config["analysis"]["url"] == "http://localhost:18881/mcp-servers/chipchain-charts"
    assert all(c["headers"] == {"Authorization": "Bearer mcp-only"} for c in config.values())
    assert all(c["transport"] == "streamable_http" for c in config.values())
    assert not mcp_server_config("http://localhost:8000/mcp", None, environment())["analysis"].get("headers")


@pytest.mark.parametrize("patch", [
    {"MCP_GATEWAY_API_KEY": ""}, {"MCP_GATEWAY_API_KEY": "provider-only"},
    {"MCP_GATEWAY_API_KEY": "web-only"}, {"MCP_GATEWAY_API_KEY": "eval-only"},
    {"MCP_GATEWAY_BASE_URL": "http://remote.example"},
    {"MCP_GATEWAY_BASE_URL": "https://user:pass@remote.example"},
    {"MCP_GATEWAY_BASE_URL": "https://remote.example?key=value"},
])
def test_invalid_mcp_gateway_does_not_fall_back_to_direct_urls(patch):
    with pytest.raises(ValueError):
        mcp_server_config("http://localhost:8000/mcp", None, {**environment(), **patch})


def test_disabled_mcp_gateway_preserves_direct_transport():
    config = mcp_server_config("http://localhost:8000/mcp", None, {"MCP_GATEWAY_ENABLED": "false"})
    assert config["chip-supply-api"] == {"url": "http://localhost:8000/mcp", "transport": "streamable_http"}


def test_mcp_resources_scope_credentials_and_preserve_protocol_and_model_policy():
    path = Path(__file__).resolve().parents[2] / "ops/higress/render_config.py"
    spec = importlib.util.spec_from_file_location("mcp_gateway_render", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    resources = module.build_resources(environment())
    auth = resources["wasmplugins/key-auth.internal.yaml"]["spec"]
    assert auth["failStrategy"] == "FAIL_CLOSE"
    assert auth["defaultConfig"]["global_auth"] is True
    assert [r["config"]["allow"] for r in auth["matchRules"]] == [
        ["chipchain-web", "chipchain-evaluation"], ["chipchain-mcp"], ["chipchain-mcp"]]
    for name, upstream_path in (("chipchain-erp", "/mcp"), ("chipchain-charts", "/provider/mcp")):
        route = resources[f"ingresses/mcp-server-{name}.internal.yaml"]
        a = route["metadata"]["annotations"]
        assert a["higress.io/mcp-server-upstream-type"] == "streamable"
        assert a["higress.io/rewrite-path"] == upstream_path
        assert a["higress.io/request-header-control-remove"] == "Authorization"
        assert a["higress.io/enable-proxy-next-upstream"] == "false"
        assert "session" not in a["higress.io/request-header-control-remove"].lower()
    assert "wasmplugins/chipchain-key-auth.yaml" not in resources
    provider = resources["wasmplugins/ai-proxy.internal.yaml"]["spec"]["defaultConfig"]["providers"][0]
    assert provider["retryOnFailure"]["enabled"] is False
