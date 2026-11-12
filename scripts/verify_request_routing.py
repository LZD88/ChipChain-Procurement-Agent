"""Live routing smoke test with a disposable account; never writes ERP data.

Credentials, cookies, user/thread/sandbox IDs and business results are omitted
from the report. --include-workspace only runs the harmless calculation 1+1.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import secrets
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import aiomysql
import httpx
from agent.mysql_storage import MySQLSettings
from agent.env_utils import SANDBOX_DOMAIN
from agent.request_classifier import resolve_request_route
from agent.request_routing import route_request


async def bound_sandbox(user):
    settings = MySQLSettings.from_env()
    connection = await aiomysql.connect(**settings.connection_options(), db=settings.database)
    try:
        async with connection.cursor() as cursor:
            # Read-only inspection; no schema initialization in this verifier.
            await cursor.execute("SELECT sandbox_id FROM sandbox_registry WHERE user_id=%s", (user,))
            return bool(await cursor.fetchone())
    finally:
        connection.close()


async def run(output, include_workspace=False, include_classifier=False):
    user = "routeqa-" + uuid.uuid4().hex[:8]
    report = {"health": {}, "optional": {}, "cases": []}
    if include_classifier:
        report["classifier"] = []
        # Classification only: semantic write examples must not execute ERP tools.
        for name, message, expected in [
            ("capabilities", "能聊聊你的本领不", "lightweight"),
            ("knowledge", "芯片和传感器差别大吗", "lightweight"),
            ("colloquial_read", "帮我瞅瞅六轴惯性传感器还有几片", "lightweight"),
            ("semantic_write", "把这批芯片订了吧", "sandbox"),
            ("contextual", "按刚才说的办", "sandbox"),
        ]:
            started = time.perf_counter()
            route = await resolve_request_route(message)
            report["classifier"].append({
                "name": name, "rule": route_request(message).reason,
                "route": route.mode, "reason": route.reason,
                "seconds": round(time.perf_counter() - started, 3),
                "passed": route.mode == expected and route.reason in {"llm_simple", "llm_sandbox"},
            })
    thread = None
    async with httpx.AsyncClient(trust_env=False, timeout=330,
                                 headers={"X-Requested-With": "DeepAgent"}) as client:
        for name, url in {
            "agent": "http://127.0.0.1:8090/health", "ui": "http://127.0.0.1:3000/",
            "erp": "http://127.0.0.1:8082/actuator/health", "sandbox": SANDBOX_DOMAIN.rstrip("/") + "/health",
            "dashboard": "http://127.0.0.1:3002/healthz", "prometheus": "http://127.0.0.1:9090/-/ready",
        }.items():
            report["health"][name] = (await client.get(url, timeout=5)).status_code
        for name, url in {"tempo": "http://127.0.0.1:3200/ready", "grafana": "http://127.0.0.1:3001/api/health",
                          "rabbitmq": "http://127.0.0.1:15692/metrics"}.items():
            try:
                report["optional"][name] = (await client.get(url, timeout=2)).status_code
            except httpx.HTTPError:
                report["optional"][name] = "unavailable"
        response = await client.post("http://127.0.0.1:8090/api/auth/register", json={
            "username": user, "password": secrets.token_urlsafe(24),
        })
        response.raise_for_status()
        cases = [("greeting", "你好呀", False),
                 ("capabilities_original", "你能做些什么呢", False),
                 ("capabilities_synonym", "你能干什么", False),
                 ("capabilities_particle", "有什么功能呀", False),
                 ("capabilities_help", "可以帮我做哪些事情", False),
                 ("knowledge", "P50和P95是啥？", False),
                 ("readonly", "查询当前库存预警，最多列出三项。", False)]
        if include_classifier:
            cases += [("classifier_capabilities", "能聊聊你的本领不", False),
                      ("classifier_knowledge", "芯片和传感器差别大吗", False),
                      ("classifier_readonly", "帮我瞅瞅六轴惯性传感器还有几片", False)]
        if include_workspace:
            cases += [("workspace", "请在分析环境中运行 Python 计算 1+1，并告诉我结果。", True),
                      ("knowledge_after_workspace", "P95是啥？", False)]
        try:
            for name, message, expect_preparation in cases:
                case = {"name": name, "tools": [], "preparing": False, "classifying": False, "done": False}
                started = time.perf_counter()
                payload = {"message": message}
                if thread:
                    payload["thread_id"] = thread
                async with client.stream("POST", "http://127.0.0.1:8090/api/chat/stream", json=payload) as stream:
                    case["http_status"] = stream.status_code
                    stream.raise_for_status()
                    case["guard_and_headers_seconds"] = round(time.perf_counter() - started, 3)
                    async for line in stream.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line[5:])
                        thread = event.get("thread_id") or thread
                        if event.get("stage") == "preparing":
                            case["preparing"] = True
                        if event.get("stage") == "routing":
                            case["classifying"] = True
                        if event.get("type") == "token":
                            case.setdefault("first_token_seconds", round(time.perf_counter() - started, 3))
                        if event.get("type") == "tool_start":
                            case["tools"].append(event.get("tool_name"))
                        if name == "workspace" and event.get("type") == "tool_result" and event.get("tool_name") == "execute":
                            text = event.get("text", "")
                            case["calculation_verified"] = bool(re.search(r"(?m)^2\s*$", text)) and "exit code 0" in text
                        if event.get("type") == "error":
                            case["error"] = event.get("message")
                        if event.get("type") == "done":
                            case["done"] = not event.get("interrupted", False) and not event.get("partial", False)
                            case["answer_characters"] = len(event.get("content", ""))
                case["total_seconds"] = round(time.perf_counter() - started, 3)
                case["sandbox_bound"] = await bound_sandbox(user)
                case["passed"] = case["done"] and case["preparing"] == expect_preparation and "error" not in case
                if not include_workspace or name not in {"workspace", "knowledge_after_workspace"}:
                    case["passed"] = case["passed"] and not case["sandbox_bound"]
                if name == "readonly":
                    case["passed"] = case["passed"] and "inventory_warning" in case["tools"]
                if name.startswith("classifier_"):
                    case["passed"] = case["passed"] and case["classifying"]
                if name == "classifier_readonly":
                    case["passed"] = case["passed"] and "inventory_query" in case["tools"]
                if name == "workspace":
                    case["passed"] = case["passed"] and "execute" in case["tools"] and case.get("calculation_verified", False)
                report["cases"].append(case)
                Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(case, ensure_ascii=False), flush=True)
        finally:
            await client.post("http://127.0.0.1:8090/api/auth/logout")
    return (all(status == 200 for status in report["health"].values())
            and all(case["passed"] for case in report["cases"])
            and all(case["passed"] for case in report.get("classifier", [])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime-logs/request-routing-verification.json")
    parser.add_argument("--include-workspace", action="store_true")
    parser.add_argument("--include-classifier", action="store_true")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.output, args.include_workspace, args.include_classifier)) else 1)
