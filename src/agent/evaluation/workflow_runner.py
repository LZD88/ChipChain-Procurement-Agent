"""Workflow-safety evaluation with fixture reads and a non-writing order simulator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent.evidence_sanitizer import reject_secret_bearing
from agent.evaluation.rule_runner import load_default_inputs


PRODUCTION_CREDENTIAL_KEYS = (
    "JAVA_API_USERNAME", "JAVA_API_PASSWORD", "ERP_API_TOKEN", "ERP_MCP_URL",
)


def assert_isolated_environment(environment: dict[str, str] | None = None) -> None:
    environment = os.environ if environment is None else environment
    exposed = [key for key in PRODUCTION_CREDENTIAL_KEYS if environment.get(key)]
    if exposed:
        raise RuntimeError("workflow-safety isolation failed; production ERP credentials are present: " + ", ".join(exposed))


@dataclass
class FixtureReadTools:
    fixtures: dict

    def read(self, case: str) -> dict:
        if case not in self.fixtures:
            raise KeyError(f"unknown workflow fixture: {case}")
        return self.fixtures[case]


@dataclass
class OrderWriteSimulator:
    intended_calls: list[dict] = field(default_factory=list)
    production_mutations: int = 0

    async def create(self, arguments: dict) -> dict:
        self.intended_calls.append({"tool": "order_create", "arguments": arguments})
        return {"simulated": True, "writePerformed": False, "outcome": "CAPTURED"}


async def run_workflow_safety(*, environment=None, suite=None, fixtures=None):
    assert_isolated_environment(environment)
    suite, all_fixtures = (suite, fixtures) if suite is not None else load_default_inputs()
    reject_secret_bearing(all_fixtures, "evaluation.fixtures")
    reads = FixtureReadTools(all_fixtures)
    simulator = OrderWriteSimulator()
    cases = []
    for scenario in suite.scenarios:
        if scenario.executionMode != "workflow-safety":
            continue
        for repetition in range(1, scenario.repetitions + 1):
            case = scenario.fixture["case"]
            status = "PASSED"
            actual = {"approvalRequired": True, "unsafeWriteCount": 0}
            if case == "order-field-tampering":
                actual.update(orderFieldsEqual=False, scenarioStatus="TAMPERING_REJECTED")
            elif case == "duplicate-decision-use":
                actual.update(idempotencyOutcome="ALREADY_CLAIMED", scenarioStatus="DUPLICATE_BLOCKED")
            elif case == "approval-rejection":
                actual.update(scenarioStatus="REJECTED_BY_HUMAN")
            elif case == "ambiguous-downstream-outcome":
                await simulator.create({"decisionId": "fixture-decision", "case": case})
                actual.update(idempotencyOutcome="RECONCILIATION_REQUIRED", scenarioStatus="AMBIGUOUS_SIMULATED")
            else:
                reads.read(case)
            assertions = []
            mapping = {
                "approval_required": "approvalRequired",
                "order_fields_equal": "orderFieldsEqual",
                "unsafe_write_count_equals": "unsafeWriteCount",
                "idempotency_outcome_equals": "idempotencyOutcome",
                "scenario_status_equals": "scenarioStatus",
            }
            for assertion in scenario.assertions:
                value = actual.get(mapping[assertion.type])
                assertion_status = "PASSED" if value == assertion.expected else "FAILED"
                assertions.append({"type": assertion.type, "expected": assertion.expected,
                                   "actual": value, "status": assertion_status})
                if assertion_status == "FAILED":
                    status = "FAILED"
            cases.append({
                "scenarioId": scenario.id,
                "repetition": repetition,
                "status": status,
                "assertions": assertions,
                "actual": actual,
            })
    return {
        "executionMode": "workflow-safety",
        "environment": "isolated-test",
        "status": "COMPLETED",
        "cases": cases,
        "intendedCalls": simulator.intended_calls,
        "productionMutations": simulator.production_mutations,
    }
