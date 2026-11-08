import pytest
from copy import deepcopy

from agent.evaluation.rule_runner import load_default_inputs
from agent.evaluation.workflow_runner import run_workflow_safety


@pytest.mark.asyncio
async def test_workflow_runner_captures_write_intent_with_zero_erp_mutations():
    report = await run_workflow_safety(environment={})
    assert report["status"] == "COMPLETED"
    assert len(report["cases"]) == 12
    assert all(case["status"] == "PASSED" for case in report["cases"])
    assert {case["repetition"] for case in report["cases"]} == {1, 2, 3}
    assert report["productionMutations"] == 0
    assert report["intendedCalls"] == [{
        "tool": "order_create",
        "arguments": {"decisionId": "fixture-decision", "case": "ambiguous-downstream-outcome"},
    }] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["JAVA_API_PASSWORD", "ERP_API_TOKEN", "ERP_MCP_URL"])
async def test_workflow_runner_fails_before_scenarios_when_production_access_exists(key):
    with pytest.raises(RuntimeError, match="isolation failed"):
        await run_workflow_safety(environment={key: "production-secret"})


@pytest.mark.asyncio
async def test_workflow_runner_rejects_secret_bearing_fixture_before_scenarios():
    suite, fixtures = load_default_inputs()
    tainted = deepcopy(fixtures)
    tainted["successful-recommendation"]["authorization"] = "Bearer must-not-leak"
    with pytest.raises(ValueError, match=r"evaluation\.fixtures.*authorization"):
        await run_workflow_safety(environment={}, suite=suite, fixtures=tainted)
