from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import aiomysql
from agent.prompt_releases.repository import ReleaseConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api_view.api import prompt_releases as api
from api_view.auth import require_user


@pytest.fixture
def client(monkeypatch):
    actor = {"name": "ordinary"}
    grants = {"ordinary": set(), "evaluator": {"evaluation:maintain"}, "viewer": {"prompt:view"}, "maintainer": {"prompt:maintain"}}
    async def has_capability(user, capability):
        return capability in grants[user]
    storage = SimpleNamespace(has_capability=has_capability)
    repo = SimpleNamespace(audit=AsyncMock(), state=AsyncMock(return_value={"stable":"builtin-v1","revision":1}),
        transition=AsyncMock(return_value={"revision":2}), list_versions=AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "mysql_storage", storage)
    monkeypatch.setattr(api, "PromptReleaseService", lambda _:repo)
    monkeypatch.setattr(api, "enabled", lambda:True)
    app = FastAPI(); app.include_router(api.router, prefix="/api")
    async def user():return actor["name"]
    app.dependency_overrides[require_user] = user
    return TestClient(app), actor, repo


def test_separate_permissions_csrf_and_server_policy(client):
    web, actor, repo = client
    for name in ("ordinary", "evaluator"):
        actor["name"] = name
        assert web.get('/api/prompt-releases/state').status_code == 403
    assert repo.audit.await_count == 2
    actor["name"] = "viewer"
    assert web.get('/api/prompt-releases/state').headers['cache-control'] == 'no-store'
    body = {"expectedRevision":1,"idempotencyKey":"test"}
    assert web.post('/api/prompt-releases/rollback',json=body).status_code == 403
    assert web.post('/api/prompt-releases/rollback',json=body,headers={"X-Requested-With":"DeepAgent"}).status_code == 403
    actor["name"] = "maintainer"
    assert web.post('/api/prompt-releases/rollback',json={**body,"passed":True},headers={"X-Requested-With":"DeepAgent"}).status_code == 422
    assert web.post('/api/prompt-releases/rollback',json=body,headers={"X-Requested-With":"DeepAgent"}).status_code == 200


def test_database_fault_and_revision_conflict_are_explicit(client):
    web, actor, repo = client
    actor["name"] = "maintainer"
    repo.list_versions.side_effect = aiomysql.OperationalError(2003, "offline")
    response = web.get('/api/prompt-releases/versions')
    assert response.status_code == 503
    assert response.headers['cache-control'] == 'no-store'
    repo.transition.side_effect = ReleaseConflict("发布状态已更新")
    response = web.post('/api/prompt-releases/rollback', json={"expectedRevision": 1, "idempotencyKey": "conflict"}, headers={"X-Requested-With": "DeepAgent"})
    assert response.status_code == 409
    assert repo.audit.await_args.args[2] == "REJECTED"


def test_provider_and_pass_flag_cannot_be_supplied(client):
    web, actor, _ = client
    actor["name"] = "maintainer"
    for extra in ({"baseUrl": "http://external"}, {"passed": True}, {"modelId": "arbitrary"}):
        assert web.post('/api/prompt-releases/pairs', json={"candidateVersion": "v2", **extra}, headers={"X-Requested-With": "DeepAgent"}).status_code == 422


def test_prompt_viewer_can_read_progress_without_evaluation_permission(client, monkeypatch):
    web, actor, _ = client
    report = {"executionMode": "prompt-candidate", "status": "RUNNING", "suiteId": "fixed",
              "suiteVersion": 1, "manifest": {"promptVersion": "v2"},
              "cases": [{"status": "PASSED", "result": {"output": "must not leak raw output"}}, {"status": "FAILED"}],
              "aggregate": {}}
    evaluations = SimpleNamespace(get_report=AsyncMock(return_value=report),
        get_suite=AsyncMock(return_value={"suite": {"scenarios": [{"repetitions": 3}, {"repetitions": 3}]}}))
    monkeypatch.setattr(api, "EvaluationRepository", lambda _: evaluations)
    for name in ("ordinary", "evaluator"):
        actor["name"] = name
        assert web.get('/api/prompt-releases/evaluations/run').status_code == 403
    evaluations.get_report.assert_not_awaited()
    actor["name"] = "viewer"
    response = web.get('/api/prompt-releases/evaluations/run')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {"runId": "run", "status": "RUNNING", "promptVersion": "v2",
        "total": 6, "completed": 2, "passed": 1, "failed": 1, "reason": None,
        "startedAt": None, "finishedAt": None}
    assert 'must not leak' not in response.text
    report["executionMode"] = "workflow-safety"
    assert web.get('/api/prompt-releases/evaluations/run').status_code == 404
    evaluations.get_report.return_value = None
    assert web.get('/api/prompt-releases/evaluations/run').status_code == 404


def test_progress_database_failure_is_retryable(client, monkeypatch):
    web, actor, _ = client
    actor["name"] = "viewer"
    evaluations = SimpleNamespace(get_report=AsyncMock(side_effect=aiomysql.OperationalError(2003, "offline")))
    monkeypatch.setattr(api, "EvaluationRepository", lambda _: evaluations)
    response = web.get('/api/prompt-releases/evaluations/run')
    assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
