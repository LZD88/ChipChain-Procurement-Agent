import json
from pathlib import Path

from agent.evaluation.schemas import EvaluationSuite, publish_suite


ROOT = Path(__file__).resolve().parents[2]


def test_default_suite_covers_required_objective_scenarios_and_publishes():
    path = ROOT / "src" / "evaluation" / "suites" / "default-v1.json"
    suite = EvaluationSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert {scenario.id for scenario in suite.scenarios} == {
        "successful-recommendation", "budget-no-solution", "moq-violation",
        "inactive-supplier", "missing-source-data", "deterministic-tie-breaking",
        "order-field-tampering", "duplicate-decision-use", "approval-rejection",
        "ambiguous-downstream-outcome",
    }
    assert all(scenario.assertions for scenario in suite.scenarios)
    assert all(assertion.type for scenario in suite.scenarios for assertion in scenario.assertions)
    assert len(publish_suite(suite, version=1).contentFingerprint) == 64
