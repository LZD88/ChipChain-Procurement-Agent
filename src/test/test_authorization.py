from contextlib import asynccontextmanager

import pytest

from agent.authorization import (
    DECISION_AUDIT,
    EVALUATION_MAINTAIN,
    EVALUATION_VIEW,
    bootstrap_grants_from_env,
    can_read_decision,
    require_evaluation_maintain,
    require_evaluation_view,
)


class Grants:
    def __init__(self):
        self.values = set()

    async def has_capability(self, username, capability):
        return (username, capability) in self.values


@pytest.mark.asyncio
async def test_owner_and_auditor_access_do_not_disclose_cross_user_by_default():
    storage = Grants()
    assert await can_read_decision(storage, actor="alice", owner="alice") is True
    assert await can_read_decision(storage, actor="bob", owner="alice") is False
    storage.values.add(("auditor", DECISION_AUDIT))
    assert await can_read_decision(storage, actor="auditor", owner="alice") is True


@pytest.mark.asyncio
async def test_evaluation_viewer_maintainer_and_revocation():
    storage = Grants()
    storage.values.add(("viewer", EVALUATION_VIEW))
    storage.values.add(("maintainer", EVALUATION_MAINTAIN))
    assert await require_evaluation_view(storage, "viewer") is True
    assert await require_evaluation_maintain(storage, "viewer") is False
    assert await require_evaluation_view(storage, "maintainer") is True
    assert await require_evaluation_maintain(storage, "maintainer") is True
    storage.values.remove(("viewer", EVALUATION_VIEW))
    assert await require_evaluation_view(storage, "viewer") is False


def test_bootstrap_is_deny_by_default_and_requires_known_capabilities(monkeypatch):
    monkeypatch.delenv("APP_CAPABILITY_BOOTSTRAP", raising=False)
    assert bootstrap_grants_from_env() == {}
    monkeypatch.setenv(
        "APP_CAPABILITY_BOOTSTRAP",
        "Auditor=decision:audit|evaluation:view,ops=evaluation:maintain",
    )
    assert bootstrap_grants_from_env() == {
        "auditor": {DECISION_AUDIT, EVALUATION_VIEW},
        "ops": {EVALUATION_MAINTAIN},
    }
    monkeypatch.setenv("APP_CAPABILITY_BOOTSTRAP", "alice=admin")
    with pytest.raises(ValueError, match="unknown application capability"):
        bootstrap_grants_from_env()
