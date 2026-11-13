"""Explicit real-provider acceptance harness; never operates on agent_db.

Run init, then api and worker in separate processes, inspect UI, run samples,
promote/rollback in UI, stop both processes, and run cleanup. Fixed credentials
are only for this disposable localhost test database. No production MCP or
sandbox is initialized; chat uses the real lightweight graph without tools.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import secrets
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ARTIFACT = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "ChipSupply/prompt-release-validation"
STATE = ARTIFACT / "isolated-acceptance.json"
USERNAME = "release_qa"
PASSWORD = "DisposableReleaseQA2026!"


def state():
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if not re.fullmatch(r"prompt_test_accept_[a-f0-9]{10}", value["database"]):
        raise RuntimeError("Refusing a non-disposable database")
    return value


async def storage():
    from agent.mysql_storage import mysql_storage, MySQLSettings
    mysql_storage.settings = replace(MySQLSettings.from_env(), database=state()["database"])
    await mysql_storage.initialize()
    async with mysql_storage.cursor() as cur:
        await cur.execute("SELECT DATABASE() AS name")
        assert (await cur.fetchone())["name"] == state()["database"]
    return mysql_storage


async def initialize():
    if STATE.exists():
        raise RuntimeError("Acceptance database already recorded; inspect/clean it first")
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"database": "prompt_test_accept_" + secrets.token_hex(5)}), encoding="utf-8")
    db = await storage()
    try:
        from api_view.auth import hash_password
        from agent.prompt_releases.service import PromptReleaseService
        assert await db.create_user(USERNAME, hash_password(PASSWORD))
        await db.grant_capability(USERNAME, "prompt:maintain", granted_by=USERNAME)
        await PromptReleaseService(db).initialize(USERNAME)
        print(json.dumps({"database": state()["database"], "url": "http://localhost:8092/#prompt-releases", "username": USERNAME}))
    finally:
        await db.close()


def api():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    from api_view.api import auth, chat, history, prompt_releases
    from api_view.agent_loader import agent_loader
    from agent.lightweight_agent import create_lightweight_agent
    from agent.prompt_releases.bundles import deployment_fingerprint
    from langchain_openai import ChatOpenAI

    @asynccontextmanager
    async def lifespan(app):
        db = await storage()
        os.environ["AGENT_PROMPT_RELEASES_ENABLED"] = "true"
        os.environ["AGENT_REQUEST_ROUTING_ENABLED"] = "true"
        cache = {}
        model = ChatOpenAI(model="deepseek-flash", api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=.1, max_tokens=4000, timeout=45, max_retries=0,
            extra_body={"thinking": {"type": "disabled"}})
        def lightweight(prompt_snapshot=None):
            key = prompt_snapshot.fingerprint
            if key not in cache:
                cache[key] = create_lightweight_agent(SimpleNamespace(analyst_mcp_tools=[], order_mcp_tools=[]),
                    checkpointer=db.checkpointer, model=model, prompt_snapshot=prompt_snapshot)
            return cache[key]
        agent_loader.get_lightweight_agent = lightweight
        async def reject_full(*args, **kwargs):
            raise RuntimeError("Isolated UI acceptance supports lightweight chat only; no production MCP or sandbox")
        agent_loader.get_agent_for_user = reject_full
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(lifespan=lifespan)
    for router in (auth.router, chat.router, history.router, prompt_releases.router):
        app.include_router(router, prefix="/api")
    @app.get("/health")
    async def health():
        return {"database": state()["database"], "pid": os.getpid(), "deploymentFingerprint": deployment_fingerprint()}
    app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="frontend")
    uvicorn.run(app, host="127.0.0.1", port=8092, log_level="warning")


async def worker():
    from agent.evaluation.repository import EvaluationRepository
    from agent.evaluation.worker import execute_run
    from agent.evaluation.workflow_runner import PRODUCTION_CREDENTIAL_KEYS
    db = await storage()
    environment = {k: v for k, v in os.environ.items() if k not in PRODUCTION_CREDENTIAL_KEYS}
    repo = EvaluationRepository(db)
    try:
        while True:
            await repo.recover_expired_leases()
            run_id = await repo.claim_next_run(worker_id=secrets.token_hex(16), lease_seconds=120)
            if run_id:
                await execute_run(repo, run_id, environment=environment)
            await asyncio.sleep(1)
    finally:
        await db.close()


async def samples():
    import httpx
    from api_view.auth import COOKIE_NAME, token_hash
    from agent.prompt_releases.repository import PromptRepository, bucket, now
    db = await storage()
    tokens = []
    try:
        release = await PromptRepository(db).state()
        assert release["status"] == "CANARY"
        groups = {"stable": [], "canary": []}
        for n in range(2000):
            name = f"qa_sample_{n}"
            arm = "canary" if bucket(name, release["salt"]) < release["percentage"] * 100 else "stable"
            if len(groups[arm]) < 20:
                groups[arm].append(name)
            if all(len(v) == 20 for v in groups.values()):
                break
        async with db.cursor() as cur:
            await cur.execute("SELECT password_hash FROM app_users WHERE username=%s", (USERNAME,))
            password_hash = (await cur.fetchone())["password_hash"]
        semaphore = asyncio.Semaphore(4)
        async def invoke(arm, username):
            async with semaphore:
                await db.create_user(username, password_hash)
                token = secrets.token_urlsafe(32)
                tokens.append(token)
                await db.create_auth_session(token_hash(token), username, now()+timedelta(hours=1))
                async with httpx.AsyncClient(base_url="http://localhost:8092", trust_env=False, timeout=100,
                        cookies={COOKIE_NAME: token}, headers={"X-Requested-With": "DeepAgent"}) as client:
                    response = await client.post("/api/chat/stream", json={"message": "请用一句话解释P95和P50。"})
                    response.raise_for_status()
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    assert not any(e["type"] == "error" for e in events)
                    version = next(e for e in events if e["type"] == "prompt_version")
                    assert version["cohort"] == arm, version
                    assert version["version"] == release["canary" if arm == "canary" else "stable"]
                    assert any(e["type"] == "done" for e in events)
                    return {"requestId": version["request_id"], "arm": arm, "version": version["version"],
                        "username": username, "thread": version["thread_id"]}
        results = await asyncio.gather(*(invoke(arm, name) for arm, names in groups.items() for name in names))
        observations = await PromptRepository(db).observations()
        assert all(observations["cohorts"][a]["sufficient"] for a in groups)
        (ARTIFACT / "isolated-samples.json").write_text(json.dumps({"requests": results, "observations": observations}, default=str, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({a: observations["cohorts"][a]["completedCount"] for a in groups}))
    finally:
        for token in tokens:
            await db.delete_auth_session(token_hash(token))
        await db.close()


async def status():
    from collections import Counter
    from agent.evaluation.repository import EvaluationRepository
    from agent.prompt_releases.service import PromptReleaseService
    from agent.prompt_releases.bundles import deployment_fingerprint
    db = await storage()
    try:
        async with db.cursor() as cur:
            await cur.execute("SELECT run_id,status,manifest_json FROM evaluation_runs ORDER BY created_at")
            runs = await cur.fetchall()
        reports, evidence = [], []
        for run in runs:
            report = await EvaluationRepository(db).get_report(run["run_id"])
            evidence.append(report)
            usages = [c.get("result", {}).get("usage", {}) for c in report["cases"]]
            reports.append({"runId": run["run_id"], "status": run["status"],
                "cases": dict(Counter(c["status"] for c in report["cases"])),
                "deploymentFingerprint": report["manifest"]["deploymentFingerprint"],
                "inputTokens": sum(u.get("inputTokens") or 0 for u in usages),
                "outputTokens": sum(u.get("outputTokens") or 0 for u in usages),
                "aggregate": report.get("aggregate")})
        service = PromptReleaseService(db)
        result = {"state": await service.state(), "fingerprint": deployment_fingerprint(),
            "runs": reports, "observations": await service.observations(), "events": await service.history("events"),
            "gates": await service.history("gates"), "audit": await service.history("audit")}
        (ARTIFACT / "isolated-results.json").write_text(json.dumps(result, default=str, ensure_ascii=False), encoding="utf-8")
        (ARTIFACT / "isolated-evidence.json").write_text(json.dumps(evidence, default=str, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"state": result["state"], "fingerprint": result["fingerprint"],
            "runs": [{k: v for k, v in r.items() if k != "aggregate"} for r in reports]}, default=str))
    finally:
        await db.close()


async def verify_rollback():
    import httpx
    from api_view.auth import COOKIE_NAME, token_hash
    from agent.prompt_releases.repository import PromptRepository, now
    from agent.prompt_releases.bundles import deployment_fingerprint
    db = await storage()
    token = secrets.token_urlsafe(32)
    try:
        release = await PromptRepository(db).state()
        assert release["status"] == "ROLLED_BACK" and release["stable"] == "builtin-v1"
        assert release["revision"] == 4
        prior = next(r for r in json.loads((ARTIFACT / "isolated-samples.json").read_text(encoding="utf-8"))["requests"] if r["arm"] == "canary")
        await db.create_auth_session(token_hash(token), prior["username"], now()+timedelta(hours=1))
        async with httpx.AsyncClient(base_url="http://localhost:8092", trust_env=False, timeout=100,
                cookies={COOKIE_NAME: token}, headers={"X-Requested-With": "DeepAgent"}) as client:
            health = (await client.get("/health")).json()
            assert health["database"] == state()["database"]
            assert health["deploymentFingerprint"] == deployment_fingerprint()
            response = await client.post("/api/chat/stream", json={"message": "请用一句话解释P95和P50。", "thread_id": prior["thread"]})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert not any(e["type"] == "error" for e in events)
            version = next(e for e in events if e["type"] == "prompt_version")
            assert version["version"] == "builtin-v1" and version["cohort"] == "stable"
            assert version["release_revision"] == 4
            (ARTIFACT / "isolated-rollback.json").write_text(json.dumps({"health": health, "migrated": version}), encoding="utf-8")
            print(json.dumps({"health": health, "migrated": version}))
    finally:
        await db.delete_auth_session(token_hash(token))
        await db.close()


async def cleanup():
    import aiomysql
    from agent.mysql_storage import MySQLSettings
    settings = MySQLSettings.from_env()
    name = state()["database"]
    connection = await aiomysql.connect(**settings.connection_options())
    try:
        async with connection.cursor() as cur:
            await cur.execute(f"DROP DATABASE `{name}`")
        STATE.unlink()
        print("Disposable acceptance database removed")
    finally:
        connection.close()
        await connection.ensure_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["init", "api", "worker", "samples", "status", "verify-rollback", "cleanup"])
    args = parser.parse_args()
    if args.mode == "api":
        api()
    else:
        asyncio.run({"init": initialize, "worker": worker, "samples": samples, "status": status,
            "verify-rollback": verify_rollback, "cleanup": cleanup}[args.mode]())
