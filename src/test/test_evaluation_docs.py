import json
from pathlib import Path

from agent.evaluation import cli


ROOT = Path(__file__).resolve().parents[2]


def test_evaluation_documentation_covers_contracts_and_exact_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "evaluation-system.md").read_text(encoding="utf-8")
    assert "docs/evaluation-system.md" in readme
    for required in (
        "客观", "rule-contract", "workflow-safety", "fixture", "分子", "分母",
        "NOT_APPLICABLE", "重复次数", "基线", "安全回归", "evaluation:view",
        "evaluation:maintain", "INCOMPLETE", "不需要模型、MCP、ERP 或网络",
    ):
        assert required in guide
    assert "python.exe -m agent.evaluation.cli validate-suite" in guide
    assert "python.exe -m agent.evaluation.cli run-rule-contract" in guide
    assert "--threshold unsafe_write_prevention=1" in guide


def test_documented_default_suite_validation_and_rule_contract_run(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    validation = tmp_path / "validation.json"
    report = tmp_path / "evaluation-rule.json"
    assert cli.main([
        "validate-suite", "src/evaluation/suites/default-v1.json",
        "--output", str(validation),
    ]) == 0
    assert cli.main(["run-rule-contract", "--output", str(report)]) == 0
    assert json.loads(validation.read_text(encoding="utf-8"))["scenarioCount"] == 10
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["status"] == "COMPLETED"
    assert result["completionLabel"] == "COMPLETE"
    assert all(case["status"] == "PASSED" for case in result["cases"])
