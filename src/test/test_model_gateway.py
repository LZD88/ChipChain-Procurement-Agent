import importlib.util
from pathlib import Path
import pytest
from agent.platform.gateway import model_endpoint, transport_policy


def gateway_env():
    return {"LLM_GATEWAY_ENABLED": "true", "LLM_GATEWAY_BASE_URL": "http://localhost:18881/v1",
            "LLM_GATEWAY_API_KEY": "dedicated-web", "LLM_GATEWAY_EVALUATION_KEY": "dedicated-eval",
            "DEEPSEEK_API_KEY": "provider-secret", "DEEPSEEK_BASE_URL": "https://api.deepseek.com"}


def test_gateway_uses_dedicated_credentials_for_web_and_evaluation():
    env = gateway_env()
    web = model_endpoint(env)
    evaluation = model_endpoint(env, purpose="evaluation")
    assert web.base_url == evaluation.base_url == env["LLM_GATEWAY_BASE_URL"]
    assert web.api_key == "dedicated-web" and evaluation.api_key == "dedicated-eval"
    assert "dedicated-web" not in repr(web)
    assert transport_policy(web) == {"max_retries": 0, "timeout": 45}


@pytest.mark.parametrize("change", [
    {"LLM_GATEWAY_API_KEY": ""}, {"LLM_GATEWAY_API_KEY": "provider-secret"},
    {"LLM_GATEWAY_BASE_URL": "http://remote.example/v1"},
    {"LLM_GATEWAY_BASE_URL": "https://user:pass@remote.example/v1"},
    {"LLM_GATEWAY_BASE_URL": "https://remote.example/v1?key=secret"},
])
def test_bad_gateway_configuration_fails_without_direct_provider_bypass(change):
    with pytest.raises(ValueError):
        model_endpoint({**gateway_env(), **change})


def test_disabled_gateway_keeps_existing_provider():
    endpoint = model_endpoint({**gateway_env(), "LLM_GATEWAY_ENABLED": "false"})
    assert endpoint.base_url == "https://api.deepseek.com"
    assert endpoint.api_key == "provider-secret"
    assert not endpoint.via_gateway


def test_rendered_gateway_enforces_auth_limits_and_fixed_model_routes():
    path = Path(__file__).resolve().parents[2] / "ops/higress/render_config.py"
    module_spec = importlib.util.spec_from_file_location("gateway_render", path)
    render = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(render)
    resources = render.build_resources(gateway_env())
    auth = resources["wasmplugins/chipchain-key-auth.yaml"]["spec"]
    assert auth["failStrategy"] == "FAIL_CLOSE"
    assert auth["defaultConfig"]["in_query"] is False
    assert auth["defaultConfig"]["consumers"][0]["credential"] == "Bearer dedicated-web"
    quota = resources["wasmplugins/chipchain-key-rate-limit.yaml"]["spec"]["defaultConfig"]
    assert quota["limit_by_header"] == "x-mse-consumer"
    assert {rule["key"] for rule in quota["limit_keys"]} == {"chipchain-web", "chipchain-evaluation"}
    provider = resources["wasmplugins/ai-proxy.internal.yaml"]["spec"]["defaultConfig"]["providers"][0]
    assert provider["apiTokens"] == ["provider-secret"]
    assert provider["retryOnFailure"]["enabled"] is False
    assert provider["failover"]["failureThreshold"] == 3
    assert provider["failover"]["healthCheckModel"] == "deepseek-flash"
    ingress = resources["ingresses/ai-route-chipchain.internal.yaml"]
    assert "^deepseek-(flash|chat)$" in str(ingress)
    assert ingress["metadata"]["annotations"]["higress.io/enable-proxy-next-upstream"] == "false"
    assert resources["configmaps/ai-route-chipchain.yaml"]["metadata"]["labels"]["higress.io/resource-definer"] == "higress"
    circuit = resources["envoyfilters/chipchain-model-circuit.yaml"]["spec"]["configPatches"][0]["patch"]["value"]
    assert circuit["common_lb_config"]["healthy_panic_threshold"]["value"] == 0
    assert circuit["outlier_detection"]["consecutive_5xx"] == 3
    assert circuit["outlier_detection"]["base_ejection_time"] == "30s"
    assert resources["wasmplugins/chipchain-ai-statistics.yaml"]["spec"]["defaultConfig"]["use_default_attributes"] is False
