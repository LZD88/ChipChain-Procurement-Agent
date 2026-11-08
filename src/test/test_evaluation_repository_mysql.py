import json
import re
from uuid import uuid4

import aiomysql
import pytest

from agent.evaluation.repository import EvaluationConflictError, EvaluationRepository
from agent.evaluation.schemas import EvaluationSuite, publish_suite
from agent.mysql_storage import APPLICATION_DDL, MySQLSettings, MySQLStorage
from test_evaluation_schema import payload
from test_evaluation_manifest import manifest


@pytest.mark.asyncio
async def test_evaluation_migration_and_repository_lifecycle():
    base = MySQLSettings.from_env()
    database = "agent_eval_test_" + uuid4().hex[:12]
    assert re.fullmatch(r"agent_eval_test_[0-9a-f]{12}", database)
    admin = await aiomysql.connect(**base.connection_options())
    pool = None
    try:
        async with admin.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        pool = await aiomysql.create_pool(**base.connection_options(), db=database, minsize=1, maxsize=3)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in APPLICATION_DDL.values():
                    await cur.execute(ddl)
                await cur.execute(
                    "INSERT INTO app_users(username,password_hash,created_at) "
                    "VALUES ('alice','test',UTC_TIMESTAMP(6))"
                )
        storage = MySQLStorage(base)
        storage.pool = pool
        repository = EvaluationRepository(storage)
        suite_payload = payload()
        suite_payload["owner"] = "alice"
        published = publish_suite(EvaluationSuite.model_validate(suite_payload), version=1)
        await repository.publish(published)
        with pytest.raises(EvaluationConflictError, match="immutable"):
            await repository.publish(published)
        historical = await repository.get_suite("default-procurement", 1)
        assert historical["content_fingerprint"] == published.contentFingerprint
        assert historical["suite"]["scenarios"][0]["id"] == "success"

        completed = "00000000-0000-0000-0000-000000000001"
        await repository.create_run(
            run_id=completed, suite_id="default-procurement", suite_version=1,
            requested_by="alice", execution_mode="rule-contract",
            manifest=manifest(suiteId="default-procurement"),
        )
        await repository.write_case(
            run_id=completed, scenario_id="success", repetition=1, status="RUNNING",
            input_data={"case": "success"},
        )
        await repository.write_assertion(
            run_id=completed, scenario_id="success", repetition=1, index=0,
            assertion_type="recommended_part_equals", status="PASSED", expected=7, actual=7,
        )
        await repository.write_case(
            run_id=completed, scenario_id="success", repetition=1, status="PASSED",
            input_data={"case": "success"}, output={"recommendedPartId": 7}, duration_ms=5,
        )
        assert await repository.finish_run(completed, status="COMPLETED", result={"passed": 1})
        await repository.set_baseline(
            suite_id="default-procurement", execution_mode="rule-contract",
            run_id=completed, selected_by="alice",
        )
        assert (await repository.list_suites())[0]["suite_id"] == "default-procurement"
        assert (await repository.list_runs())[0]["run_id"] == completed
        report = await repository.get_report(completed)
        assert report["status"] == "COMPLETED"
        assert report["incomplete"] is False
        assert report["cases"][0]["assertions"][0]["status"] == "PASSED"
        baseline = await repository.get_baseline(
            suite_id="default-procurement", execution_mode="rule-contract",
        )
        assert baseline["run_id"] == completed
        await repository.record_audit(
            actor_user_id="alice", action="RUN_EXPORT", resource_type="EVALUATION_RUN",
            resource_id=completed, outcome="SUCCESS", metadata={"format": "json"},
        )

        for suffix, status in (("2", "CANCELLED"), ("3", "TIMED_OUT"), ("4", "PARTIAL")):
            run_id = "00000000-0000-0000-0000-00000000000" + suffix
            await repository.create_run(
                run_id=run_id, suite_id="default-procurement", suite_version=1,
                requested_by="alice", execution_mode="rule-contract",
                manifest=manifest(suiteId="default-procurement"),
            )
            if status == "CANCELLED":
                assert await repository.request_cancel(run_id)
            assert await repository.finish_run(run_id, status=status, result={"status": status})
            assert (await repository.get_run(run_id))["status"] == status

        async with storage.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS count FROM evaluation_baselines")
            assert (await cur.fetchone())["count"] == 1
            await cur.execute("SELECT metadata_json FROM evaluation_audit_events WHERE action='BASELINE_SET'")
            baseline_audit = await cur.fetchone()
            baseline_metadata = json.loads(baseline_audit["metadata_json"])
            assert baseline_metadata["newRunId"] == completed
            assert baseline_metadata["previousRunId"] is None
            await cur.execute("SELECT COUNT(*) AS count FROM evaluation_audit_events")
            assert (await cur.fetchone())["count"] == 2
            await cur.execute("SELECT COUNT(*) AS count FROM evaluation_cases")
            assert (await cur.fetchone())["count"] == 1
    finally:
        if pool is not None:
            pool.close()
            await pool.wait_closed()
        async with admin.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        await admin.ensure_closed()
