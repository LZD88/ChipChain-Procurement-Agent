"""Additive application schema; runtime and evaluation permissions stay separate."""

DDL = {
    "prompt_bundle_versions": """CREATE TABLE IF NOT EXISTS prompt_bundle_versions (
        version VARCHAR(64) PRIMARY KEY, fingerprint CHAR(64) NOT NULL UNIQUE,
        schema_version INT NOT NULL DEFAULT 1, roles_json JSON NOT NULL,
        author VARCHAR(32) NOT NULL, change_note VARCHAR(1000) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB""",
    "prompt_release_state": """CREATE TABLE IF NOT EXISTS prompt_release_state (
        environment VARCHAR(32) PRIMARY KEY, revision BIGINT NOT NULL DEFAULT 1,
        state_json JSON NOT NULL, updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB""",
    "prompt_release_events": """CREATE TABLE IF NOT EXISTS prompt_release_events (
        event_id CHAR(36) PRIMARY KEY, environment VARCHAR(32) NOT NULL,
        revision BIGINT NOT NULL, action VARCHAR(32) NOT NULL, actor VARCHAR(32) NOT NULL,
        idempotency_key VARCHAR(128) NOT NULL, request_fingerprint CHAR(64) NOT NULL,
        event_json JSON NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_prompt_event_key(environment,idempotency_key),
        UNIQUE KEY uq_prompt_event_revision(environment,revision)
    ) ENGINE=InnoDB""",
    "prompt_release_gates": """CREATE TABLE IF NOT EXISTS prompt_release_gates (
        gate_id CHAR(36) PRIMARY KEY, candidate_version VARCHAR(64) NOT NULL,
        stable_version VARCHAR(64) NOT NULL, passed BOOLEAN NOT NULL, evidence_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), expires_at DATETIME(6) NOT NULL,
        FOREIGN KEY(candidate_version) REFERENCES prompt_bundle_versions(version),
        FOREIGN KEY(stable_version) REFERENCES prompt_bundle_versions(version)
    ) ENGINE=InnoDB""",
    "thread_prompt_bindings": """CREATE TABLE IF NOT EXISTS thread_prompt_bindings (
        thread_id VARCHAR(64) PRIMARY KEY, username VARCHAR(32) NOT NULL,
        version VARCHAR(64) NOT NULL, binding_json JSON NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        FOREIGN KEY(version) REFERENCES prompt_bundle_versions(version), INDEX idx_prompt_binding_user(username)
    ) ENGINE=InnoDB""",
    "prompt_request_samples": """CREATE TABLE IF NOT EXISTS prompt_request_samples (
        request_id CHAR(36) PRIMARY KEY, thread_id VARCHAR(64) NOT NULL,
        version VARCHAR(64) NULL, release_id CHAR(36) NULL, revision BIGINT NOT NULL,
        cohort VARCHAR(16) NOT NULL, route VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL,
        sample_json JSON NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        finished_at DATETIME(6) NULL,
        FOREIGN KEY(version) REFERENCES prompt_bundle_versions(version),
        INDEX idx_prompt_sample_release(release_id,created_at), INDEX idx_prompt_sample_thread(thread_id,status)
    ) ENGINE=InnoDB""",
    "prompt_management_audit": """CREATE TABLE IF NOT EXISTS prompt_management_audit (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(32) NOT NULL,
        action VARCHAR(40) NOT NULL, outcome VARCHAR(24) NOT NULL, metadata_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB""",
}


async def ensure_immutable_versions(cur):
    for action in ("UPDATE", "DELETE"):
        name = "prompt_bundle_no_" + action.lower()
        await cur.execute("SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s", (name,))
        if not await cur.fetchone():
            await cur.execute(f"CREATE TRIGGER {name} BEFORE {action} ON prompt_bundle_versions "
                              "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='prompt versions are immutable'")
