"""MySQL persistence for immutable evaluation suites and incremental runs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiomysql

from agent.evaluation.schemas import PublishedSuite
from agent.evaluation.manifest import EvaluationRunManifest
from agent.evaluation.telemetry import observe_evaluation_run


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value):
    return value if value is None or isinstance(value, (dict, list)) else json.loads(value)


class EvaluationConflictError(RuntimeError):
    pass


class EvaluationRepository:
    def __init__(self, storage):
        self.storage = storage

    async def publish(self, published: PublishedSuite, *, now=None) -> None:
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        suite = published.suite
        try:
            async with self.storage.cursor(transaction=True) as cur:
                await cur.execute(
                    "INSERT INTO evaluation_suite_versions "
                    "(suite_id,version,owner_user_id,title,content_fingerprint,suite_json,published_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (suite.suiteId, published.version, suite.owner, suite.title,
                     published.contentFingerprint, _json(suite.model_dump()), now),
                )
        except aiomysql.IntegrityError as exc:
            raise EvaluationConflictError("evaluation suite version is immutable") from exc

    async def get_suite(self, suite_id: str, version: int):
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT * FROM evaluation_suite_versions WHERE suite_id=%s AND version=%s",
                (suite_id, version),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return {**row, "suite": _decode(row["suite_json"])}

    async def list_suites(self, *, page: int = 1, size: int = 20):
        if page < 1 or size < 1 or size > 100:
            raise ValueError("invalid evaluation suite pagination")
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT suite_id,version,owner_user_id,title,content_fingerprint,published_at "
                "FROM evaluation_suite_versions ORDER BY published_at DESC,suite_id,version DESC "
                "LIMIT %s OFFSET %s",
                (size, (page - 1) * size),
            )
            return list(await cur.fetchall())

    async def create_run(self, *, run_id, suite_id, suite_version, requested_by,
                         execution_mode, manifest, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        validated_manifest = (
            manifest if isinstance(manifest, EvaluationRunManifest)
            else EvaluationRunManifest.model_validate(manifest)
        )
        if (validated_manifest.suiteId != suite_id
                or validated_manifest.suiteVersion != suite_version
                or validated_manifest.executionMode != execution_mode):
            raise ValueError("evaluation manifest does not match queued run")
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO evaluation_runs "
                "(run_id,suite_id,suite_version,requested_by,execution_mode,status,manifest_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,'QUEUED',%s,%s)",
                (run_id, suite_id, suite_version, requested_by, execution_mode,
                 _json({**validated_manifest.model_dump(mode="json"),
                        "manifestFingerprint": validated_manifest.fingerprint()}), now),
            )

    async def list_runs(self, *, page: int = 1, size: int = 20, suite_id: str | None = None):
        if page < 1 or size < 1 or size > 100:
            raise ValueError("invalid evaluation run pagination")
        where, parameters = "", []
        if suite_id:
            where, parameters = " WHERE suite_id=%s", [suite_id]
        parameters.extend((size, (page - 1) * size))
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT run_id,suite_id,suite_version,requested_by,execution_mode,status,"
                "created_at,started_at,finished_at,cancel_requested_at FROM evaluation_runs" + where +
                " ORDER BY created_at DESC,run_id LIMIT %s OFFSET %s",
                tuple(parameters),
            )
            return list(await cur.fetchall())

    async def write_case(self, *, run_id, scenario_id, repetition, status, input_data,
                         output=None, diagnostic=None, duration_ms=None, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        terminal = status in {"PASSED", "FAILED", "TIMED_OUT", "SETUP_ERROR", "DEPENDENCY_ERROR", "CANCELLED"}
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO evaluation_cases "
                "(run_id,scenario_id,repetition,status,input_json,output_json,diagnostic_json,duration_ms,created_at,finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE status=VALUES(status),output_json=VALUES(output_json),"
                "diagnostic_json=VALUES(diagnostic_json),duration_ms=VALUES(duration_ms),"
                "finished_at=VALUES(finished_at)",
                (run_id, scenario_id, repetition, status, _json(input_data),
                 _json(output) if output is not None else None,
                 _json(diagnostic) if diagnostic is not None else None,
                 duration_ms, now, now if terminal else None),
            )

    async def write_assertion(self, *, run_id, scenario_id, repetition, index,
                              assertion_type, status, expected, actual, diagnostic=None):
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO evaluation_assertion_results "
                "(run_id,scenario_id,repetition,assertion_index,assertion_type,status,"
                "expected_json,actual_json,diagnostic) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE status=VALUES(status),actual_json=VALUES(actual_json),"
                "diagnostic=VALUES(diagnostic)",
                (run_id, scenario_id, repetition, index, assertion_type, status,
                 _json(expected), _json(actual), diagnostic),
            )

    async def finish_run(self, run_id: str, *, status: str, result=None, now=None):
        if status not in {"COMPLETED", "FAILED", "PARTIAL", "CANCELLED", "TIMED_OUT"}:
            raise ValueError("invalid evaluation terminal status")
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        observation = None
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT execution_mode,manifest_json,started_at,created_at FROM evaluation_runs "
                "WHERE run_id=%s AND finished_at IS NULL FOR UPDATE",
                (run_id,),
            )
            active = await cur.fetchone()
            if active is None:
                return False
            await cur.execute(
                "UPDATE evaluation_runs SET status=%s,result_json=%s,finished_at=%s,"
                "lease_owner=NULL,lease_expires_at=NULL WHERE run_id=%s AND finished_at IS NULL",
                (status, _json(result) if result is not None else None, now, run_id),
            )
            updated = cur.rowcount == 1
            if updated:
                manifest = _decode(active["manifest_json"])
                started = active.get("started_at") or active["created_at"]
                observation = {
                    "execution_mode": active["execution_mode"],
                    "status": status,
                    "environment": manifest["environment"],
                    "duration_seconds": max(0.0, (now - started).total_seconds()),
                }
        if observation:
            # Metrics are operational evidence, never a reason to roll back a finished run.
            try:
                observe_evaluation_run(**observation)
            except (KeyError, TypeError, ValueError):
                pass
        return updated

    async def request_cancel(self, run_id: str, *, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE evaluation_runs SET cancel_requested_at=%s "
                "WHERE run_id=%s AND status IN ('QUEUED','RUNNING') AND cancel_requested_at IS NULL",
                (now, run_id),
            )
            return cur.rowcount == 1

    async def set_baseline(self, *, suite_id, execution_mode, run_id, selected_by, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT run_id FROM evaluation_baselines WHERE suite_id=%s AND execution_mode=%s FOR UPDATE",
                (suite_id, execution_mode),
            )
            previous = await cur.fetchone()
            await cur.execute(
                "INSERT INTO evaluation_baselines "
                "(suite_id,execution_mode,run_id,selected_by,selected_at) "
                "SELECT suite_id,execution_mode,run_id,%s,%s FROM evaluation_runs "
                "WHERE run_id=%s AND suite_id=%s AND execution_mode=%s AND status='COMPLETED' "
                "ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),selected_by=VALUES(selected_by),"
                "selected_at=VALUES(selected_at)",
                (selected_by, now, run_id, suite_id, execution_mode),
            )
            if cur.rowcount < 1:
                raise ValueError("baseline run must be a compatible completed run")
            await cur.execute(
                "INSERT INTO evaluation_audit_events "
                "(actor_user_id,action,resource_type,resource_id,outcome,metadata_json,created_at) "
                "VALUES (%s,'BASELINE_SET','EVALUATION_SUITE',%s,'SUCCESS',%s,%s)",
                (selected_by, suite_id, _json({
                    "executionMode": execution_mode,
                    "previousRunId": previous.get("run_id") if previous else None,
                    "newRunId": run_id,
                }), now),
            )

    async def get_run(self, run_id: str):
        async with self.storage.cursor() as cur:
            await cur.execute("SELECT * FROM evaluation_runs WHERE run_id=%s", (run_id,))
            row = await cur.fetchone()
        if not row:
            return None
        return {**row, "manifest": _decode(row["manifest_json"]), "result": _decode(row.get("result_json"))}

    async def get_report(self, run_id: str):
        run = await self.get_run(run_id)
        if run is None:
            return None
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT * FROM evaluation_cases WHERE run_id=%s "
                "ORDER BY scenario_id,repetition",
                (run_id,),
            )
            case_rows = list(await cur.fetchall())
            await cur.execute(
                "SELECT * FROM evaluation_assertion_results WHERE run_id=%s "
                "ORDER BY scenario_id,repetition,assertion_index",
                (run_id,),
            )
            assertion_rows = list(await cur.fetchall())
        assertions: dict[tuple[str, int], list[dict]] = {}
        for row in assertion_rows:
            assertions.setdefault((row["scenario_id"], row["repetition"]), []).append({
                "index": row["assertion_index"],
                "type": row["assertion_type"],
                "status": row["status"],
                "expected": _decode(row.get("expected_json")),
                "actual": _decode(row.get("actual_json")),
                "diagnostic": row.get("diagnostic"),
            })
        cases = []
        for row in case_rows:
            cases.append({
                "scenarioId": row["scenario_id"],
                "repetition": row["repetition"],
                "status": row["status"],
                "input": _decode(row.get("input_json")),
                "result": _decode(row.get("output_json")),
                "diagnostic": _decode(row.get("diagnostic_json")),
                "durationMs": row.get("duration_ms"),
                "assertions": assertions.get((row["scenario_id"], row["repetition"]), []),
            })
        aggregate = run.get("result") or {}
        return {
            "runId": run["run_id"],
            "suiteId": run["suite_id"],
            "suiteVersion": run["suite_version"],
            "executionMode": run["execution_mode"],
            "status": run["status"],
            "incomplete": run["status"] != "COMPLETED",
            "manifest": run["manifest"],
            "metrics": aggregate.get("metrics", []),
            "aggregate": aggregate,
            "cases": cases,
            "createdAt": run.get("created_at"),
            "startedAt": run.get("started_at"),
            "finishedAt": run.get("finished_at"),
        }

    async def get_baseline(self, *, suite_id: str, execution_mode: str):
        async with self.storage.cursor() as cur:
            await cur.execute(
                "SELECT * FROM evaluation_baselines WHERE suite_id=%s AND execution_mode=%s",
                (suite_id, execution_mode),
            )
            return await cur.fetchone()

    async def record_audit(self, *, actor_user_id: str | None, action: str,
                           resource_type: str, resource_id: str | None,
                           outcome: str, metadata: dict | None = None, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "INSERT INTO evaluation_audit_events "
                "(actor_user_id,action,resource_type,resource_id,outcome,metadata_json,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (actor_user_id, action, resource_type, resource_id, outcome,
                 _json(metadata or {}), now),
            )

    async def claim_next_run(self, *, worker_id: str, lease_seconds: int = 60, now=None):
        if not worker_id or lease_seconds < 5 or lease_seconds > 3600:
            raise ValueError("invalid evaluation worker lease")
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        lease_expires = now + timedelta(seconds=lease_seconds)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT run_id FROM evaluation_runs WHERE status='QUEUED' "
                "AND cancel_requested_at IS NULL ORDER BY created_at,run_id "
                "LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            row = await cur.fetchone()
            if not row:
                return None
            await cur.execute(
                "UPDATE evaluation_runs SET status='RUNNING',lease_owner=%s,lease_expires_at=%s,"
                "started_at=COALESCE(started_at,%s) WHERE run_id=%s AND status='QUEUED'",
                (worker_id, lease_expires, now, row["run_id"]),
            )
            if cur.rowcount != 1:
                return None
            return row["run_id"]

    async def renew_lease(self, run_id: str, *, worker_id: str, lease_seconds: int = 60, now=None):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "UPDATE evaluation_runs SET lease_expires_at=%s WHERE run_id=%s "
                "AND status='RUNNING' AND lease_owner=%s AND cancel_requested_at IS NULL",
                (now + timedelta(seconds=lease_seconds), run_id, worker_id),
            )
            return cur.rowcount == 1

    async def recover_expired_leases(self, *, now=None, limit: int = 100):
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        if limit < 1 or limit > 1000:
            raise ValueError("invalid lease recovery limit")
        async with self.storage.cursor(transaction=True) as cur:
            await cur.execute(
                "SELECT run_id FROM evaluation_runs WHERE status='RUNNING' "
                "AND lease_expires_at<%s ORDER BY lease_expires_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (now, limit),
            )
            rows = await cur.fetchall()
            ids = [row["run_id"] for row in rows]
            for run_id in ids:
                await cur.execute(
                    "UPDATE evaluation_runs SET status='QUEUED',lease_owner=NULL,lease_expires_at=NULL,"
                    "result_json=JSON_OBJECT('incomplete',TRUE,'reason','worker_lease_expired') "
                    "WHERE run_id=%s AND status='RUNNING' AND lease_expires_at<%s",
                    (run_id, now),
                )
            return ids

    async def cancellation_requested(self, run_id: str, *, worker_id: str | None = None) -> bool:
        async with self.storage.cursor() as cur:
            if worker_id:
                await cur.execute(
                    "SELECT cancel_requested_at FROM evaluation_runs WHERE run_id=%s "
                    "AND status='RUNNING' AND lease_owner=%s",
                    (run_id, worker_id),
                )
            else:
                await cur.execute(
                    "SELECT cancel_requested_at FROM evaluation_runs WHERE run_id=%s",
                    (run_id,),
                )
            row = await cur.fetchone()
        return bool(row and row.get("cancel_requested_at") is not None)
