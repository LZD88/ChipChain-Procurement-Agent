import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from agent.prompt_releases.bundles import builtin_bundle, PromptBundleSnapshot, PROTECTION
from agent.prompt_releases.candidate_suite import candidate_suite
from agent.prompt_releases.usage import UsageRecorder
from agent.evaluation.prompt_candidate_runner import run_candidate_case, run_prompt_candidate, IsolatedBusinessTools, MemoryExecutionBackend, ExecutionBudget, EvaluationBudgetExceeded
from agent.evaluation.rule_runner import load_default_inputs
from api_view.agent_loader import PromptUserKey
from api_view.runtime_preparation import RuntimePreparation
from api_view import runtime_preparation as preparation
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.runtime_config import RuntimeConfig


class Model(FakeMessagesListChatModel):
    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.extend(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", builtin_bundle().roles)
async def test_actual_role_prompt_and_protected_prefix(role):
    bundle = builtin_bundle()
    scenario = next(s for s in candidate_suite("alice").scenarios if s.fixture["role"] == role)
    model = Model(responses=[AIMessage(content="测试回答")])
    result = await run_candidate_case(bundle, scenario, model, environment={})
    assert role in result["result"]["rolesExecuted"]
    assert bundle.prompt(role).startswith(PROTECTION)
    system = '\n'.join(m.content if isinstance(m.content, str) else '\n'.join(block.get('text', '') for block in m.content if isinstance(block, dict)) for m in model.seen if m.type == 'system')
    assert bundle.roles[role] in system and PROTECTION in system
    assert result["status"] == ("PASSED" if scenario.id == "light-chat" else "FAILED")


@pytest.mark.asyncio
async def test_tools_enforce_approval_fields_idempotency_and_no_shell(tmp_path):
    fixture = load_default_inputs()[1]["successful-recommendation"]
    simulator = IsolatedBusinessTools(fixture)
    tools = simulator.tools()
    await tools["procurement_compare"].ainvoke({"part_name": "传感器", "quantity": 10, "budget": 1500})
    args = {"order_detail": [{"partId": 1, "quantity": 10, "unitPrice": 100}], "decision_id": "isolated-decision", "policy_version": "supplier-score-v1"}
    with pytest.raises(ValueError, match="approval"):
        await tools["order_create"].ainvoke(args)
    simulator.approved = True
    bad = {**args, "order_detail": [{"partId": 1, "quantity": 100, "unitPrice": 1}]}
    with pytest.raises(ValueError, match="证据"):
        await tools["order_create"].ainvoke(bad)
    assert simulator.events[-1]["blocked"] == "evidence_mismatch"
    assert (await tools["order_create"].ainvoke(args))["writePerformed"] is False
    from agent.decision_repository import DecisionClaimedError
    with pytest.raises(DecisionClaimedError):
        await tools["order_create"].ainvoke(args)
    assert simulator.events[-1]["blocked"] == "decision_already_claimed"
    target = tmp_path / "must-not-exist"
    assert (await MemoryExecutionBackend().aexecute(f"touch {target}")).exit_code == 1
    assert not target.exists()


def test_usage_multicall_deduplicates_and_preserves_unknown():
    recorder = UsageRecorder()
    for run_id, input_tokens, output_tokens in (("one", 100, 20), ("two", 200, 30)):
        recorder.on_chat_model_start({}, [], run_id=run_id)
        response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}))]])
        recorder.on_llm_end(response, run_id=run_id)
        recorder.on_llm_end(response, run_id=run_id)
    result = recorder.result()
    assert (result["inputTokens"], result["outputTokens"], result["modelCalls"]) == (300, 50, 2)
    recorder.on_chat_model_start({}, [], run_id="unknown")
    assert recorder.result()["usageState"] == "partial"
    assert UsageRecorder().result()["estimatedCostUsd"] is None
    budget = ExecutionBudget()
    for i in range(8):
        budget.on_chat_model_start({}, [], run_id=i)
    with pytest.raises(EvaluationBudgetExceeded):
        budget.on_chat_model_start({}, [], run_id=9)
    capped = ExecutionBudget(remaining_run_tokens=10)
    capped.on_chat_model_start({}, [], run_id="last")
    with pytest.raises(EvaluationBudgetExceeded, match="run-token"):
        capped.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))]]), run_id="last")
    assert capped.result()["inputTokens"] == 10
    with pytest.raises(EvaluationBudgetExceeded, match="run-token"):
        capped.on_chat_model_start({}, [], run_id="forbidden-next")


@pytest.mark.asyncio
async def test_candidate_rejects_production_and_fake_provenance():
    model = Model(responses=[AIMessage(content="ok")])
    with pytest.raises(RuntimeError, match="isolation"):
        await run_prompt_candidate(builtin_bundle(), candidate_suite("alice"), model, environment={"ERP_API_TOKEN": "production"})
    with pytest.raises(ValueError, match="real-provider"):
        await run_prompt_candidate(builtin_bundle(), candidate_suite("alice"), model, environment={}, provenance="real-provider")


@pytest.mark.asyncio
async def test_version_cache_keys_share_sandbox_but_not_graphs():
    bundle = builtin_bundle()
    revised = PromptBundleSnapshot.create("v2", {**bundle.roles, "main": "新提示词"})
    one, two = [PromptUserKey("alice", b.fingerprint, b) for b in (bundle, revised)]
    proxy = SandboxBackendProxy(SimpleNamespace(id="test"))
    proxies = {"alice": proxy}
    builder = AsyncMock(side_effect=lambda user, _: SimpleNamespace(bundle=user.snapshot))
    runtime = RuntimePreparation(builder, lambda:"runtime", replace(RuntimeConfig(), graph_max_users=2))
    with patch.object(preparation.sandbox_manager, "SANDBOX_BACKENDS", proxies), patch.object(preparation.sandbox_manager, "ensure_sandbox_for_user", AsyncMock(return_value=proxy)), patch.object(preparation, "check_sandbox", AsyncMock(return_value=True)):
        try:
            first, second = await asyncio.gather(runtime.acquire(one), runtime.acquire(two))
            assert first.bundle.fingerprint != second.bundle.fingerprint
            same = await runtime.acquire(one)
            assert same._entry.graph is first._entry.graph
            assert len(runtime.entries) == 2
            await same.release(); await first.release(); await second.release()
        finally:
            await runtime.close()


@pytest.mark.asyncio
async def test_downstream_fixture_and_versioned_safety_scenarios():
    suite = candidate_suite("alice")
    assert len(suite.scenarios) == 13
    assert all(s.repetitions == 3 for s in suite.scenarios)
    assert {"order-tampering", "order-duplicate", "analyst-downstream"} <= {s.id for s in suite.scenarios}
    fixture = {**load_default_inputs()[1]["successful-recommendation"], "downstreamUnavailable": True}
    business = IsolatedBusinessTools(fixture)
    with pytest.raises(RuntimeError, match="不可用"):
        await business.tools()["procurement_compare"].ainvoke({"part_name": "传感器", "quantity": 10})
    assert business.events == [{"tool": "procurement_compare", "error": "downstream_unavailable"}]
    assert not business.claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status,reason", [
    (TimeoutError(), "TIMED_OUT", "timeout"),
    (EvaluationBudgetExceeded(), "FAILED", "budget_exceeded"),
    (RuntimeError(), "DEPENDENCY_ERROR", "dependency_failure"),
])
async def test_failed_cases_retain_usage_and_classify(error, status, reason):
    async def fail(*args, budget, **kwargs):
        budget.on_chat_model_start({}, [], run_id="call")
        budget.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="partial", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))]]), run_id="call")
        raise error
    suite = candidate_suite("alice").model_copy(update={"scenarios": candidate_suite("alice").scenarios[:1]})
    with patch("agent.evaluation.prompt_candidate_runner.run_candidate_case", fail):
        report = await run_prompt_candidate(builtin_bundle(), suite, Model(responses=[AIMessage(content="ok")]), environment={})
    assert len(report["cases"]) == 3
    for case in report["cases"]:
        assert case["status"] == status
        assert case["result"]["reason"] == reason
        assert case["result"]["usage"]["inputTokens"] == 10
        assert case["durationMs"] >= 0


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_completed():
    with patch("agent.evaluation.prompt_candidate_runner.run_candidate_case", AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await run_prompt_candidate(builtin_bundle(), candidate_suite("alice"), Model(responses=[AIMessage(content="ok")]), environment={})


@pytest.mark.asyncio
async def test_failed_version_preparation_does_not_destroy_shared_sandbox():
    baseline = builtin_bundle()
    revised = PromptBundleSnapshot.create('v2', {**baseline.roles, 'main': '测试并发失败。'})
    good, bad = [PromptUserKey('alice', bundle.fingerprint, bundle) for bundle in (baseline, revised)]
    proxy = SandboxBackendProxy(SimpleNamespace(id='shared-test'))
    proxies, reached = {}, []
    ready = asyncio.Event()
    async def ensure(owner):
        reached.append(owner)
        if len(reached) == 2: ready.set()
        await ready.wait()
        proxies[owner] = proxy
        return proxy
    async def build(key, _):
        if key == bad:
            while not runtime.entries: await asyncio.sleep(0)
            raise ValueError('graph failed')
        return SimpleNamespace(bundle=key.snapshot)
    runtime = RuntimePreparation(build, lambda: 'runtime', replace(RuntimeConfig(), graph_max_users=2, prepare_concurrency=2))
    cleanup = AsyncMock()
    storage = SimpleNamespace(get_sandbox=AsyncMock(return_value=None))
    with patch.object(preparation.sandbox_manager, 'SANDBOX_BACKENDS', proxies), patch.object(preparation.sandbox_manager, '_sandbox_storage', lambda: storage), patch.object(preparation.sandbox_manager, 'ensure_sandbox_for_user', ensure), patch.object(preparation.sandbox_manager, 'cleanup_user', cleanup):
        try:
            first, second = await asyncio.gather(runtime.acquire(good), runtime.acquire(bad), return_exceptions=True)
            assert isinstance(second, ValueError)
            assert first.bundle.version == 'builtin-v1'
            cleanup.assert_not_awaited()
            assert proxies['alice'] is proxy
            await first.release()
        finally: await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.CancelledError(), RuntimeError('failed')])
async def test_candidate_claim_loss_does_not_crash_worker(failure):
    from agent.evaluation.prompt_worker import execute_candidate
    from agent.evaluation.repository import EvaluationConflictError
    repository = SimpleNamespace(persist_candidate=AsyncMock(side_effect=EvaluationConflictError('frozen')),
        get_report=AsyncMock(return_value={'status': 'FAILED', 'cases': []}))
    with patch('agent.evaluation.prompt_worker.assert_isolated_environment', side_effect=failure):
        assert await execute_candidate(repository, {'run_id': 'run', 'lease_owner': 'old'}, environment={}) is False
