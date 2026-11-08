import builtins
from copy import deepcopy

import pytest

from agent.evaluation.rule_runner import load_default_inputs, run_rule_contract


def test_default_rule_contract_is_deterministic_green_and_offline(monkeypatch):
    def forbidden_import(name, *args, **kwargs):
        if name.startswith(("httpx", "fastmcp", "openai")):
            raise AssertionError(f"network/model dependency imported: {name}")
        return original_import(name, *args, **kwargs)

    original_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", forbidden_import)
    first = run_rule_contract()
    second = run_rule_contract()
    assert first == second
    assert first["status"] == "COMPLETED"
    assert len(first["cases"]) == 6
    assert all(case["status"] == "PASSED" for case in first["cases"])
    assert all(assertion["status"] == "PASSED" for case in first["cases"] for assertion in case["assertions"])


def test_rule_contract_rejects_secret_bearing_fixture_before_execution():
    suite, fixtures = load_default_inputs()
    tainted = deepcopy(fixtures)
    tainted["successful-recommendation"]["api_token"] = "must-not-be-read-or-persisted"
    with pytest.raises(ValueError, match=r"evaluation\.fixtures.*api_token"):
        run_rule_contract(suite, tainted)
