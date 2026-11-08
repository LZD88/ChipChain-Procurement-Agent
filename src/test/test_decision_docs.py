from pathlib import Path

from agent.authorization import KNOWN_CAPABILITIES
from agent.mysql_storage import APPLICATION_DDL


ROOT = Path(__file__).resolve().parents[2]


def test_documented_decision_tables_and_configuration_match_code():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "src/docs/project-design.md").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    decision_tables = [name for name in APPLICATION_DDL if name not in {
        "app_users", "auth_sessions", "chat_projects", "chat_threads",
        "session_display_messages", "sandbox_registry",
    }]
    for table in decision_tables:
        assert table in readme
        assert table in design
    for capability in KNOWN_CAPABILITIES:
        assert capability in readme
        assert capability in env
    for key in (
        "DECISION_EVIDENCE_RETENTION_DAYS",
        "APP_CAPABILITY_BOOTSTRAP",
        "EVIDENCE_SECRET_FIELDS",
    ):
        assert key in env
