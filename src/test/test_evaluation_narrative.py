"""Published evaluation numbers stay traceable and honestly scoped."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_top_level_narrative_links_every_measured_row_to_export_and_command():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("### 可复现评测结果（离线 / 测试证据）", 1)[1].split("\n## ", 1)[0]
    for link in (
        "docs/evaluation-results/rule-baseline.md",
        "docs/evaluation-results/rule-candidate.md",
        "docs/evaluation-results/workflow-safety.md",
        "docs/evaluation-results/rule-comparison.json",
    ):
        assert f"]({link})" in section
        assert (ROOT / link).is_file()

    measured_rows = [line for line in section.splitlines() if line.startswith("| `")]
    assert len(measured_rows) == 6
    assert all("](" in line for line in measured_rows)
    assert "$env:EVALUATION_E2E_OUTPUT_DIR" in section
    assert "src/test/test_evaluation_end_to_end_mysql.py" in section
    assert "不是线上准确率、实际采购节省、生产吞吐" in section
    assert "或业务 ROI" in section


def test_published_report_values_match_the_narrative():
    rule = (ROOT / "docs/evaluation-results/rule-baseline.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs/evaluation-results/workflow-safety.md").read_text(encoding="utf-8")
    comparison = (ROOT / "docs/evaluation-results/rule-comparison.json").read_text(encoding="utf-8")
    for row in (
        "| hard_constraint_pass_rate | 1.0 | 10 | 10 |",
        "| expected_recommendation_agreement | 1.0 | 3 | 3 |",
        "| scenario_completion_rate | 1.0 | 6 | 6 |",
    ):
        assert row in rule
    for row in (
        "| unsafe_write_prevention | 1.0 | 12 | 12 |",
        "| approval_boundary_compliance | 1.0 | 6 | 6 |",
        "| scenario_completion_rate | 1.0 | 12 | 12 |",
    ):
        assert row in workflow
    assert '"passed": true' in comparison
    assert '"failures": []' in comparison
