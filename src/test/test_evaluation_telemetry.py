import re

import pytest
from prometheus_client import generate_latest

from agent.evaluation.telemetry import observe_evaluation_run


def test_evaluation_metrics_use_only_bounded_low_cardinality_labels():
    observe_evaluation_run(
        execution_mode="workflow-safety", status="PARTIAL",
        environment="isolated-test", duration_seconds=1.25,
    )
    scrape = generate_latest().decode("utf-8")
    lines = [line for line in scrape.splitlines()
             if line.startswith("procurement_evaluation_") and not line.startswith("#")]
    assert any(line.startswith("procurement_evaluation_runs_total") for line in lines)
    assert any(line.startswith("procurement_evaluation_run_duration_seconds_bucket") for line in lines)
    for line in lines:
        labels = re.search(r"\{([^}]*)\}", line)
        if labels:
            label_names = {item.split("=", 1)[0] for item in labels.group(1).split(",")}
            assert label_names <= {"execution_mode", "status", "environment", "le"}
        for forbidden in ("user", "decision", "suite", "scenario", "run_id", "runId"):
            assert forbidden not in line


@pytest.mark.parametrize(
    "update",
    [
        {"execution_mode": "arbitrary"},
        {"status": "RUNNING"},
        {"environment": "production-customer-42"},
    ],
)
def test_unknown_label_values_are_rejected(update):
    values = {
        "execution_mode": "rule-contract", "status": "COMPLETED",
        "environment": "ci", "duration_seconds": 0,
    }
    values.update(update)
    with pytest.raises(ValueError):
        observe_evaluation_run(**values)
