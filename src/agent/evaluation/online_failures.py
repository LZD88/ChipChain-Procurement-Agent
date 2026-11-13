"""Owner-scoped failed online inputs, explicitly imported into isolated replay."""
import json
import os
import re
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ONLINE_DDL = {"evaluation_online_failures": """CREATE TABLE IF NOT EXISTS evaluation_online_failures (
    request_id CHAR(36) PRIMARY KEY, owner_user_id VARCHAR(32) NOT NULL,
    thread_id VARCHAR(150) NOT NULL, snapshot_json JSON NOT NULL,
    source_run_id CHAR(36) NULL, created_at DATETIME(6) NOT NULL,
    INDEX idx_online_failure_owner (owner_user_id,created_at),
    FOREIGN KEY (owner_user_id) REFERENCES app_users(username),
    FOREIGN KEY (source_run_id) REFERENCES evaluation_runs(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""}


class OnlineCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str = Field(min_length=1, max_length=36)
    scenarioId: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)


def redact_question(question):
    text = question[:8000]
    for key, value in os.environ.items():
        if re.search(r'PASSWORD|SECRET|API_KEY|TOKEN', key) and value and len(value) >= 8:
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'(?i)(bearer\s+|(?:password|api[_-]?key|secret|token)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', text)


async def record_online_failure(storage, *, request_id, owner, thread_id, question, route, prompt_version, trace_id=None):
    if not question or not question.strip():
        return  # Approval resumes need their original interaction, not an invented question.
    snapshot = {"question": redact_question(question), "route": route, "promptVersion": prompt_version,
                "traceId": trace_id, "failureKind": "online-request-failure"}
    async with storage.cursor(transaction=True) as cur:
        await cur.execute("INSERT IGNORE INTO evaluation_online_failures(request_id,owner_user_id,thread_id,snapshot_json,created_at) VALUES(%s,%s,%s,%s,UTC_TIMESTAMP(6))",
            (request_id, owner, thread_id, json.dumps(snapshot, ensure_ascii=False)))


async def list_online_failures(storage, actor):
    async with storage.cursor() as cur:
        await cur.execute("SELECT request_id,thread_id,snapshot_json,created_at,source_run_id FROM evaluation_online_failures WHERE owner_user_id=%s AND created_at>UTC_TIMESTAMP()-INTERVAL 30 DAY ORDER BY created_at DESC LIMIT 50", (actor,))
        rows = list(await cur.fetchall())
    for row in rows:
        value = row.pop("snapshot_json")
        row["snapshot"] = value if isinstance(value, dict) else json.loads(value)
    return rows


async def capture_online_failure(workbench, request, actor):
    from agent.evaluation.workbench import real_suite, TaskRequest, Variant, CaptureRequest
    from agent.prompt_releases.bundles import digest
    from agent.evaluation.schemas import EvaluationSuite
    async with workbench.storage.cursor() as cur:
        await cur.execute("SELECT * FROM evaluation_online_failures WHERE request_id=%s AND owner_user_id=%s", (request.requestId, actor))
        row = await cur.fetchone()
    if not row:
        raise ValueError("线上失败记录不存在或无权访问")
    if row["source_run_id"]:
        report = await workbench.repository.get_report(row["source_run_id"])
        return await workbench.capture(CaptureRequest(runId=row["source_run_id"], scenarioId=report["cases"][0]["scenarioId"], title=request.title), actor)
    snapshot = row["snapshot_json"]
    snapshot = snapshot if isinstance(snapshot, dict) else json.loads(snapshot)
    templates = real_suite(actor)
    template = next((s for s in templates.scenarios if s.id == request.scenarioId), None)
    if not template:
        raise ValueError("请选择已审核的回归检查场景")
    fixture = {**template.fixture, "question": snapshot["question"]}
    suite = templates.model_copy(update={"scenarios": (template.model_copy(update={"fixture": fixture}),)})
    suite = await workbench.prepare(TaskRequest(scenarioIds=[template.id]), actor, frozen=suite.model_dump())
    manifest = await workbench.manifest(suite, Variant(promptVersion=snapshot.get("promptVersion")), mode="experiment")
    manifest = manifest.model_copy(update={"runnerVersion": "online-failure-capture-v1"})
    run_id = str(uuid4())
    async with workbench.storage.cursor(transaction=True) as cur:
        await cur.execute("SELECT source_run_id FROM evaluation_online_failures WHERE request_id=%s AND owner_user_id=%s FOR UPDATE", (request.requestId, actor))
        current = await cur.fetchone()
        if current["source_run_id"]:
            run_id = current["source_run_id"]
        else:
            await cur.execute("INSERT INTO evaluation_runs(run_id,suite_id,suite_version,requested_by,execution_mode,status,manifest_json,result_json,created_at,finished_at) VALUES(%s,%s,1,%s,'experiment','FAILED',%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (run_id, suite.suiteId, actor, json.dumps({**manifest.model_dump(mode='json'), 'manifestFingerprint': manifest.fingerprint()}), json.dumps({'provenance': 'online-failure-capture', 'incomplete': True, 'reason': 'online_request_failure', 'sourceRequestId': request.requestId})))
            await cur.execute("INSERT INTO evaluation_cases(run_id,scenario_id,repetition,status,input_json,output_json,created_at,finished_at) VALUES(%s,%s,1,'FAILED',%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (run_id, template.id, json.dumps({'fixture': fixture}, ensure_ascii=False), json.dumps({'reason': 'online_request_failure', 'sourceRequestId': request.requestId, 'traceId': snapshot.get('traceId'), 'replayUsesIsolatedFixtures': True})))
            await cur.execute("UPDATE evaluation_online_failures SET source_run_id=%s WHERE request_id=%s", (run_id, request.requestId))
    report = await workbench.repository.get_report(run_id)
    return await workbench.capture(CaptureRequest(runId=run_id, scenarioId=report['cases'][0]['scenarioId'], title=request.title), actor)
