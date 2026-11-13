"""Server-constructed evaluation tasks, immutable failure capture and experiments.

Research runs deliberately use a distinct mode and cannot satisfy release gates.
Historical code is compared through persisted evidence, never executed from input.
"""
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from agent.evaluation.manifest import EvaluationRunManifest
from agent.evaluation.repository import EvaluationRepository, EvaluationConflictError
from agent.evaluation.rule_runner import load_default_inputs
from agent.evaluation.schemas import EvaluationSuite, publish_suite
from agent.evidence_sanitizer import sanitize_event, reject_secret_bearing
from agent.prompt_releases.bundles import digest, model_config, deployment_fingerprint
from agent.prompt_releases.candidate_suite import candidate_suite
from agent.prompt_releases.repository import PromptRepository


WORKBENCH_DDL = {
    "evaluation_failures": """CREATE TABLE IF NOT EXISTS evaluation_failures (
        failure_id CHAR(36) PRIMARY KEY, title VARCHAR(200) NOT NULL,
        source_run_id CHAR(36) NOT NULL, scenario_id VARCHAR(64) NOT NULL,
        repetition INT NOT NULL, snapshot_json JSON NOT NULL,
        snapshot_fingerprint CHAR(64) NOT NULL, created_by VARCHAR(32) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        UNIQUE KEY uq_failure_source (source_run_id,scenario_id,repetition),
        FOREIGN KEY (source_run_id) REFERENCES evaluation_runs(run_id),
        FOREIGN KEY (created_by) REFERENCES app_users(username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    "evaluation_failure_replays": """CREATE TABLE IF NOT EXISTS evaluation_failure_replays (
        replay_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        failure_id CHAR(36) NOT NULL, run_id CHAR(36) NOT NULL UNIQUE,
        created_at DATETIME(6) NOT NULL,
        INDEX idx_failure_replay (failure_id,replay_id),
        FOREIGN KEY (failure_id) REFERENCES evaluation_failures(failure_id),
        FOREIGN KEY (run_id) REFERENCES evaluation_runs(run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
}


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    promptVersion: str | None = Field(None, max_length=64)
    modelId: str | None = Field(None, max_length=64)
    skillVariant: Literal["current", "without-skills"] = "current"


class TaskRequest(Variant):
    suiteId: str = Field(default="agent-real", pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    suiteVersion: int = Field(default=1, ge=1)
    executionMode: Literal["rule-contract", "workflow-safety", "experiment"] = "experiment"
    repetitions: int = Field(default=1, ge=1, le=3)
    scenarioIds: list[str] | None = Field(default=None, min_length=1, max_length=40)


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskRequest
    baseline: Variant
    candidate: Variant


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runId: str = Field(min_length=1, max_length=36)
    scenarioId: str = Field(min_length=1, max_length=64)
    repetition: int = Field(default=1, ge=1, le=20)
    title: str = Field(min_length=1, max_length=200)


def configuration_for(variant):
    configuration = model_config()
    model = variant.modelId or configuration["model"]
    # These names match the project's reviewed Higress model routes.
    if model not in {configuration["model"], "deepseek-flash", "deepseek-chat"}:
        raise ValueError("模型不在评测允许列表中")
    return {**configuration, "model": model}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def real_suite(owner):
    suite = candidate_suite(owner)
    analyst = next(s for s in suite.scenarios if s.id == "analyst-compare")
    skill_case = analyst.model_copy(update={"id": "analyst-skills", "title": "读取技能后完成采购分析",
        "fixture": {**analyst.fixture, "question": "请先读取 /skills/procurement/procurement-analysis/SKILL.md，参考技能分析采购10个传感器，预算1500、交期14天，再用 procurement_compare 比价。只给简短结论，不下单。"},
        "assertions": (*analyst.assertions, analyst.assertions[-1].model_copy(update={"type": "skill_loaded", "expected": True}))})
    return suite.model_copy(update={"scenarios": (*suite.scenarios, skill_case)})


class Workbench:
    def __init__(self, storage):
        self.storage = storage
        self.repository = EvaluationRepository(storage)
        self.prompts = PromptRepository(storage)

    async def catalog(self):
        suite, _ = load_default_inputs()
        items = [{"suiteId": "agent-real", "version": 1, "title": "Agent 真实调用评测",
                  "modes": ["experiment"], "scenarios": [s.model_dump() for s in real_suite("evaluation-system").scenarios]},
                 {"suiteId": suite.suiteId, "version": 1, "title": suite.title,
                  "modes": ["rule-contract", "workflow-safety"], "scenarios": [s.model_dump() for s in suite.scenarios]}]
        for row in await self.repository.list_suites(size=100):
            if row["suite_id"].startswith(("wb-", "replay-")) or any(i["suiteId"] == row["suite_id"] and i["version"] == row["version"] for i in items):
                continue
            stored = await self.repository.get_suite(row["suite_id"], row["version"])
            scenarios = stored["suite"]["scenarios"]
            modes = sorted({s["executionMode"] for s in scenarios} - {"prompt-candidate"})
            if modes:
                items.append({"suiteId": row["suite_id"], "version": row["version"], "title": row["title"], "modes": modes, "scenarios": scenarios})
        config = model_config()
        return {"suites": items, "defaultModel": config["model"], "models": sorted({config["model"], "deepseek-flash", "deepseek-chat"}),
                "prompts": await self.prompts.list_versions(limit=100),
                "skills": [{"id": "current", "title": "当前技能"}, {"id": "without-skills", "title": "不加载技能（对照实验）"}],
                "codeRevision": deployment_fingerprint(), "codePolicy": "新任务使用当前代码；不同代码版本通过历史运行比较"}

    async def publish(self, suite):
        published = publish_suite(suite, version=1)
        try:
            await self.repository.publish(published)
        except EvaluationConflictError:
            stored = await self.repository.get_suite(suite.suiteId, 1)
            if stored["content_fingerprint"] != published.contentFingerprint:
                raise ValueError("套件快照冲突")
        return published

    async def prepare(self, request, actor, *, frozen=None):
        if frozen:
            source = EvaluationSuite.model_validate(frozen)
        elif request.suiteId == "agent-real":
            if request.executionMode != "experiment":
                raise ValueError("Agent 真实调用套件必须使用真实实验模式")
            source = real_suite(actor)
        else:
            default, _ = load_default_inputs()
            if request.suiteId == default.suiteId and request.suiteVersion == 1:
                source = default
            else:
                stored = await self.repository.get_suite(request.suiteId, request.suiteVersion)
                if not stored:
                    raise ValueError("套件版本不存在")
                source = EvaluationSuite.model_validate(stored["suite"])
        selected = request.scenarioIds
        if selected and len(selected) != len(set(selected)):
            raise ValueError("场景不能重复")
        scenarios = []
        for scenario in source.scenarios:
            source_mode = scenario.executionMode
            if source_mode != request.executionMode and not (request.executionMode == "experiment" and source_mode == "prompt-candidate"):
                continue
            if selected and scenario.id not in selected:
                continue
            scenarios.append({**scenario.model_dump(), "executionMode": request.executionMode,
                              "repetitions": 1 if request.executionMode == "rule-contract" else request.repetitions})
        if not scenarios or (selected and set(selected) != {s["id"] for s in scenarios}):
            raise ValueError("所选模式没有这些场景")
        payload = {"title": source.title, "owner": actor, "fixtureVersion": source.fixtureVersion, "scenarios": scenarios}
        # Owner is provenance, not part of the scenario identity used across users.
        suite_id = "wb-" + digest({k: v for k, v in payload.items() if k != "owner"})[:32]
        existing = await self.repository.get_suite(suite_id, 1)
        suite = EvaluationSuite.model_validate(existing["suite"] if existing else {"suiteId": suite_id, **payload})
        await self.publish(suite)
        return suite

    async def manifest(self, suite, variant, *, mode, failure_id=None, experiment_id=None, arm=None):
        from agent.policies.procurement import POLICY_VERSION
        state = await self.prompts.state()
        bundle = await self.prompts.get_version(variant.promptVersion or state["stable"])
        configuration = configuration_for(variant)
        deployment = deployment_fingerprint()
        _, fixtures = load_default_inputs()
        price_key = "EVALUATION_PRICE_" + configuration["model"].upper().replace("-", "_")
        prices = {}
        if os.getenv(price_key + "_INPUT_PER_1K") and os.getenv(price_key + "_OUTPUT_PER_1K"):
            prices = {"inputPricePer1k": float(os.environ[price_key + "_INPUT_PER_1K"]),
                      "outputPricePer1k": float(os.environ[price_key + "_OUTPUT_PER_1K"]),
                      "priceCurrency": os.getenv("EVALUATION_PRICE_CURRENCY", "CNY")}
        return EvaluationRunManifest(suiteId=suite.suiteId, suiteVersion=1, suiteFingerprint=suite.content_fingerprint(),
            fixtureVersion=suite.fixtureVersion, fixtureFingerprint=digest(fixtures), policyVersion=POLICY_VERSION,
            policyFingerprint=digest(POLICY_VERSION), codeRevision=deployment, deploymentFingerprint=deployment,
            promptVersion=bundle.version, promptFingerprint=bundle.fingerprint,
            skillFingerprint=digest([deployment, variant.skillVariant]), skillVariant=variant.skillVariant,
            modelId=configuration["model"], providerId=configuration["provider"], modelConfigFingerprint=digest(configuration),
            runnerVersion="workbench-v1", environment="isolated-test", executionMode=mode,
            repetitionCount=suite.scenarios[0].repetitions, startedAt=_now(),
            failureCaseId=failure_id, experimentId=experiment_id, experimentArm=arm, **prices)

    async def enqueue(self, manifests, actor, *, failure_id=None):
        ids = []
        async with self.storage.cursor(transaction=True) as cur:
            if failure_id:
                await cur.execute("SELECT failure_id FROM evaluation_failures WHERE failure_id=%s FOR UPDATE", (failure_id,))
                await cur.fetchone()
                await cur.execute("SELECT r.run_id FROM evaluation_failure_replays f JOIN evaluation_runs r ON r.run_id=f.run_id WHERE f.failure_id=%s AND r.status IN ('QUEUED','RUNNING') LIMIT 1", (failure_id,))
                if await cur.fetchone():
                    raise EvaluationConflictError("该案例已有重跑任务，请等待完成或取消后再试")
            # Pair admission and failure-to-replay links commit with queued runs.
            for manifest in manifests:
                run_id = str(uuid4())
                await cur.execute("INSERT INTO evaluation_runs(run_id,suite_id,suite_version,requested_by,execution_mode,status,manifest_json,created_at) VALUES(%s,%s,%s,%s,%s,'QUEUED',%s,UTC_TIMESTAMP(6))",
                    (run_id, manifest.suiteId, manifest.suiteVersion, actor, manifest.executionMode,
                     json.dumps({**manifest.model_dump(mode="json"), "manifestFingerprint": manifest.fingerprint()})))
                if failure_id:
                    await cur.execute("INSERT INTO evaluation_failure_replays(failure_id,run_id,created_at) VALUES(%s,%s,UTC_TIMESTAMP(6))", (failure_id, run_id))
                await cur.execute("INSERT INTO evaluation_audit_events(actor_user_id,action,resource_type,resource_id,outcome,metadata_json,created_at) VALUES(%s,'TASK_CREATE','EVALUATION_RUN',%s,'SUCCESS',%s,UTC_TIMESTAMP(6))",
                    (actor, run_id, json.dumps({"experimentId": manifest.experimentId, "failureCaseId": failure_id})))
                ids.append(run_id)
        return ids

    async def create_task(self, request, actor):
        suite = await self.prepare(request, actor)
        manifest = await self.manifest(suite, request, mode=request.executionMode)
        ids = await self.enqueue([manifest], actor)
        return {"runId": ids[0], "status": "QUEUED"}

    async def experiment(self, request, actor):
        if request.task.executionMode != "experiment":
            raise ValueError("模型、提示词和技能对比需要真实实验模式")
        suite = await self.prepare(request.task, actor)
        experiment_id = str(uuid4())
        manifests = [await self.manifest(suite, variant, mode="experiment", experiment_id=experiment_id, arm=arm)
                     for arm, variant in (("baseline", request.baseline), ("candidate", request.candidate))]
        if all(getattr(manifests[0], k) == getattr(manifests[1], k) for k in ("modelId", "promptFingerprint", "skillVariant")):
            raise ValueError("请至少改变模型、提示词或技能中的一项")
        ids = await self.enqueue(manifests, actor)
        return {"experimentId": experiment_id, "baselineRunId": ids[0], "candidateRunId": ids[1], "status": "QUEUED"}

    async def capture(self, request, actor):
        report = await self.repository.get_report(request.runId)
        if not report or report["status"] in {"QUEUED", "RUNNING"}:
            raise ValueError("请从已结束的评测中保存失败场景")
        case = next((c for c in report["cases"] if c["scenarioId"] == request.scenarioId and c["repetition"] == request.repetition), None)
        if not case or case["status"] not in {"FAILED", "TIMED_OUT", "SETUP_ERROR", "DEPENDENCY_ERROR"}:
            raise ValueError("只允许保存实际失败的场景")
        suite = await self.repository.get_suite(report["suiteId"], report["suiteVersion"])
        scenario = next(s for s in suite["suite"]["scenarios"] if s["id"] == request.scenarioId)
        reject_secret_bearing(scenario["fixture"])
        frozen = {**suite["suite"], "scenarios": [scenario]}
        fixtures = load_default_inputs()[1]
        if report["manifest"]["fixtureFingerprint"] != digest(fixtures):
            raise ValueError("原运行的测试数据已变化，无法恢复原始输入，请从当前评测重新保存")
        snapshot = {"suite": frozen, "fixtures": fixtures, "sourceManifest": report["manifest"], "case": sanitize_event(case)}
        failure_id = str(uuid4())
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute("INSERT IGNORE INTO evaluation_failures(failure_id,title,source_run_id,scenario_id,repetition,snapshot_json,snapshot_fingerprint,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
                (failure_id, request.title, request.runId, request.scenarioId, request.repetition, json.dumps(snapshot, ensure_ascii=False), digest(snapshot), actor))
            await cur.execute("SELECT failure_id FROM evaluation_failures WHERE source_run_id=%s AND scenario_id=%s AND repetition=%s", (request.runId, request.scenarioId, request.repetition))
            failure_id = (await cur.fetchone())["failure_id"]
        await self.repository.record_audit(actor_user_id=actor, action="FAILURE_CAPTURE", resource_type="EVALUATION_FAILURE", resource_id=failure_id, outcome="SUCCESS")
        return {"failureId": failure_id}

    async def failure(self, failure_id):
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT * FROM evaluation_failures WHERE failure_id=%s", (failure_id,))
            row = await cur.fetchone()
        if not row:
            raise ValueError("失败案例不存在")
        snapshot = row["snapshot_json"]
        snapshot = snapshot if isinstance(snapshot, dict) else json.loads(snapshot)
        if digest(snapshot) != row["snapshot_fingerprint"]:
            raise ValueError("失败案例快照指纹不一致")
        return row, snapshot

    async def replay(self, failure_id, variant, actor):
        row, snapshot = await self.failure(failure_id)
        if digest(load_default_inputs()[1]) != digest(snapshot["fixtures"]):
            raise ValueError("冻结的测试数据与当前版本不同，需恢复对应数据版本后再重跑")
        mode = snapshot["sourceManifest"]["executionMode"]
        mode = "experiment" if mode == "prompt-candidate" else mode
        request = TaskRequest(executionMode=mode, repetitions=3 if mode == "experiment" else 1, **variant.model_dump())
        suite = await self.prepare(request, actor, frozen=snapshot["suite"])
        manifest = await self.manifest(suite, variant, mode=mode, failure_id=failure_id)
        ids = await self.enqueue([manifest], actor, failure_id=failure_id)
        return {"failureId": failure_id, "runId": ids[0], "status": "QUEUED"}

    async def failures(self, *, page=1, size=20):
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT failure_id,title,source_run_id,scenario_id,repetition,created_by,created_at FROM evaluation_failures ORDER BY created_at DESC,failure_id LIMIT %s OFFSET %s", (size, (page - 1) * size))
            rows = list(await cur.fetchall())
            for row in rows:
                await cur.execute("SELECT run_id FROM evaluation_failure_replays WHERE failure_id=%s ORDER BY replay_id DESC LIMIT 1", (row["failure_id"],))
                latest = await cur.fetchone()
                report = await self.repository.get_report(latest["run_id"]) if latest else None
                state = "OPEN"
                if report:
                    expected = report["manifest"]["repetitionCount"]
                    cases = report["cases"]
                    resolved = (report["status"] == "COMPLETED" and len(cases) == expected and all(c["status"] == "PASSED" and c["assertions"] and all(a["status"] == "PASSED" for a in c["assertions"]) for c in cases))
                    state = "RESOLVED" if resolved else "REPLAYING" if report["status"] in {"QUEUED", "RUNNING"} else "OPEN"
                row.update(state=state, latestRunId=latest["run_id"] if latest else None, latestStatus=report["status"] if report else None)
        return rows


def experiment_summary(report):
    cases = report["cases"]
    return {"runId": report["runId"], "status": report["status"], "manifest": report["manifest"],
            "quality": sum(c["status"] == "PASSED" for c in cases) / len(cases) if cases else None,
            "sampleCount": len(cases), "metrics": report["metrics"]}
