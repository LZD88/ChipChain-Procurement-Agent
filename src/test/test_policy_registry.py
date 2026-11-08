import pytest

from agent.policies.registry import (
    PolicyDefinition,
    PolicyNotFoundError,
    PolicyReferencedError,
    PolicyRegistry,
    PolicyUnavailableError,
)


def evaluator(**_kwargs):
    return {"policyVersion": "v1"}


def test_referenced_policy_cannot_be_retired():
    registry = PolicyRegistry()
    registry.register(PolicyDefinition("v1", evaluator))
    registry.add_reference("v1", "decision:DEC-1")

    with pytest.raises(PolicyReferencedError, match="decision:DEC-1"):
        registry.retire("v1")

    registry.remove_reference("v1", "decision:DEC-1")
    registry.retire("v1")
    with pytest.raises(PolicyUnavailableError, match="retired"):
        registry.get("v1")


def test_unknown_version_is_never_replaced_by_registered_version():
    registry = PolicyRegistry()
    current = PolicyDefinition("current", evaluator)
    registry.register(current)

    with pytest.raises(PolicyNotFoundError, match="missing"):
        registry.get("missing")
    assert registry.get("current") is current


def test_non_replayable_policy_is_explicitly_unavailable_for_replay():
    registry = PolicyRegistry()
    registry.register(PolicyDefinition("online-only", evaluator, replay_supported=False))

    assert registry.get("online-only").version == "online-only"
    with pytest.raises(PolicyUnavailableError, match="does not support replay"):
        registry.get_for_replay("online-only")
