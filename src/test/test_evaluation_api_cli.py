import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from agent.authorization import EVALUATION_MAINTAIN, EVALUATION_VIEW
from agent.evaluation import cli
from agent.evaluation.reporting import portable_report
from api_view.api import evaluations
from api_view.auth import require_user
from test_evaluation_manifest import manifest


NOW = datetime(2026, 1, 1)


def _report(run_id="run-completed", status="COMPLETED", recommendation=1):
    return {
        "runId": run_id,
        "suiteId": "suite",
        "suiteVersion": 1,
        "executionMode": "rule-contract",
        "status": status,
        "incomplete": status != "COMPLETED",
        "manifest": {
            "suiteId": "suite", "suiteVersion": 1, "fixtureVersion": "fixtures-v1",
            "fixtureFingerprint": "b" * 64, "executionMode": "rule-contract",
        },
        "metrics": [{
            "name": "unsafe_write_prevention", "value": 1.0, "numerator": 1,
            "denominator": 1, "excluded": 0, "unit": "ratio",
        }],
        "cases": [{
            "scenarioId": "success", "repetition": 1, "status": "PASSED",
            "result": {"recommendedPartId": recommendation}, "assertions": [],
        }],
    }


class FakeRepository:
    def __init__(self):
        self.audit = []
        self.suite = {
            "suite_id": "suite", "version": 1, "owner_user_id": "maintainer",
            "title": "Suite", "content_fingerprint": "a" * 64, "published_at": NOW,
            "suite": {"suiteId": "suite"},
        }
        self.runs = {
            "run-completed": {
                "run_id": "run-completed", "suite_id": "suite", "suite_version": 1,
                "requested_by": "maintainer", "execution_mode": "rule-contract",
                "status": "COMPLETED", "created_at": NOW, "started_at": NOW,
                "finished_at": NOW, "cancel_requested_at": None,
            },
            "run-cancelled": {
                "run_id": "run-cancelled", "suite_id": "suite", "suite_version": 1,
                "requested_by": "maintainer", "execution_mode": "rule-contract",
                "status": "CANCELLED", "created_at": NOW, "started_at": NOW,
                "finished_at": NOW, "cancel_requested_at": NOW,
            },
            "run-queued": {
                "run_id": "run-queued", "suite_id": "suite", "suite_version": 1,
                "requested_by": "maintainer", "execution_mode": "rule-contract",
                "status": "QUEUED", "created_at": NOW, "started_at": None,
                "finished_at": None, "cancel_requested_at": None,
            },
        }
        self.reports = {
            "run-completed": _report(),
            "run-cancelled": _report("run-cancelled", "CANCELLED"),
            "run-queued": _report("run-queued", "QUEUED"),
        }
        self.baseline = {
            "suite_id": "suite", "execution_mode": "rule-contract",
            "run_id": "run-completed", "selected_by": "maintainer", "selected_at": NOW,
        }

    async def record_audit(self, **kwargs):
        self.audit.append(kwargs)

    async def list_suites(self, **_kwargs):
        return [self.suite]

    async def get_suite(self, suite_id, version):
        return self.suite if (suite_id, version) == ("suite", 1) else None

    async def publish(self, published):
        self.published = published

    async def list_runs(self, **_kwargs):
        return list(self.runs.values())

    async def create_run(self, **kwargs):
        self.created = kwargs

    async def get_run(self, run_id):
        return self.runs.get(run_id)

    async def get_report(self, run_id):
        return self.reports.get(run_id)

    async def request_cancel(self, run_id):
        return run_id == "run-queued"

    async def set_baseline(self, **kwargs):
        self.set_baseline_args = kwargs

    async def get_baseline(self, **_kwargs):
        return self.baseline


@pytest.fixture
def api(monkeypatch):
    repository = FakeRepository()
    actor = {"name": "viewer"}
    grants = {
        "viewer": {EVALUATION_VIEW},
        "maintainer": {EVALUATION_MAINTAIN},
        "ungranted": set(),
    }

    async def current_actor():
        return actor["name"]

    async def has_capability(username, capability):
        return capability in grants.get(username, set())

    monkeypatch.setattr(evaluations, "EvaluationRepository", lambda _storage: repository)
    monkeypatch.setattr(evaluations.mysql_storage, "has_capability", has_capability)
    app = FastAPI()
    app.include_router(evaluations.router, prefix="/api")
    app.dependency_overrides[require_user] = current_actor
    return TestClient(app), repository, actor


def test_viewer_reads_no_store_but_cannot_start_and_denial_is_audited(api):
    client, repository, actor = api
    listed = client.get("/api/evaluations/runs")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["items"][1]["incomplete"] is True

    denied = client.post(
        "/api/evaluations/runs", json={"manifest": jsonable_encoder(manifest(suiteId="suite"))},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "无评测权限"}
    assert "suite" not in denied.text
    assert repository.audit[-1]["action"] == "RUN_CREATE"
    assert repository.audit[-1]["outcome"] == "DENIED"

    actor["name"] = "ungranted"
    read_denied = client.get("/api/evaluations/runs")
    assert read_denied.status_code == 403
    assert read_denied.json() == {"detail": "无评测权限"}
    assert "run-completed" not in read_denied.text
    assert repository.audit[-1]["action"] == "RUN_LIST"


def test_maintainer_queues_cancels_and_audits_mutations(api):
    client, repository, actor = api
    actor["name"] = "maintainer"
    queued = client.post(
        "/api/evaluations/runs",
        json={"manifest": jsonable_encoder(manifest(suiteId="suite"))},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "QUEUED"
    assert repository.created["requested_by"] == "maintainer"
    assert repository.audit[-1]["action"] == "RUN_CREATE"

    cancelled = client.post(
        "/api/evaluations/runs/run-queued/cancel",
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["cancelRequested"] is True
    assert repository.audit[-1]["action"] == "RUN_CANCEL"


def test_json_and_markdown_exports_agree_and_incomplete_is_explicit(api):
    client, repository, _actor = api
    json_export = client.get("/api/evaluations/runs/run-completed/export")
    markdown_export = client.get(
        "/api/evaluations/runs/run-completed/export", params={"format": "markdown"},
    )
    body = json_export.json()
    assert body["runId"] == "run-completed"
    assert body["status"] == "COMPLETED"
    assert body["completionLabel"] == "COMPLETE"
    assert "Evaluation run run-completed" in markdown_export.text
    assert "Status: **COMPLETED**" in markdown_export.text
    assert body["exportFingerprint"] in markdown_export.text
    assert [entry["action"] for entry in repository.audit[-2:]] == ["RUN_EXPORT", "RUN_EXPORT"]

    incomplete = client.get("/api/evaluations/runs/run-cancelled/export").json()
    assert incomplete["status"] == "CANCELLED"
    assert incomplete["incomplete"] is True
    assert incomplete["completionLabel"] == "INCOMPLETE"


def test_comparison_uses_baseline_and_returns_gate_result(api):
    client, repository, _actor = api
    repository.reports["candidate"] = _report("candidate", recommendation=2)
    response = client.post(
        "/api/evaluations/comparisons",
        json={"candidateRunId": "candidate", "thresholds": {"unsafe_write_prevention": 1}},
        headers={"X-Requested-With": "DeepAgent"},
    )
    assert response.status_code == 200
    assert response.json()["baselineRunId"] == "run-completed"
    assert response.json()["comparison"]["scenarioDiffs"][0]["recommendation"]["changed"] is True
    assert response.json()["gates"]["passed"] is True
    read_only = client.get(
        "/api/evaluations/comparisons", params={"candidateRunId": "candidate"},
    )
    assert read_only.status_code == 200
    assert read_only.headers["cache-control"] == "no-store"
    assert read_only.json()["candidateRunId"] == "candidate"


def test_read_only_comparison_preserves_repeats_and_blocks_early_regression(api):
    client, repository, _actor = api
    baseline = repository.reports["run-completed"]
    candidate = _report("candidate")
    for run in (baseline, candidate):
        run["executionMode"] = run["manifest"]["executionMode"] = "workflow-safety"
        run["cases"] = [
            {"scenarioId": "success", "repetition": repetition, "status": "PASSED",
             "result": {"recommendedPartId": 1}}
            for repetition in (1, 2, 3)
        ]
    candidate["cases"][0]["status"] = "FAILED"
    repository.reports["candidate"] = candidate
    response = client.get("/api/evaluations/comparisons", params={"candidateRunId": "candidate"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert len(payload["comparison"]["scenarioDiffs"]) == 3
    assert payload["comparison"]["scenarioSummaries"][0]["after"]["passRate"] == pytest.approx(2 / 3)
    assert payload["gates"]["passed"] is False
    assert payload["gates"]["failures"] == [{"scenarioId": "success", "repetition": 1, "priority": "ASSERTION"}]


def test_comparison_rejects_duplicate_case_identity_with_422(api):
    client, repository, _actor = api
    candidate = _report("candidate")
    candidate["cases"].append(dict(candidate["cases"][0]))
    repository.reports["candidate"] = candidate
    response = client.get("/api/evaluations/comparisons", params={"candidateRunId": "candidate"})
    assert response.status_code == 422
    assert "duplicate evaluation case" in response.text


def test_cli_returns_nonzero_on_gate_failure(tmp_path):
    baseline = portable_report(_report())
    candidate = portable_report(_report("candidate"))
    candidate["metrics"][0]["value"] = 0.5
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "comparison.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    exit_code = cli.main([
        "compare", str(baseline_path), str(candidate_path),
        "--threshold", "unsafe_write_prevention=1", "--output", str(output_path),
    ])
    assert exit_code == 2
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["gates"]["passed"] is False
    assert result["gates"]["blockingSafetyRegression"] is True
