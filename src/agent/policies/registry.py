"""Registry and lifecycle controls for reproducible procurement policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


PolicyEvaluator = Callable[..., dict[str, Any]]


class PolicyNotFoundError(KeyError):
    pass


class PolicyUnavailableError(RuntimeError):
    pass


class PolicyReferencedError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyDefinition:
    version: str
    evaluator: PolicyEvaluator
    replay_supported: bool = True
    description: str = ""


class PolicyRegistry:
    def __init__(self) -> None:
        self._policies: dict[str, PolicyDefinition] = {}
        self._references: dict[str, set[str]] = {}
        self._retired: set[str] = set()

    def register(self, definition: PolicyDefinition) -> None:
        existing = self._policies.get(definition.version)
        if existing is not None and existing != definition:
            raise ValueError(f"policy version already registered: {definition.version}")
        self._policies[definition.version] = definition
        self._references.setdefault(definition.version, set())

    def get(self, version: str) -> PolicyDefinition:
        definition = self._policies.get(version)
        if definition is None:
            raise PolicyNotFoundError(f"unknown procurement policy version: {version}")
        if version in self._retired:
            raise PolicyUnavailableError(f"procurement policy version is retired: {version}")
        return definition

    def get_for_replay(self, version: str) -> PolicyDefinition:
        definition = self.get(version)
        if not definition.replay_supported:
            raise PolicyUnavailableError(
                f"procurement policy version does not support replay: {version}"
            )
        return definition

    def list(self, *, include_retired: bool = False) -> tuple[PolicyDefinition, ...]:
        versions = sorted(self._policies)
        if not include_retired:
            versions = [version for version in versions if version not in self._retired]
        return tuple(self._policies[version] for version in versions)

    def add_reference(self, version: str, reference_id: str) -> None:
        self.get(version)
        if not reference_id.strip():
            raise ValueError("policy reference id is required")
        self._references[version].add(reference_id)

    def remove_reference(self, version: str, reference_id: str) -> None:
        if version not in self._policies:
            raise PolicyNotFoundError(f"unknown procurement policy version: {version}")
        self._references[version].discard(reference_id)

    def references(self, version: str) -> tuple[str, ...]:
        if version not in self._policies:
            raise PolicyNotFoundError(f"unknown procurement policy version: {version}")
        return tuple(sorted(self._references[version]))

    def retire(self, version: str) -> None:
        self.get(version)
        references = self.references(version)
        if references:
            raise PolicyReferencedError(
                f"policy {version} is still referenced by: {', '.join(references)}"
            )
        self._retired.add(version)


_REGISTRY = PolicyRegistry()


def register_policy(definition: PolicyDefinition) -> None:
    _REGISTRY.register(definition)


def get_policy(version: str) -> PolicyDefinition:
    return _REGISTRY.get(version)


def get_replay_policy(version: str) -> PolicyDefinition:
    return _REGISTRY.get_for_replay(version)


def list_policies() -> tuple[PolicyDefinition, ...]:
    return _REGISTRY.list()


def add_policy_reference(version: str, reference_id: str) -> None:
    _REGISTRY.add_reference(version, reference_id)


def remove_policy_reference(version: str, reference_id: str) -> None:
    _REGISTRY.remove_reference(version, reference_id)


def retire_policy(version: str) -> None:
    _REGISTRY.retire(version)
