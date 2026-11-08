from copy import deepcopy

import pytest
from pydantic import ValidationError

from agent.evaluation.schemas import EvaluationSuite, publish_suite


def payload():
    return {
        "suiteId": "default-procurement",
        "title": "默认采购契约",
        "owner": "alice",
        "fixtureVersion": "fixtures-v1",
        "scenarios": [{
            "id": "success",
            "title": "正常推荐",
            "executionMode": "rule-contract",
            "fixture": {"request": {"partName": "传感器", "quantity": 10}},
            "assertions": [{"type": "recommended_part_equals", "expected": 7}],
        }],
    }


def test_publish_fingerprint_is_stable_and_published_version_is_immutable():
    suite = EvaluationSuite.model_validate(payload())
    published = publish_suite(suite, version=1)
    assert len(published.contentFingerprint) == 64
    assert published.contentFingerprint == publish_suite(
        EvaluationSuite.model_validate(payload()), version=1,
    ).contentFingerprint
    changed = payload()
    changed["scenarios"][0]["title"] = "被修改"
    with pytest.raises(ValueError, match="immutable"):
        published.assert_unchanged(EvaluationSuite.model_validate(changed))


@pytest.mark.parametrize("key", ["code", "script", "python", "command", "exec"])
def test_executable_content_is_rejected(key):
    data = payload()
    data["scenarios"][0]["fixture"][key] = "print('unsafe')"
    with pytest.raises(ValidationError, match="executable content"):
        EvaluationSuite.model_validate(data)


def test_unknown_assertions_duplicate_ids_and_invalid_modes_are_rejected():
    unknown = payload()
    unknown["scenarios"][0]["assertions"][0]["type"] = "run_python"
    with pytest.raises(ValidationError):
        EvaluationSuite.model_validate(unknown)

    duplicate = payload()
    duplicate["scenarios"].append(deepcopy(duplicate["scenarios"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        EvaluationSuite.model_validate(duplicate)

    invalid_mode = payload()
    invalid_mode["scenarios"][0]["executionMode"] = "production-write"
    with pytest.raises(ValidationError):
        EvaluationSuite.model_validate(invalid_mode)
