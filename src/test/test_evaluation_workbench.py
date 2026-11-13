"""Exercise failure/replay and research isolation in disposable databases only."""
from dataclasses import replace
from uuid import uuid4
from unittest.mock import patch

import pytest

from test_prompt_repository_mysql import prompt_db
from test_evaluation_api_cli import api
from agent.evaluation.workbench import Workbench, TaskRequest, Variant, CaptureRequest, ExperimentRequest
from agent.evaluation.repository import EvaluationConflictError
from agent.evaluation.worker import execute_run
from agent.evaluation import rule_runner
from agent.prompt_releases.repository import PromptRepository
from agent.prompt_releases.bundles import builtin_bundle
from agent.prompt_releases.gates import evaluate_pair


@pytest.mark.asyncio
async def test_capture_actual_rule_failure_then_replay_after_fix(prompt_db):
    wb = Workbench(prompt_db)
    await wb.prompts.initialize("alice")
    task = TaskRequest(suiteId="default-procurement", executionMode="rule-contract", scenarioIds=["successful-recommendation"])
    queued = await wb.create_task(task, "alice")
    repo = wb.repository
    assert await repo.claim_next_run(worker_id="rule-test") == queued["runId"]
    original_policy = rule_runner.get_policy("supplier-score-v1")
    def broken_rule(**kwargs):
        return {**original_policy.evaluator(**kwargs), "recommendedPartId": 999}
    with patch.object(rule_runner, "get_policy", return_value=replace(original_policy, evaluator=broken_rule)):
        await execute_run(repo, queued["runId"], environment={})
    report = await repo.get_report(queued["runId"])
    assert report["cases"][0]["status"] == "FAILED"
    request = CaptureRequest(runId=queued["runId"], scenarioId="successful-recommendation", title="Wrong recommendation")
    failure = await wb.capture(request, "alice")
    assert await wb.capture(request, "alice") == failure  # Duplicate clicks preserve one snapshot.
    frozen = (await wb.failure(failure["failureId"]))[1]
    assert frozen["case"]["result"]["recommendedPartId"] == 999
    assert frozen["suite"]["scenarios"][0]["assertions"][0]["expected"] == 1
    replay = await wb.replay(failure["failureId"], Variant(), "alice")
    assert (await wb.failures())[0]["state"] == "REPLAYING"
    assert await repo.claim_next_run(worker_id="fixed-rule-test") == replay["runId"]
    await execute_run(repo, replay["runId"], environment={})
    assert (await wb.failures())[0]["state"] == "RESOLVED"
    assert (await wb.failure(failure["failureId"]))[1] == frozen
    with pytest.raises(ValueError, match="实际失败"):
        await wb.capture(CaptureRequest(runId=replay["runId"], scenarioId="successful-recommendation", title="not a failure"), "alice")
    retry = await wb.replay(failure["failureId"], Variant(), "alice")
    await repo.request_cancel(retry["runId"])
    assert (await wb.failures())[0]["state"] == "OPEN"  # A cancelled latest replay cannot claim repair.


@pytest.mark.asyncio
async def test_experiment_atomic_pair_fences_and_release_exclusion(prompt_db):
    wb = Workbench(prompt_db)
    await wb.prompts.initialize("alice")
    task = TaskRequest(scenarioIds=["light-chat"], repetitions=1)
    request = ExperimentRequest(task=task, baseline=Variant(), candidate=Variant(skillVariant="without-skills"))
    pair = await wb.experiment(request, "alice")
    repo = wb.repository
    a, b = [await repo.get_run(pair[k]) for k in ("baselineRunId", "candidateRunId")]
    assert a["manifest"]["suiteFingerprint"] == b["manifest"]["suiteFingerprint"]
    assert a["manifest"]["skillFingerprint"] != b["manifest"]["skillFingerprint"]
    claim = await repo.claim_next_run(worker_id="research-owner")
    assert claim in {a["run_id"], b["run_id"]}
    assert await repo.claim_next_run(worker_id="overlap") is None
    with pytest.raises(EvaluationConflictError):
        await repo.finish_run(claim, status="COMPLETED")
    with pytest.raises(EvaluationConflictError):
        await repo.persist_candidate(claim, "wrong-owner", status="COMPLETED")
    assert await repo.request_cancel(claim)
    assert not await repo.persist_candidate(claim, "research-owner", status="COMPLETED")
    assert (await repo.get_run(claim))["status"] == "CANCELLED"
    assert await repo.claim_next_run(worker_id="next-owner")
    report = await repo.get_report(claim)
    gate = evaluate_pair(report, report, builtin_bundle(), builtin_bundle(), report["manifest"]["deploymentFingerprint"])
    assert not gate["passed"]
    assert any("真实提示词评测" in reason for reason in gate["reasons"])
    with pytest.raises(ValueError, match="至少改变"):
        await wb.experiment(ExperimentRequest(task=task, baseline=Variant(), candidate=Variant()), "alice")


@pytest.mark.asyncio
async def test_task_rejects_unknown_models_scenarios_and_forged_fingerprints(prompt_db):
    wb = Workbench(prompt_db)
    await wb.prompts.initialize("alice")
    with pytest.raises(ValueError, match="允许列表"):
        await wb.create_task(TaskRequest(modelId="unreviewed-host/model"), "alice")
    with pytest.raises(ValueError, match="这些场景"):
        await wb.create_task(TaskRequest(scenarioIds=["does-not-exist"]), "alice")
    with pytest.raises(ValueError, match="不能重复"):
        await wb.create_task(TaskRequest(scenarioIds=["light-chat", "light-chat"]), "alice")
    rows = await wb.repository.list_runs()
    assert rows == []


@pytest.mark.parametrize("path,body", [
    ("tasks", {}),
    ("experiments", {"task": {}, "baseline": {}, "candidate": {}}),
    ("failures", {"runId": "hidden-run", "scenarioId": "secret-case", "title": "case"}),
    ("failures/hidden-failure/replay", {}),
])
def test_readonly_users_cannot_mutate_workbench(api, path, body):
    client, repository, actor = api
    response = client.post(f"/api/evaluations/{path}", json=body, headers={"X-Requested-With": "DeepAgent"})
    assert response.status_code == 403
    assert "hidden" not in response.text
    assert repository.audit[-1]["outcome"] == "DENIED"
    csrf = client.post(f"/api/evaluations/{path}", json=body)
    assert csrf.status_code == 403


@pytest.mark.asyncio
async def test_without_skills_changes_graph_inputs_not_just_label():
    from agent.evaluation.prompt_candidate_runner import run_candidate_case
    from agent.prompt_releases.candidate_suite import candidate_suite
    from test_prompt_execution import Model
    from langchain_core.messages import AIMessage
    scenario = next(s for s in candidate_suite("alice").scenarios if s.fixture["role"] == "procurement-analyst")
    scenario = scenario.model_copy(update={"fixture": {**scenario.fixture, "question": scenario.fixture["question"] + "请使用技能生成报告。"}})
    current = Model(responses=[AIMessage(content="test")])
    stripped = Model(responses=[AIMessage(content="test")])
    await run_candidate_case(builtin_bundle(), scenario, current, environment={})
    await run_candidate_case(builtin_bundle(), scenario, stripped, environment={}, skill_variant="without-skills")
    current_text = "\n".join(str(m.content) for m in current.seen)
    stripped_text = "\n".join(str(m.content) for m in stripped.seen)
    assert current_text != stripped_text
    assert '可用技能（索引已加载' in current_text
    assert '可用技能（索引已加载' not in stripped_text


@pytest.mark.asyncio
async def test_missing_skill_file_does_not_count_as_successful_skill_use():
    from agent.evaluation.workbench import real_suite
    from agent.evaluation.prompt_candidate_runner import run_candidate_case
    from test_prompt_execution import Model
    from langchain_core.messages import AIMessage
    scenario = next(s for s in real_suite("alice").scenarios if s.id == "analyst-skills")
    for variant, expected in (("current", True), ("without-skills", False)):
        model = Model(responses=[AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"file_path": "/skills/procurement/procurement-analysis/SKILL.md"}, "id": "read-skill"}]), AIMessage(content="test")])
        result = await run_candidate_case(builtin_bundle(), scenario, model, environment={}, skill_variant=variant)
        assertion = next(a for a in result["assertions"] if a["type"] == "skill_loaded")
        assert assertion["actual"] is expected


@pytest.mark.asyncio
async def test_online_failure_capture_is_owner_scoped_and_replays_original_question(prompt_db, monkeypatch):
    from agent.evaluation.online_failures import record_online_failure, list_online_failures, capture_online_failure, OnlineCaptureRequest
    wb = Workbench(prompt_db)
    await wb.prompts.initialize("alice")
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "private-key-not-for-evidence")
    request_id = str(uuid4())
    await record_online_failure(prompt_db, request_id=request_id, owner="alice", thread_id="test-thread", question="库存查询 token=private-key-not-for-evidence", route="lightweight", prompt_version="builtin-v1")
    inbox = await list_online_failures(prompt_db, "alice")
    assert len(inbox) == 1
    assert "private-key-not-for-evidence" not in str(inbox)
    assert await list_online_failures(prompt_db, "other") == []
    body = OnlineCaptureRequest(requestId=request_id, scenarioId="light-read", title="Online inventory failure")
    with pytest.raises(ValueError, match="无权访问"):
        await capture_online_failure(wb, body, "other")
    result = await capture_online_failure(wb, body, "alice")
    assert await capture_online_failure(wb, body, "alice") == result
    snapshot = (await wb.failure(result["failureId"]))[1]
    assert snapshot["suite"]["scenarios"][0]["fixture"]["question"] == inbox[0]["snapshot"]["question"]
    assert snapshot["case"]["result"]["sourceRequestId"] == request_id
    replay = await wb.replay(result["failureId"], Variant(), "alice")
    run = await wb.repository.get_run(replay["runId"])
    assert run["execution_mode"] == "experiment"
    assert run["manifest"]["repetitionCount"] == 3
