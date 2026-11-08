import pytest

from agent.evaluation.rule_runner import load_default_inputs
from agent.evaluation.worker import WorkerSettings, execute_run


class FakeRepository:
    def __init__(self, mode):
        suite, _fixtures = load_default_inputs()
        repetitions = 3 if mode == "workflow-safety" else 1
        self.run = {
            "run_id": "run-1", "suite_id": suite.suiteId, "suite_version": 1,
            "execution_mode": mode, "status": "RUNNING",
            "manifest": {
                "policyVersion": "supplier-score-v1",
                "repetitionCount": repetitions,
            },
        }
        self.suite = {"suite": suite.model_dump()}
        self.cases = []
        self.assertions = []
        self.finished = None

    async def get_run(self, _run_id):
        return self.run

    async def get_suite(self, _suite_id, _version):
        return self.suite

    async def cancellation_requested(self, _run_id):
        return False

    async def write_case(self, **kwargs):
        self.cases.append(kwargs)

    async def write_assertion(self, **kwargs):
        self.assertions.append(kwargs)

    async def finish_run(self, run_id, *, status, result):
        self.finished = {"run_id": run_id, "status": status, "result": result}
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected_cases", [("rule-contract", 6), ("workflow-safety", 12)])
async def test_worker_executes_isolated_runner_and_persists_incrementally(mode, expected_cases):
    repository = FakeRepository(mode)
    assert await execute_run(repository, "run-1", environment={}) is True
    assert len(repository.cases) == expected_cases
    assert repository.assertions
    assert repository.finished["status"] == "COMPLETED"
    assert repository.finished["result"]["metrics"]
    if mode == "workflow-safety":
        assert repository.finished["result"]["productionMutations"] == 0
        assert repository.finished["result"]["intendedCalls"]


@pytest.mark.asyncio
async def test_worker_rejects_manifest_repetition_mismatch():
    repository = FakeRepository("workflow-safety")
    repository.run["manifest"]["repetitionCount"] = 2
    assert await execute_run(repository, "run-1", environment={}) is True
    assert repository.cases == []
    assert repository.finished["status"] == "FAILED"
    assert repository.finished["result"]["reason"] == "runner_failure"


def test_worker_requires_dedicated_database_password():
    with pytest.raises(RuntimeError, match="EVALUATION_DB_PASSWORD"):
        WorkerSettings(password="").validate()
