"""Render private Higress resources from local deployment credentials.

Run with the project's Python. Never commit the output directory: it contains
the upstream key and dedicated gateway credentials. Existing console settings
are preserved. The standard business route only accepts reviewed model names.
"""
import argparse
import json
import os
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit
import yaml
from dotenv import dotenv_values


def resource(kind, name, spec, api="extensions.higress.io/v1alpha1"):
    return {"apiVersion": api, "kind": kind, "metadata": {
        "name": name, "namespace": "higress-system", "resourceVersion": "1",
        "labels": {"higress.io/resource-definer": "higress"}}, "spec": spec}


def wasm(name, version, config, *, phase="UNSPECIFIED_PHASE", priority=100):
    value = resource("WasmPlugin", "chipchain-" + name, {
        "url": f"http://localhost:8002/plugins/{name}/{version}/plugin.wasm",
        "phase": phase, "priority": priority, "failStrategy": "FAIL_CLOSE",
        "defaultConfigDisable": False, "defaultConfig": config})
    value["metadata"]["labels"] = {"higress.io/wasm-plugin-name": name,
        "higress.io/wasm-plugin-version": version, "higress.io/resource-definer": "higress"}
    return value


def build_resources(env):
    url = urlsplit(env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    if (url.scheme != "https" or url.path.rstrip("/") not in {"", "/v1"} or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("Expected the validated HTTPS DeepSeek /v1-compatible endpoint")
    key = env["DEEPSEEK_API_KEY"]
    consumers = [{"name": name, "credential": "Bearer " + env[field]}
                 for name, field in [("chipchain-web", "LLM_GATEWAY_API_KEY"),
                                      ("chipchain-evaluation", "LLM_GATEWAY_EVALUATION_KEY")]]
    provider = {"id": "chipchain-deepseek", "type": "openai", "apiTokens": [key],
        "openaiCustomUrl": f"{url.scheme}://{url.netloc}/v1/chat/completions",
        "modelMapping": {"deepseek-flash": "deepseek-flash", "deepseek-chat": "deepseek-chat"},
        "failover": {"enabled": True, "failureThreshold": 3, "successThreshold": 1,
                     "healthCheckModel": "deepseek-flash", "healthCheckInterval": 30000,
                     "healthCheckTimeout": 5000, "failoverOnStatus": ["429", "5.*"]},
        "retryOnFailure": {"enabled": False}}
    bridge = resource("McpBridge", "default", {"registries": [
        {"name": "higress-console", "type": "static", "domain": "127.0.0.1:8001", "port": 80},
        {"name": "chipchain-deepseek", "type": "dns", "domain": url.hostname,
         "port": url.port or 443, "protocol": "https"}]}, "networking.higress.io/v1")
    route_name = "ai-route-chipchain.internal"
    ingress = resource("Ingress", route_name, {"ingressClassName": "higress", "rules": [
        {"http": {"paths": [{"path": "/v1/chat/completions", "pathType": "Exact", "backend": {
            "resource": {"apiGroup": "networking.higress.io", "kind": "McpBridge", "name": "default"}}}]}}]}, "networking.k8s.io/v1")
    ingress["metadata"]["annotations"] = {"higress.io/destination": f"chipchain-deepseek.dns:{url.port or 443}",
        "higress.io/regex-match-header-x-higress-llm-model": "^deepseek-(flash|chat)$",
        "higress.io/timeout": "45000", "higress.io/enable-proxy-next-upstream": "false"}
    proxy = wasm("ai-proxy", "2.0.4", {"providers": [provider]})
    proxy["metadata"]["name"] = "ai-proxy.internal"
    proxy["spec"]["matchRules"] = [{"ingress": [route_name], "config": {
        "activeProviderId": "chipchain-deepseek"}, "configDisable": False}]
    # Single gateway instance: local per-consumer quotas need no extra Redis.
    limit = wasm("key-rate-limit", "2.0.0", {"limit_by_header": "x-mse-consumer", "limit_keys": [
        {"key": "chipchain-web", "query_per_minute": 120},
        {"key": "chipchain-evaluation", "query_per_minute": 60}]}, priority=10)
    auth = wasm("key-auth", "2.0.0", {"global_auth": True, "in_header": True,
        "in_query": False, "keys": ["Authorization"], "consumers": consumers}, phase="AUTHN", priority=310)
    # ai-proxy deliberately reuses a failed token when every token is down.
    # Envoy outlier detection + disabled panic mode bounds those repeated calls.
    circuit = resource("EnvoyFilter", "chipchain-model-circuit", {"configPatches": [{
        "applyTo": "CLUSTER", "match": {"cluster": {"name": f"outbound|{url.port or 443}||chipchain-deepseek.dns"}},
        "patch": {"operation": "MERGE", "value": {"connect_timeout": "5s",
            "common_lb_config": {"healthy_panic_threshold": {"value": 0}},
            "outlier_detection": {"consecutive_5xx": 3, "interval": "1s", "base_ejection_time": "30s",
                "max_ejection_percent": 100, "enforcing_consecutive_5xx": 100,
                "split_external_local_origin_errors": True, "consecutive_local_origin_failure": 3,
                "enforcing_consecutive_local_origin_failure": 100}}}}]}, "networking.istio.io/v1alpha3")
    # Console metadata describes the same file-managed route; no secrets here.
    route = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "ai-route-chipchain", "namespace": "higress-system", "resourceVersion": "1",
        "labels": {"higress.io/config-map-type": "ai-route", "higress.io/resource-definer": "higress"}}, "data": {"data": json.dumps({
        "name": "chipchain", "domains": [],
        "pathPredicate": {"matchType": "EQUAL", "matchValue": "/v1/chat/completions"},
        "upstreams": [{"provider": "chipchain-deepseek", "weight": 100}],
        "modelPredicates": [{"matchType": "EQUAL", "matchValue": model}
                            for model in ("deepseek-flash", "deepseek-chat")],
        "proxyNextUpstream": {"enabled": False}})}}
    resources = {"mcpbridges/default.yaml": bridge, "ingresses/ai-route-chipchain.internal.yaml": ingress,
        "configmaps/ai-route-chipchain.yaml": route,
        "wasmplugins/chipchain-model-router.yaml": wasm("model-router", "2.0.4",
            {"modelToHeader": "x-higress-llm-model"}, phase="AUTHN", priority=900),
        "wasmplugins/ai-proxy.internal.yaml": proxy, "wasmplugins/chipchain-key-auth.yaml": auth,
        "wasmplugins/chipchain-key-rate-limit.yaml": limit,
        "envoyfilters/chipchain-model-circuit.yaml": circuit,
        "wasmplugins/chipchain-ai-statistics.yaml": wasm("ai-statistics", "2.0.3",
            {"use_default_attributes": False}, priority=900)}
    if env.get("MCP_GATEWAY_ENABLED", "false").lower() in {"true", "1", "yes"}:
        add_mcp_resources(resources, env)
    return resources


def add_mcp_resources(resources, env):
    """Use Higress native DIRECT_ROUTE metadata, preserving MCP bodies/sessions."""
    key = env.get("MCP_GATEWAY_API_KEY", "")
    if not key or key in {env.get(k) for k in (
            "DEEPSEEK_API_KEY", "LLM_GATEWAY_API_KEY", "LLM_GATEWAY_EVALUATION_KEY")}:
        raise ValueError("MCP requires a dedicated gateway credential")
    auth = resources.pop("wasmplugins/chipchain-key-auth.yaml")
    # Native consumer/MCP screens discover the internal authentication resource.
    auth["metadata"]["name"] = "key-auth.internal"
    auth["metadata"]["labels"]["higress.io/internal"] = "true"
    auth["spec"]["defaultConfig"]["consumers"].append({
        "name": "chipchain-mcp", "credential": "Bearer " + key})
    for consumer in auth["spec"]["defaultConfig"]["consumers"]:
        consumer.update(keys=["Authorization"], in_header=True, in_query=False)
    auth["spec"]["matchRules"] = [{"ingress": ["ai-route-chipchain.internal"],
        "config": {"allow": ["chipchain-web", "chipchain-evaluation"]}, "configDisable": False}]
    resources["wasmplugins/key-auth.internal.yaml"] = auth
    resources["wasmplugins/chipchain-key-rate-limit.yaml"]["spec"]["defaultConfig"]["limit_keys"].append(
        {"key": "chipchain-mcp", "query_per_minute": 300})
    for name, field, description in (
            ("chipchain-erp", "HIGRESS_MCP_ERP_UPSTREAM", "ERP 采购工具（保留业务身份、审批和幂等校验）"),
            ("chipchain-charts", "HIGRESS_MCP_ANALYSIS_UPSTREAM", "图表和表格工具")):
        raw_url = env.get(field, "")
        if not raw_url and name == "chipchain-charts":
            continue
        url = urlsplit(raw_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or not url.path.startswith("/")
                or url.username or url.password or url.query or url.fragment):
            raise ValueError(f"{field} must be an HTTP(S) MCP URL without embedded credentials or query")
        if url.scheme == "http" and url.hostname != "10.0.2.2":
            raise ValueError("Plain HTTP MCP upstreams are restricted to the VirtualBox host")
        try:
            ipaddress.ip_address(url.hostname)
            static = True
        except ValueError:
            static = False
        port = url.port or (443 if url.scheme == "https" else 80)
        service_type = "static" if static else "dns"
        registry = {"name": name, "type": service_type, "port": 80 if static else port,
                    "domain": f"{url.hostname}:{port}" if static else url.hostname, "protocol": url.scheme}
        resources["mcpbridges/default.yaml"]["spec"]["registries"].append(registry)
        route_name = f"mcp-server-{name}.internal"
        public_path = f"/mcp-servers/{name}"
        ingress = resource("Ingress", route_name, {"ingressClassName": "higress", "rules": [
            {"http": {"paths": [{"path": public_path, "pathType": "Prefix", "backend": {
                "resource": {"apiGroup": "networking.higress.io", "kind": "McpBridge", "name": "default"}}}]}}]},
            "networking.k8s.io/v1")
        ingress["metadata"]["labels"].update({"higress.io/biz-type": "mcp-server",
            "higress.io/internal": "true", "higress.io/mcp-server-type": "DIRECT_ROUTE"})
        ingress["metadata"]["annotations"] = {
            "higress.io/destination": f"{name}.{service_type}:{registry['port']}",
            "higress.io/mcp-server": "true", "higress.io/mcp-server-match-rule-domains": "*",
            "higress.io/mcp-server-match-rule-type": "prefix", "higress.io/mcp-server-match-rule-value": public_path,
            "higress.io/mcp-server-upstream-type": "streamable",
            "higress.io/mcp-server-upstream-transport-type": "streamable",
            "higress.io/mcp-server-enable-path-rewrite": "true",
            "higress.io/mcp-server-path-rewrite-prefix": url.path,
            "higress.io/resource-description": description,
            "higress.io/enable-rewrite": "true", "higress.io/rewrite-path": url.path,
            "higress.io/upstream-vhost": url.hostname,
            "higress.io/enable-header-control": "true",
            "higress.io/request-header-control-remove": "Authorization",
            "higress.io/enable-proxy-next-upstream": "false", "higress.io/timeout": "120000"}
        resources[f"ingresses/{route_name}.yaml"] = ingress
        auth["spec"]["matchRules"].append({"ingress": [route_name],
            "config": {"allow": ["chipchain-mcp"]}, "configDisable": False})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    resources = build_resources(dotenv_values(args.env_file))
    for path, value in resources.items():
        target = args.output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(target, 0o600)
    print(f"Rendered {len(resources)} private gateway resources")


if __name__ == "__main__":
    main()
