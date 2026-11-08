from datetime import datetime

import pytest
from pydantic import ValidationError

from agent.evaluation.manifest import EvaluationRunManifest, assertion_evidence_class


def manifest(**updates):
    data = {
        "suiteId": "suite", "suiteVersion": 1, "suiteFingerprint": "a" * 64,
        "fixtureVersion": "fixtures-v1", "fixtureFingerprint": "b" * 64,
        "policyVersion": "supplier-score-v1", "policyFingerprint": "c" * 64,
        "codeRevision": "abc123", "promptFingerprint": "d" * 64,
        "skillFingerprint": "e" * 64, "modelId": None, "providerId": None,
        "modelConfigFingerprint": None, "runnerVersion": "rule-contract-v1",
        "environment": "ci", "executionMode": "rule-contract",
        "startedAt": datetime(2026, 1, 1), "repetitionCount": 1,
    }
    data.update(updates)
    return data


def test_identical_manifest_reproduces_fingerprint_and_labels_evidence_class():
    first = EvaluationRunManifest.model_validate(manifest())
    second = EvaluationRunManifest.model_validate(manifest())
    assert first.fingerprint() == second.fingerprint()
    assert assertion_evidence_class("rule-contract") == "deterministic-contract"
    assert assertion_evidence_class("workflow-safety") == "sampled-model-stability"


def test_workflow_manifest_records_model_provider_and_repetitions():
    parsed = EvaluationRunManifest.model_validate(manifest(
        executionMode="workflow-safety", runnerVersion="workflow-v1",
        modelId="model-config-1", providerId="provider-1",
        modelConfigFingerprint="f" * 64, repetitionCount=5,
    ))
    assert parsed.repetitionCount == 5
    with pytest.raises(ValidationError, match="model and provider"):
        EvaluationRunManifest.model_validate(manifest(executionMode="workflow-safety"))


@pytest.mark.parametrize("field", ["api_key", "authorization", "hidden_reasoning", "chain_of_thought"])
def test_manifest_rejects_secrets_and_hidden_reasoning(field):
    data = manifest()
    data[field] = "sensitive"
    with pytest.raises(ValidationError):
        EvaluationRunManifest.model_validate(data)
