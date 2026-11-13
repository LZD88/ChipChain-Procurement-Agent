"""Authenticated prompt management; model origins and pass flags are server owned."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
import aiomysql
from agent.authorization import PROMPT_VIEW, PROMPT_MAINTAIN
from agent.mysql_storage import mysql_storage
from agent.evaluation.repository import EvaluationRepository
from agent.prompt_releases.bundles import bundle_diff, deployment_fingerprint
from agent.prompt_releases.repository import ReleaseConflict, enabled
from agent.prompt_releases.service import PromptReleaseService
from api_view.auth import require_same_origin_request, require_user

router = APIRouter(prefix="/prompt-releases", dependencies=[Depends(require_same_origin_request)])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionBody(StrictBody):
    roles: dict[str, str]
    note: str = Field(min_length=1, max_length=1000)


class PairBody(StrictBody):
    candidateVersion: str = Field(max_length=64)


class GateBody(StrictBody):
    stableRunId: str
    candidateRunId: str


class TransitionBody(StrictBody):
    expectedRevision: int = Field(ge=1)
    idempotencyKey: str = Field(min_length=1, max_length=128)
    gateId: str | None = None
    percentage: int = Field(default=10, ge=1, le=25)
    allowlist: list[str] = Field(default_factory=list, max_length=100)


def error(status, detail):
    return HTTPException(status, detail, headers={"Cache-Control": "no-store"})


async def authorize(actor, maintain=False):
    repo = PromptReleaseService(mysql_storage)
    allowed = await execute(mysql_storage.has_capability(actor, PROMPT_MAINTAIN))
    if not allowed and not maintain:
        allowed = await execute(mysql_storage.has_capability(actor, PROMPT_VIEW))
    if not allowed:
        await execute(repo.audit(actor, "AUTHORIZE_WRITE" if maintain else "AUTHORIZE_READ", "DENIED"))
        raise error(403, "无提示词发布权限，请开通 prompt:view 或 prompt:maintain")
    return repo


async def execute(operation):
    try:
        return await operation
    except ReleaseConflict as exc:
        raise error(409, str(exc)) from exc
    except ValueError as exc:
        raise error(422, str(exc)) from exc
    except (aiomysql.Error, RuntimeError) as exc:
        raise error(503, "提示词发布数据暂不可用，请稍后刷新") from exc


@router.get("/capabilities")
async def capabilities(response: Response, actor=Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    maintain = await execute(mysql_storage.has_capability(actor, PROMPT_MAINTAIN))
    return {"view": maintain or await execute(mysql_storage.has_capability(actor, PROMPT_VIEW)), "maintain": maintain, "enabled": enabled()}


@router.get("/state")
async def state(response: Response, actor=Depends(require_user)):
    repo = await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    return {**await execute(repo.state()), "enabled": enabled(), "deploymentFingerprint": deployment_fingerprint()}


@router.get("/versions")
async def versions(response: Response, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), actor=Depends(require_user)):
    repo = await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    return {"items": await execute(repo.list_versions(limit=limit, offset=offset))}


@router.get("/versions/{version}")
async def version_detail(version: str, response: Response, compareTo: str | None = None, actor=Depends(require_user)):
    repo = await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    snapshot = await execute(repo.get_version(version))
    return {**snapshot.public(), "diff": bundle_diff(await execute(repo.get_version(compareTo)), snapshot) if compareTo else None}


@router.post("/versions", status_code=201)
async def create_version(body: VersionBody, response: Response, actor=Depends(require_user)):
    repo = await authorize(actor, True)
    response.headers["Cache-Control"] = "no-store"
    return (await execute(repo.create_version(body.roles, actor, body.note))).public()


@router.post("/pairs", status_code=202)
async def pairs(body: PairBody, response: Response, actor=Depends(require_user)):
    repo = await authorize(actor, True)
    response.headers["Cache-Control"] = "no-store"
    return await execute(repo.pair(body.candidateVersion, actor))


@router.post("/gates")
async def gates(body: GateBody, response: Response, actor=Depends(require_user)):
    repo = await authorize(actor, True)
    response.headers["Cache-Control"] = "no-store"
    return await execute(repo.gate(body.stableRunId, body.candidateRunId, actor))


@router.get("/observations")
async def observations(response: Response, hours: int = Query(24, ge=1, le=720), actor=Depends(require_user)):
    repo = await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    return await execute(repo.observations(hours))


@router.get("/evaluations/{run_id}")
async def evaluation_progress(run_id: str, response: Response, actor=Depends(require_user)):
    await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    evaluations = EvaluationRepository(mysql_storage)
    report = await execute(evaluations.get_report(run_id))
    if not report or report["executionMode"] != "prompt-candidate":
        raise error(404, "未找到提示词测试任务")
    suite = await execute(evaluations.get_suite(report["suiteId"], report["suiteVersion"]))
    scenarios = (suite or {}).get("suite", {}).get("scenarios", [])
    return {"runId": run_id, "status": report["status"],
            "promptVersion": report["manifest"].get("promptVersion"),
            "total": sum(s.get("repetitions", 1) for s in scenarios) or None,
            "completed": len(report["cases"]),
            "passed": sum(c["status"] == "PASSED" for c in report["cases"]),
            "failed": sum(c["status"] == "FAILED" for c in report["cases"]),
            "reason": report.get("aggregate", {}).get("reason"),
            "startedAt": report.get("startedAt"), "finishedAt": report.get("finishedAt")}


@router.get("/history")
async def history(response: Response, actor=Depends(require_user)):
    repo = await authorize(actor)
    response.headers["Cache-Control"] = "no-store"
    return {kind: await execute(repo.history(kind)) for kind in ("events", "audit", "gates")}


@router.post("/{action}")
async def transition(action: str, body: TransitionBody, response: Response, actor=Depends(require_user)):
    repo = await authorize(actor, True)
    response.headers["Cache-Control"] = "no-store"
    if not enabled():
        raise error(409, "提示词发布功能尚未启用")
    try:
        return await execute(repo.transition(action, actor, expected_revision=body.expectedRevision,
            idempotency_key=body.idempotencyKey, gateId=body.gateId, percentage=body.percentage, allowlist=body.allowlist))
    except HTTPException:
        await repo.audit(actor, action.upper()[:40], "REJECTED", {"revision": body.expectedRevision})
        raise
