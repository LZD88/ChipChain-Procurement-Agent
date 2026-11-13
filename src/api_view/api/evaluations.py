"""Authenticated evaluation suite, run, comparison, and export APIs."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from agent.authorization import require_evaluation_maintain, require_evaluation_view
from agent.evaluation.comparison import (
    IncompatibleRunsError,
    compare_runs,
    evaluate_regression_gates,
)
from agent.evaluation.manifest import EvaluationRunManifest
from agent.evaluation.reporting import portable_report, render_markdown
from agent.evaluation.repository import EvaluationConflictError, EvaluationRepository
from agent.evaluation.schemas import EvaluationSuite, publish_suite
from agent.mysql_storage import mysql_storage
from api_view.auth import require_same_origin_request, require_user
from agent.evaluation.workbench import Workbench, TaskRequest, ExperimentRequest, CaptureRequest, Variant, experiment_summary
from agent.evaluation.online_failures import OnlineCaptureRequest, list_online_failures, capture_online_failure


router = APIRouter(
    prefix="/evaluations",
    dependencies=[Depends(require_same_origin_request)],
)


class PublishSuiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    suite: EvaluationSuite


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: EvaluationRunManifest


class BaselineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    suiteId: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    executionMode: Literal["rule-contract", "workflow-safety", "experiment"]


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateRunId: str
    baselineRunId: str | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


async def _authorize(repository: EvaluationRepository, *, actor: str, maintain: bool,
                     action: str, resource_type: str, resource_id: str | None = None) -> None:
    allowed = (
        await require_evaluation_maintain(mysql_storage, actor)
        if maintain else await require_evaluation_view(mysql_storage, actor)
    )
    if allowed:
        return
    await repository.record_audit(
        actor_user_id=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="DENIED",
        metadata={"requiredCapability": "evaluation:maintain" if maintain else "evaluation:view"},
    )
    raise _error(403, "无评测权限")


def _suite_summary(row: dict) -> dict:
    return {
        "suiteId": row["suite_id"],
        "version": row["version"],
        "owner": row["owner_user_id"],
        "title": row["title"],
        "contentFingerprint": row["content_fingerprint"],
        "publishedAt": row["published_at"],
    }


def _run_summary(row: dict) -> dict:
    import json
    manifest = row.get("manifest_json") or {}
    manifest = json.loads(manifest) if isinstance(manifest, str) else manifest
    status = row["status"]
    return {
        "runId": row["run_id"],
        "suiteId": row["suite_id"],
        "suiteVersion": row["suite_version"],
        "requestedBy": row["requested_by"],
        "executionMode": row["execution_mode"],
        "status": status,
        "incomplete": status != "COMPLETED",
        "createdAt": row.get("created_at"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "cancelRequestedAt": row.get("cancel_requested_at"),
        "variant": {key: manifest.get(key) for key in ("modelId", "promptVersion", "skillVariant", "codeRevision", "experimentId", "experimentArm")},
    }


@router.get("/suites")
async def list_suites(
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="SUITE_LIST",
                     resource_type="EVALUATION_SUITE")
    rows = await repository.list_suites(page=page, size=size)
    _no_store(response)
    return {"items": [_suite_summary(row) for row in rows], "page": page, "size": size}


@router.get("/suites/{suite_id}/versions/{version}")
async def suite_detail(
    suite_id: str,
    version: int,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="SUITE_READ",
                     resource_type="EVALUATION_SUITE", resource_id=suite_id)
    row = await repository.get_suite(suite_id, version)
    if row is None:
        raise _error(404, "评测套件不存在")
    _no_store(response)
    return {"suite": row["suite"], **_suite_summary(row)}


@router.post("/suites/publish", status_code=201)
async def publish_evaluation_suite(
    body: PublishSuiteRequest,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=True, action="SUITE_PUBLISH",
                     resource_type="EVALUATION_SUITE", resource_id=body.suite.suiteId)
    published = publish_suite(body.suite, version=body.version)
    try:
        await repository.publish(published)
    except EvaluationConflictError as exc:
        await repository.record_audit(
            actor_user_id=actor, action="SUITE_PUBLISH", resource_type="EVALUATION_SUITE",
            resource_id=body.suite.suiteId, outcome="CONFLICT",
            metadata={"version": body.version},
        )
        raise _error(409, str(exc)) from exc
    await repository.record_audit(
        actor_user_id=actor, action="SUITE_PUBLISH", resource_type="EVALUATION_SUITE",
        resource_id=body.suite.suiteId, outcome="SUCCESS",
        metadata={"version": body.version, "contentFingerprint": published.contentFingerprint},
    )
    _no_store(response)
    return {"suiteId": body.suite.suiteId, "version": body.version,
            "contentFingerprint": published.contentFingerprint}


@router.get("/runs")
async def list_runs(
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    suiteId: str | None = Query(None),
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="RUN_LIST",
                     resource_type="EVALUATION_RUN")
    rows = await repository.list_runs(page=page, size=size, suite_id=suiteId)
    _no_store(response)
    return {"items": [_run_summary(row) for row in rows], "page": page, "size": size}


@router.post("/runs", status_code=202)
async def create_run(
    body: CreateRunRequest,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    manifest = body.manifest
    if manifest.executionMode in {"prompt-candidate", "experiment"}:
        raise _error(422, "提示词评测请使用提示词发布页创建服务端配对任务")
    await _authorize(repository, actor=actor, maintain=True, action="RUN_CREATE",
                     resource_type="EVALUATION_RUN", resource_id=manifest.suiteId)
    suite = await repository.get_suite(manifest.suiteId, manifest.suiteVersion)
    if suite is None:
        raise _error(422, "评测套件版本不存在")
    if suite["content_fingerprint"] != manifest.suiteFingerprint:
        raise _error(422, "运行清单与已发布套件指纹不一致")
    run_id = str(uuid.uuid4())
    await repository.create_run(
        run_id=run_id,
        suite_id=manifest.suiteId,
        suite_version=manifest.suiteVersion,
        requested_by=actor,
        execution_mode=manifest.executionMode,
        manifest=manifest,
    )
    await repository.record_audit(
        actor_user_id=actor, action="RUN_CREATE", resource_type="EVALUATION_RUN",
        resource_id=run_id, outcome="SUCCESS",
        metadata={"suiteId": manifest.suiteId, "suiteVersion": manifest.suiteVersion,
                  "executionMode": manifest.executionMode},
    )
    _no_store(response)
    return {"runId": run_id, "status": "QUEUED", "incomplete": True}


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: str,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="RUN_READ",
                     resource_type="EVALUATION_RUN", resource_id=run_id)
    report = await repository.get_report(run_id)
    if report is None:
        raise _error(404, "评测运行不存在")
    _no_store(response)
    return portable_report(report)


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: str,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=True, action="RUN_CANCEL",
                     resource_type="EVALUATION_RUN", resource_id=run_id)
    run = await repository.get_run(run_id)
    if run is None:
        raise _error(404, "评测运行不存在")
    if not await repository.request_cancel(run_id):
        raise _error(409, "评测运行已结束或取消已请求")
    await repository.record_audit(
        actor_user_id=actor, action="RUN_CANCEL", resource_type="EVALUATION_RUN",
        resource_id=run_id, outcome="SUCCESS", metadata={"previousStatus": run["status"]},
    )
    _no_store(response)
    return {"runId": run_id, "status": run["status"], "cancelRequested": True,
            "incomplete": True}


@router.put("/baselines")
async def set_baseline(
    body: BaselineRequest,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=True, action="BASELINE_SET",
                     resource_type="EVALUATION_SUITE", resource_id=body.suiteId)
    try:
        await repository.set_baseline(
            suite_id=body.suiteId,
            execution_mode=body.executionMode,
            run_id=body.runId,
            selected_by=actor,
        )
    except ValueError as exc:
        raise _error(422, str(exc)) from exc
    _no_store(response)
    return {"suiteId": body.suiteId, "executionMode": body.executionMode,
            "runId": body.runId}


@router.get("/baselines/{suite_id}/{execution_mode}")
async def get_baseline(
    suite_id: str,
    execution_mode: Literal["rule-contract", "workflow-safety", "experiment"],
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="BASELINE_READ",
                     resource_type="EVALUATION_SUITE", resource_id=suite_id)
    row = await repository.get_baseline(suite_id=suite_id, execution_mode=execution_mode)
    if row is None:
        raise _error(404, "评测基线不存在")
    _no_store(response)
    return {"suiteId": row["suite_id"], "executionMode": row["execution_mode"],
            "runId": row["run_id"], "selectedBy": row["selected_by"],
            "selectedAt": row["selected_at"]}


@router.post("/comparisons")
async def compare_evaluation_runs(
    body: ComparisonRequest,
    response: Response,
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="RUN_COMPARE",
                     resource_type="EVALUATION_RUN", resource_id=body.candidateRunId)
    candidate = await repository.get_report(body.candidateRunId)
    if candidate is None:
        raise _error(404, "候选评测运行不存在")
    baseline_id = body.baselineRunId
    if baseline_id is None:
        baseline_row = await repository.get_baseline(
            suite_id=candidate["suiteId"], execution_mode=candidate["executionMode"],
        )
        if baseline_row is None:
            raise _error(404, "兼容评测基线不存在")
        baseline_id = baseline_row["run_id"]
    baseline = await repository.get_report(baseline_id)
    if baseline is None:
        raise _error(404, "基线评测运行不存在")
    if baseline["incomplete"] or candidate["incomplete"]:
        raise _error(409, "未完成运行不能用于聚合比较")
    try:
        comparison = compare_runs(baseline, candidate)
    except IncompatibleRunsError as exc:
        raise _error(422, str(exc)) from exc
    gates = evaluate_regression_gates(comparison, body.thresholds)
    _no_store(response)
    return {"baselineRunId": baseline_id, "candidateRunId": body.candidateRunId,
            "comparison": comparison, "gates": gates}


@router.get("/comparisons")
async def compare_evaluation_runs_read_only(
    response: Response,
    candidateRunId: str = Query(...),
    baselineRunId: str | None = Query(None),
    actor: str = Depends(require_user),
):
    """Read-only comparison for the observability same-origin proxy."""
    return await compare_evaluation_runs(
        ComparisonRequest(candidateRunId=candidateRunId, baselineRunId=baselineRunId),
        response,
        actor,
    )


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: str,
    format: Literal["json", "markdown"] = Query("json"),
    actor: str = Depends(require_user),
):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=False, action="RUN_EXPORT",
                     resource_type="EVALUATION_RUN", resource_id=run_id)
    report = await repository.get_report(run_id)
    if report is None:
        raise _error(404, "评测运行不存在")
    body = portable_report(report)
    await repository.record_audit(
        actor_user_id=actor, action="RUN_EXPORT", resource_type="EVALUATION_RUN",
        resource_id=run_id, outcome="SUCCESS",
        metadata={"format": format, "exportFingerprint": body["exportFingerprint"],
                  "incomplete": body["incomplete"]},
    )
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if format == "markdown":
        headers["Content-Disposition"] = f'attachment; filename="evaluation-{run_id}.md"'
        return PlainTextResponse(render_markdown(report), media_type="text/markdown", headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="evaluation-{run_id}.json"'
    return JSONResponse(content=body, headers=headers)


async def _workbench_access(actor, response, *, maintain=False, action="WORKBENCH_READ"):
    repository = EvaluationRepository(mysql_storage)
    await _authorize(repository, actor=actor, maintain=maintain, action=action, resource_type="EVALUATION_WORKBENCH")
    _no_store(response)
    return Workbench(mysql_storage)


async def _workbench_action(awaitable):
    try:
        return await awaitable
    except (ValueError, EvaluationConflictError) as exc:
        raise _error(422, str(exc)) from exc


@router.get("/catalog")
async def workbench_catalog(response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response)
    return {**await workbench.catalog(), "canMaintain": await require_evaluation_maintain(mysql_storage, actor)}


@router.post("/tasks", status_code=202)
async def create_task(body: TaskRequest, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response, maintain=True, action="TASK_CREATE")
    return await _workbench_action(workbench.create_task(body, actor))


@router.get("/online-failures")
async def online_failure_list(response: Response, actor: str = Depends(require_user)):
    await _workbench_access(actor, response)
    return {"items": await list_online_failures(mysql_storage, actor)}


@router.post("/online-failures/capture", status_code=201)
async def online_failure_capture(body: OnlineCaptureRequest, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response, maintain=True, action="ONLINE_FAILURE_CAPTURE")
    return await _workbench_action(capture_online_failure(workbench, body, actor))


@router.post("/experiments", status_code=202)
async def create_experiment(body: ExperimentRequest, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response, maintain=True, action="EXPERIMENT_CREATE")
    return await _workbench_action(workbench.experiment(body, actor))


@router.get("/experiments")
async def list_experiments(response: Response, page: int = Query(1, ge=1), actor: str = Depends(require_user)):
    await _workbench_access(actor, response)
    async with mysql_storage.cursor() as cur:
        await cur.execute("SELECT JSON_UNQUOTE(JSON_EXTRACT(manifest_json,'$.experimentId')) AS experiment_id,MIN(created_at) AS created_at FROM evaluation_runs WHERE execution_mode='experiment' AND JSON_EXTRACT(manifest_json,'$.experimentId') IS NOT NULL AND JSON_UNQUOTE(JSON_EXTRACT(manifest_json,'$.experimentId'))<>'null' GROUP BY experiment_id ORDER BY created_at DESC LIMIT 20 OFFSET %s", ((page - 1) * 20,))
        rows = list(await cur.fetchall())
    return {"items": rows, "page": page}


@router.get("/experiments/{experiment_id}")
async def experiment_detail(experiment_id: str, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response)
    async with mysql_storage.cursor() as cur:
        await cur.execute("SELECT run_id FROM evaluation_runs WHERE JSON_UNQUOTE(JSON_EXTRACT(manifest_json,'$.experimentId'))=%s AND execution_mode='experiment'", (experiment_id,))
        rows = list(await cur.fetchall())
    if len(rows) != 2:
        raise _error(404, "实验不存在")
    reports = [await workbench.repository.get_report(row["run_id"]) for row in rows]
    arms = {r["manifest"]["experimentArm"]: r for r in reports}
    comparison = None
    if all(r["status"] == "COMPLETED" for r in reports):
        comparison = compare_runs(arms["baseline"], arms["candidate"])
    return {"experimentId": experiment_id, "arms": {k: experiment_summary(v) for k, v in arms.items()}, "comparison": comparison}


@router.post("/failures", status_code=201)
async def capture_failure(body: CaptureRequest, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response, maintain=True, action="FAILURE_CAPTURE")
    return await _workbench_action(workbench.capture(body, actor))


@router.get("/failures")
async def list_failures(response: Response, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response)
    return {"items": await workbench.failures(page=page, size=size), "page": page, "size": size}


@router.get("/failures/{failure_id}")
async def failure_detail(failure_id: str, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response)
    row, snapshot = await _workbench_action(workbench.failure(failure_id))
    async with mysql_storage.cursor() as cur:
        await cur.execute("SELECT run_id,created_at FROM evaluation_failure_replays WHERE failure_id=%s ORDER BY replay_id DESC LIMIT 100", (failure_id,))
        history = list(await cur.fetchall())
    return {"failureId": failure_id, "title": row["title"], "snapshot": snapshot, "replays": history}


@router.post("/failures/{failure_id}/replay", status_code=202)
async def replay_failure(failure_id: str, body: Variant, response: Response, actor: str = Depends(require_user)):
    workbench = await _workbench_access(actor, response, maintain=True, action="FAILURE_REPLAY")
    return await _workbench_action(workbench.replay(failure_id, body, actor))
