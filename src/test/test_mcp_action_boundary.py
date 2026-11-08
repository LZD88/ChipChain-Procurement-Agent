import inspect

import pytest

from agent.invocation_context import InvocationContextError, verify_invocation
from agent.main_agent import create_main_agent
from mcp_server.decision_db import DecisionDBSettings
from mcp_server.tools import inventory_tools, order_tools, parts_tools, procurement_tools, suppliers_tools


def test_unsigned_action_context_is_rejected_but_read_modules_do_not_require_it():
    with pytest.raises(InvocationContextError, match="required"):
        verify_invocation(None, audience="procurement_compare", secret=b"x" * 32)

    for module in (suppliers_tools, parts_tools, inventory_tools):
        assert "verify_invocation" not in inspect.getsource(module)
    assert "verify_invocation" in inspect.getsource(procurement_tools)
    assert "verify_invocation" in inspect.getsource(order_tools)


def test_agent_factory_wraps_only_actionable_mcp_tools():
    source = inspect.getsource(create_main_agent)
    assert '{"procurement_compare", "order_create"}' in source
    assert "wrap_signed_mcp_tool" in source


def test_decision_database_defaults_to_dedicated_account(monkeypatch):
    for key in ("DECISION_DB_USER", "DECISION_DB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    settings = DecisionDBSettings.from_env()
    assert settings.user == "chipchain_mcp"
    assert settings.user != "root"
