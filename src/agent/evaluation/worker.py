"""MySQL-backed evaluation worker with isolated deterministic/safety runners."""

from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass, field

import aiomysql

from agent.evaluation.metrics import calculate_metrics
from agent.evaluation.repository import EvaluationRepository
from agent.evaluation.rule_runner import load_default_inputs, run_rule_contract
from agent.evaluation.schemas import EvaluationSuite
from agent.evaluation.workflow_runner import run_workflow_safety
from agent.mysql_storage import MySQLStorage


@dataclass(frozen=True)
class WorkerSettings:
    host: str = "localhost"
    port: int = 3306
    user: str = "chipchain_evaluation"
    password: str = field(default="", repr=False)
    database: str = "agent_db"
    poll_seconds: float = 2.0
    lease_seconds: int = 120

    @classmethod
    def from_env(cls):
        return cls(
            host=os.getenv("EVALUATION_DB_HOST", os.getenv("MYSQL_HOST", "localhost")),
            port=int(os.getenv("EVALUATION_DB_PORT", os.getenv("MYSQL_PORT", "3306"))),
            user=os.getenv("EVALUATION_DB_USER", "chipchain_evaluation"),
            password=os.getenv("EVALUATION_DB_PASSWORD", ""),
            database=os.getenv("EVALUATION_DB_NAME", os.getenv("MYSQL_DATABASE", "agent_db")),
            poll_seconds=float(os.getenv("EVALUATION_WORKER_POLL_SECONDS", "2")),
            lease_seconds=int(os.getenv("EVALUATION_WORKER_LEASE_SECONDS", "120")),
        )

    def validate(self):
        if not self.password:
            raise RuntimeError("EVALUATION_DB_PASSWORD is required")
        if not 0.1 <= self.poll_seconds <= 60 or not 10 <= self.lease_seconds <= 3600:
            raise RuntimeError("invalid evaluation worker timing configuration")


async def create_worker_storage(settings: WorkerSettings) -> MySQLStorage:
    settings.validate()
    pool = await aiomysql.create_pool(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, db=settings.database, charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=4, pool_recycle=1800,
    )
    storage = MySQLStorage()
    storage.pool = pool
    return storage


async def execute_run(repository: EvaluationRepository, run_id: str, *, environment=None):
    run = await repository.get_run(run_id)
    if run is None:
        return False
    if await repository.cancellation_requested(run_id):
        return await repository.finish_run(run_id, status="CANCELLED", result={
            "incomplete": True, "reason": "cancelled_before_execution", "metrics": [],
        })
    suite_row = await repository.get_suite(run["suite_id"], run["suite_version"])
    if suite_row is None:
        return await repository.finish_run(run_id, status="FAILED", result={
            "incomplete": True, "reason": "published_suite_missing", "metrics": [],
        })
    suite = EvaluationSuite.model_validate(suite_row["suite"])
    _default_suite, fixtures = load_default_inputs()
    try:
        configured_repetitions = {
            scenario.repetitions
            for scenario in suite.scenarios
            if scenario.executionMode == run["execution_mode"]
        }
        if configured_repetitions != {run["manifest"]["repetitionCount"]}:
            raise ValueError("run manifest repetition count does not match the published suite")
        if run["execution_mode"] == "rule-contract":
            report = run_rule_contract(
                suite, fixtures, policy_version=run["manifest"]["policyVersion"],
            )
        elif run["execution_mode"] == "workflow-safety":
            report = await run_workflow_safety(
                environment=environment, suite=suite, fixtures=fixtures,
            )
        else:
            raise ValueError("unsupported execution mode")
        for case in report["cases"]:
            if await repository.cancellation_requested(run_id):
                return await repository.finish_run(run_id, status="CANCELLED", result={
                    "incomplete": True, "reason": "cancelled_during_persistence", "metrics": [],
                })
            scenario = next(item for item in suite.scenarios if item.id == case["scenarioId"])
            repetition = int(case.get("repetition", 1))
            output = case.get("result", case.get("actual"))
            await repository.write_case(
                run_id=run_id, scenario_id=case["scenarioId"], repetition=repetition,
                status=case["status"], input_data={"fixture": scenario.fixture}, output=output,
                duration_ms=case.get("durationMs"),
            )
            for index, assertion in enumerate(case.get("assertions", [])):
                await repository.write_assertion(
                    run_id=run_id, scenario_id=case["scenarioId"], repetition=repetition,
                    index=index, assertion_type=assertion["type"], status=assertion["status"],
                    expected=assertion.get("expected"), actual=assertion.get("actual"),
                    diagnostic=assertion.get("diagnostic"),
                )
        metric_result = calculate_metrics(report)
        return await repository.finish_run(run_id, status="COMPLETED", result={
            **metric_result,
            "reportFingerprint": report.get("reportFingerprint"),
            "productionMutations": report.get("productionMutations"),
            "intendedCalls": report.get("intendedCalls", []),
        })
    except Exception as exc:
        return await repository.finish_run(run_id, status="FAILED", result={
            "incomplete": True,
            "reason": "runner_failure",
            "errorType": type(exc).__name__,
            "metrics": [],
        })


async def run_worker(*, once: bool | None = None):
    settings = WorkerSettings.from_env()
    storage = await create_worker_storage(settings)
    repository = EvaluationRepository(storage)
    worker_id = os.getenv("EVALUATION_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    once = (os.getenv("EVALUATION_WORKER_ONCE", "false").lower() == "true"
            if once is None else once)
    try:
        while True:
            await repository.recover_expired_leases()
            run_id = await repository.claim_next_run(
                worker_id=worker_id, lease_seconds=settings.lease_seconds,
            )
            if run_id:
                await execute_run(repository, run_id, environment=os.environ)
            if once:
                return
            await asyncio.sleep(settings.poll_seconds)
    finally:
        await storage.close()


def main():
    # CLI workers use the same project-level configuration as the Web and MCP
    # launchers.  Keep this in ``main`` so imported tests can still provide an
    # isolated environment explicitly.
    from dotenv import load_dotenv
    from agent.env_utils import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
