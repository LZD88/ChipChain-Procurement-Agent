"""Use a disposable authenticated account for a read-only SSE and readiness check."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import httpx
from agent.env_utils import SANDBOX_DOMAIN, ERP_MCP_URL
from agent.mysql_storage import mysql_storage


async def run(output, prepare_first=False, stale_binding=False):
    report = {"checks": {}, "sse": {}, "optional": {}}
    user = "prepqa-" + uuid.uuid4().hex[:8]
    password = secrets.token_urlsafe(24)
    thread = None
    async with httpx.AsyncClient(trust_env=False, timeout=310) as client:
        for name, url in {
            "sandbox": SANDBOX_DOMAIN.rstrip("/") + "/health",
            "erp": "http://127.0.0.1:8082/actuator/health",
            "agent": "http://127.0.0.1:8090/health", "ui": "http://127.0.0.1:3000/",
            "dashboard": "http://127.0.0.1:3002/healthz", "prometheus": "http://127.0.0.1:9090/-/ready",
        }.items():
            response = await client.get(url)
            report["checks"][name] = response.status_code
        for name, url in {"tempo": "http://127.0.0.1:3200/ready", "grafana": "http://127.0.0.1:3001/api/health",
                          "rabbitmq": "http://127.0.0.1:15692/metrics"}.items():
            try:
                report["optional"][name] = (await client.get(url, timeout=2)).status_code
            except httpx.HTTPError:
                report["optional"][name] = "unavailable"
        headers = {"X-Requested-With": "DeepAgent"}
        async def auth_ready_count():
            text = (await client.get("http://127.0.0.1:8090/metrics")).text
            match = re.search(r'^agent_preparation_schedule_total\{result="ready"\} ([0-9.e+]+)$', text, re.M)
            return float(match.group(1)) if match else 0
        if stale_binding:
            from api_view.auth import hash_password
            await mysql_storage.initialize()
            try:
                assert await mysql_storage.create_user(user, await asyncio.to_thread(hash_password, password))
                await mysql_storage.bind_sandbox(user, str(uuid.uuid4()))
            finally:
                await mysql_storage.close()
            report["scenario"] = "existing_user_with_expired_sandbox"
        started = time.perf_counter()
        response = await client.post("http://127.0.0.1:8090/api/auth/" + ("login" if stale_binding else "register"),
            headers=headers, json={"username": user, "password": password})
        report["authentication" if stale_binding else "registration"] = {
            "status": response.status_code, "seconds": time.perf_counter() - started}
        response.raise_for_status()
        if prepare_first:
            background_started = time.perf_counter()
            async with asyncio.timeout(300):
                while True:
                    count = await auth_ready_count()
                    (await client.get("http://127.0.0.1:8090/api/auth/me")).raise_for_status()
                    if await auth_ready_count() > count:
                        break
                    await asyncio.sleep(1)
            report["background_wait_seconds"] = time.perf_counter() - background_started
        started = time.perf_counter()
        events = []
        async with client.stream("POST", "http://127.0.0.1:8090/api/chat/stream", headers=headers,
            json={"message": "请只读查询当前库存预警，简要报告最多三项；不要创建或修改订单，也不要修改数据。"}) as stream:
            report["sse"]["http_status"] = stream.status_code
            async for line in stream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                if event.get("thread_id"):
                    thread = event["thread_id"]
                elapsed = time.perf_counter() - started
                events.append({"type": event.get("type"), "stage": event.get("stage"), "seconds": elapsed})
                if event.get("type") == "token" and "first_token_seconds" not in report["sse"]:
                    report["sse"]["first_token_seconds"] = elapsed
                if event.get("type") == "tool_start":
                    report["sse"].setdefault("tools", []).append(event.get("tool_name"))
                if event.get("type") == "error":
                    report["sse"]["error"] = event.get("message")
                if event.get("type") == "done":
                    report["sse"]["completed"] = True
                    report["sse"]["answer_length"] = len(event.get("content", ""))
        report["sse"]["total_seconds"] = time.perf_counter() - started
        report["sse"]["events"] = events
        report["sse"]["thread_received"] = bool(thread)
        metrics = (await client.get("http://127.0.0.1:8090/metrics")).text
        report["preparation_metrics_present"] = all(name in metrics for name in
            ("agent_preparation_duration_seconds", "agent_preparation_wait_seconds", "agent_sandbox_pool_ready"))
        report["pool"] = [line for line in metrics.splitlines() if line.startswith((
            "agent_sandbox_pool_ready ", "agent_sandbox_pool_target ", "agent_sandbox_pool_creating "))]
        # Logs/report omit cookies, passwords, user identity, thread and sandbox IDs.
        await client.post("http://127.0.0.1:8090/api/auth/logout", headers=headers)
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": report["checks"], "authentication": report.get("authentication", report.get("registration")),
        "completed": report["sse"].get("completed", False), "total_seconds": report["sse"]["total_seconds"],
        "first_token_seconds": report["sse"].get("first_token_seconds"),
        "tools": report["sse"].get("tools"), "optional": report["optional"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime-logs/preparation-web-verification.json")
    parser.add_argument("--prepare-first", action="store_true", help="Wait for /me to report a ready cached graph before chatting")
    parser.add_argument("--stale-binding", action="store_true", help="Log in a disposable existing account with an expired sandbox binding")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.prepare_first, args.stale_binding))
