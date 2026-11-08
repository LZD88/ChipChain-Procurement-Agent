import re

import pytest

from agent.mysql_storage import APPLICATION_DDL, _ensure_application_tables


BASE_TABLES = {
    "app_users",
    "auth_sessions",
    "chat_projects",
    "chat_threads",
    "session_display_messages",
    "sandbox_registry",
}
DECISION_TABLES = {
    "procurement_decisions",
    "procurement_decision_events",
    "procurement_decision_claims",
    "procurement_replays",
    "app_capability_grants",
    "decision_audit_actions",
    "procurement_decision_retention_holds",
    "procurement_decision_tombstones",
}
EVALUATION_TABLES = {
    "evaluation_suite_versions", "evaluation_runs", "evaluation_cases",
    "evaluation_assertion_results", "evaluation_baselines", "evaluation_audit_events",
}


class SchemaCursor:
    def __init__(self, existing=()):
        self.tables = set(existing)
        self.created = []
        self._lookup = None

    async def execute(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            self._lookup = params[0]
            return
        match = re.search(r"CREATE TABLE IF NOT EXISTS\s+([A-Za-z0-9_]+)", sql)
        if match:
            table = match.group(1)
            self.tables.add(table)
            self.created.append(table)

    async def fetchone(self):
        return {"exists": 1} if self._lookup in self.tables else None


@pytest.mark.asyncio
async def test_fresh_schema_creates_base_and_decision_tables_in_declared_order():
    cursor = SchemaCursor()
    await _ensure_application_tables(cursor)

    assert cursor.created == list(APPLICATION_DDL)
    assert BASE_TABLES | DECISION_TABLES | EVALUATION_TABLES <= cursor.tables
    assert cursor.created.index("app_users") < cursor.created.index("procurement_decisions")
    assert cursor.created.index("procurement_decisions") < cursor.created.index(
        "procurement_decision_events"
    )


@pytest.mark.asyncio
async def test_existing_release_schema_adds_only_new_decision_tables():
    cursor = SchemaCursor(existing=BASE_TABLES)
    await _ensure_application_tables(cursor)

    assert set(cursor.created) == DECISION_TABLES | EVALUATION_TABLES
    assert not BASE_TABLES.intersection(cursor.created)


def test_decision_schema_contains_required_integrity_constraints():
    decisions = APPLICATION_DDL["procurement_decisions"]
    claims = APPLICATION_DDL["procurement_decision_claims"]
    events = APPLICATION_DDL["procurement_decision_events"]

    assert "PRIMARY KEY (decision_id, owner_user_id)" in decisions
    assert "evidence_fingerprint CHAR(64) NOT NULL" in decisions
    assert "PRIMARY KEY (decision_id, owner_user_id)" in claims
    assert "UNIQUE KEY uq_decision_idempotency" in claims
    assert "event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY" in events


def test_evaluation_schema_has_immutable_case_and_baseline_keys():
    assert "PRIMARY KEY (suite_id, version)" in APPLICATION_DDL["evaluation_suite_versions"]
    assert "PRIMARY KEY (run_id, scenario_id, repetition)" in APPLICATION_DDL["evaluation_cases"]
    assert "PRIMARY KEY (suite_id, execution_mode)" in APPLICATION_DDL["evaluation_baselines"]
