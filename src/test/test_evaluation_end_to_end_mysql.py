"""End-to-end baseline execution, persistence, comparison, and export verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiomysql
import pytest

from agent.evaluation.comparison import compare_runs, evaluate_regression_gates
from agent.evaluation.manifest import EvaluationRunManifest
from agent.evaluation.reporting import portable_report, render_markdown
from agent.evaluation.repository import EvaluationRepository
from agent.evaluation.rule_runner import load_default_inputs
from agent.evaluation.schemas import publish_suite
from agent.evaluation.worker import execute_run
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from agent.policies.canonical import canonical_json


def _digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _manifest(suite, fixtures, *, mode: str, repetitions: int):
    workflow = mode == "workflow-safety"
    return EvaluationRunManifest(
        suiteId=suite.suiteId,
        suiteVersion=1,
        suiteFingerprint=suite.content_fingerprint(),
        fixtureVersion=suite.fixtureVersion,
        fixtureFingerprint=_digest(fixtures),
        policyVersion="supplier-score-v1",
        policyFingerprint=_digest({"policyVersion": "supplier-score-v1"}),
        codeRevision="verification-working-tree",
        promptFingerprint=_digest({"prompt": "default-evaluation-v1"}),
        skillFingerprint=_digest({"skill": "procurement-evaluation-v1"}),
        modelId="fixture-workflow-simulator-v1" if workflow else None,
        providerId="local-non-writing-simulator" if workflow else None,
        modelConfigFingerprint=(
            _digest({"network": "disabled", "writes": "simulated"}) if workflow else None
        ),
        runnerVersion="workflow-safety-v1" if workflow else "rule-contract-v1",
        environment="isolated-test",
        executionMode=mode,
        startedAt=datetime.now(timezone.utc),
        repetitionCount=repetitions,
    )


def _metric(report, name):
    return next(metric for metric in report["metrics"] if metric["name"] == name)


@pytest.mark.asyncio
async def test_default_runs_reports_comparison_and_quality_payload_agree_end_to_end():
    base = MySQLSettings.from_env()
    database = "agent_eval_e2e_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_eval_e2e_[0-9a-f]{12}", database)
    admin = await aiomysql.connect(**base.connection_options())
    storage = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        pool = await aiomysql.create_pool(
            **base.connection_options(), db=database, minsize=1, maxsize=4,
        )
        storage = MySQLStorage(base)
        storage.pool = pool
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute(
                    "INSERT INTO app_users(username,password_hash,created_at) VALUES "
                    "('evaluation-system','test',UTC_TIMESTAMP(6)),"
                    "('evaluation-runner','test',UTC_TIMESTAMP(6))"
                )

        repository = EvaluationRepository(storage)
        suite, fixtures = load_default_inputs()
        await repository.publish(publish_suite(suite, version=1))

        async def run(run_id, mode, repetitions):
            await repository.create_run(
                run_id=run_id,
                suite_id=suite.suiteId,
                suite_version=1,
                requested_by="evaluation-runner",
                execution_mode=mode,
                manifest=_manifest(suite, fixtures, mode=mode, repetitions=repetitions),
            )
            assert await repository.claim_next_run(worker_id="e2e-worker") == run_id
            assert await execute_run(repository, run_id, environment={}) is True
            report = await repository.get_report(run_id)
            assert report["status"] == "COMPLETED"
            assert report["incomplete"] is False
            return report

        baseline = await run(
            "10000000-0000-0000-0000-000000000001", "rule-contract", 1,
        )
        candidate = await run(
            "10000000-0000-0000-0000-000000000002", "rule-contract", 1,
        )
        workflow = await run(
            "10000000-0000-0000-0000-000000000003", "workflow-safety", 3,
        )

        assert len(baseline["cases"]) == len(candidate["cases"]) == 6
        assert [case["result"] for case in baseline["cases"]] == [
            case["result"] for case in candidate["cases"]
        ]
        assert baseline["aggregate"]["reportFingerprint"] == candidate["aggregate"]["reportFingerprint"]
        assert len(workflow["cases"]) == 12
        assert {case["repetition"] for case in workflow["cases"]} == {1, 2, 3}
        assert workflow["aggregate"]["productionMutations"] == 0
        assert len(workflow["aggregate"]["intendedCalls"]) == 3

        assert (
            _metric(baseline, "hard_constraint_pass_rate")["numerator"],
            _metric(baseline, "hard_constraint_pass_rate")["denominator"],
        ) == (10, 10)
        assert (
            _metric(baseline, "expected_recommendation_agreement")["numerator"],
            _metric(baseline, "expected_recommendation_agreement")["denominator"],
        ) == (3, 3)
        assert (
            _metric(baseline, "scenario_completion_rate")["numerator"],
            _metric(baseline, "scenario_completion_rate")["denominator"],
        ) == (6, 6)
        assert (
            _metric(workflow, "unsafe_write_prevention")["numerator"],
            _metric(workflow, "unsafe_write_prevention")["denominator"],
        ) == (12, 12)
        assert (
            _metric(workflow, "approval_boundary_compliance")["numerator"],
            _metric(workflow, "approval_boundary_compliance")["denominator"],
        ) == (6, 6)
        assert (
            _metric(workflow, "scenario_completion_rate")["numerator"],
            _metric(workflow, "scenario_completion_rate")["denominator"],
        ) == (12, 12)

        await repository.set_baseline(
            suite_id=suite.suiteId,
            execution_mode="rule-contract",
            run_id=baseline["runId"],
            selected_by="evaluation-runner",
        )
        selected = await repository.get_baseline(
            suite_id=suite.suiteId, execution_mode="rule-contract",
        )
        assert selected["run_id"] == baseline["runId"]
        comparison = compare_runs(baseline, candidate)
        gates = evaluate_regression_gates(comparison, {
            "hard_constraint_pass_rate": 1.0,
            "expected_recommendation_agreement": 1.0,
        })
        assert gates == {
            "passed": True,
            "failures": [],
            "blockingSafetyRegression": False,
        }
        assert all(item["delta"] in (0, None) for item in comparison["metricDeltas"])
        assert not any(item["assertionRegression"] for item in comparison["scenarioDiffs"])

        for report in (baseline, candidate, workflow):
            exported = portable_report(report)
            markdown = render_markdown(report)
            assert exported["completionLabel"] == "COMPLETE"
            assert exported["exportFingerprint"] in markdown
            assert f"Evaluation run {report['runId']}" in markdown
            assert len(exported["cases"]) == len(report["cases"])
            # These are the exact persisted fields consumed by the quality view.
            assert exported["suiteId"] == suite.suiteId
            assert exported["suiteVersion"] == 1
            assert exported["manifest"]["environment"] == "isolated-test"
            assert all("denominator" in metric for metric in exported["metrics"])

        output_dir = os.getenv("EVALUATION_E2E_OUTPUT_DIR")
        if output_dir:
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            named_reports = {
                "rule-baseline": baseline,
                "rule-candidate": candidate,
                "workflow-safety": workflow,
            }
            for name, report in named_reports.items():
                exported = portable_report(report)
                (destination / f"{name}.json").write_text(
                    json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                (destination / f"{name}.md").write_text(
                    render_markdown(report), encoding="utf-8",
                )
            (destination / "rule-comparison.json").write_text(
                json.dumps(
                    {"comparison": comparison, "gates": gates},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        if storage is not None:
            await storage.close()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        await admin.ensure_closed()
