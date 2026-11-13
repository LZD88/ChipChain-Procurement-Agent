"""Authenticated read-only integration health; credentials are never returned."""
from fastapi import APIRouter, Depends, HTTPException, Response
from agent.mysql_storage import mysql_storage
from agent.platform.runtime import runtime_configuration
from agent.platform.telemetry import langfuse_integration
from api_view.auth import require_user
from api_view.integrations import platform_integrations

router = APIRouter(prefix="/integrations")


@router.get("/status")
async def status(response: Response, actor: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    allowed = False
    for capability in ("prompt:view", "prompt:maintain", "evaluation:view", "evaluation:maintain"):
        if await mysql_storage.has_capability(actor, capability):
            allowed = True
            break
    if not allowed:
        raise HTTPException(403, "没有配置与观测查看权限", headers={"Cache-Control": "no-store"})
    return {"nacos": runtime_configuration.public(), "langfuse": langfuse_integration.public(),
            "promptAuthority": "project-mysql", "promptMirror": platform_integrations.mirror_status,
            "mirroredReleaseRevision": platform_integrations.mirrored}
