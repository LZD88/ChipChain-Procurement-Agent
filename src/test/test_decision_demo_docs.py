from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_evidence_docs_cover_review_flow_and_exact_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    api = (ROOT / "docs" / "decision-evidence-api.md").read_text(encoding="utf-8")
    demo = (ROOT / "docs" / "procurement-evidence-demo.md").read_text(encoding="utf-8")
    for required in ("旧", "临时 ID", "RECONCILIATION_REQUIRED", "模拟", "不触发审批"):
        assert required in readme + api + demo
    for step in ("procurement_compare", "批准或拒绝", "/api/decisions", "/replay"):
        assert step in demo
    assert "npm --prefix frontend test" in demo
    assert "npm --prefix frontend run build" in demo
    assert "ORDER_RECONCILIATION_RETRY_IF_ABSENT" in api
