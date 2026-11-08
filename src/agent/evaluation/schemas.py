"""Strict, non-executable schemas for immutable evaluation suites."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.policies.canonical import canonical_json


ExecutionMode = Literal["rule-contract", "workflow-safety"]
AssertionType = Literal[
    "recommended_part_equals",
    "candidate_eligible",
    "rejection_reason_contains",
    "policy_version_equals",
    "approval_required",
    "order_fields_equal",
    "unsafe_write_count_equals",
    "idempotency_outcome_equals",
    "scenario_status_equals",
]
FORBIDDEN_EXECUTABLE_KEYS = frozenset({
    "code", "script", "python", "javascript", "command", "shell", "exec", "eval",
})


def _reject_executable(value: Any, path: str = "suite") -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_EXECUTABLE_KEYS:
                raise ValueError(f"executable content is forbidden: {path}.{key}")
            _reject_executable(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_executable(item, f"{path}[{index}]")
    return value


class EvaluationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: AssertionType
    expected: Any
    candidatePartId: int | None = None
    reason: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def required_context(self):
        if self.type in {"candidate_eligible", "rejection_reason_contains"} and self.candidatePartId is None:
            raise ValueError(f"{self.type} requires candidatePartId")
        if self.type == "rejection_reason_contains" and not self.reason:
            raise ValueError("rejection_reason_contains requires reason")
        return self


class EvaluationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    title: str = Field(min_length=1, max_length=200)
    executionMode: ExecutionMode
    fixture: dict[str, Any]
    assertions: tuple[EvaluationAssertion, ...] = Field(min_length=1)
    timeoutSeconds: int = Field(default=30, ge=1, le=300)
    repetitions: int = Field(default=1, ge=1, le=20)

    @field_validator("fixture", mode="before")
    @classmethod
    def no_executable_fixture(cls, value):
        return _reject_executable(value, "scenario.fixture")


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suiteId: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    title: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=2, max_length=32)
    fixtureVersion: str = Field(min_length=1, max_length=64)
    scenarios: tuple[EvaluationScenario, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_scenario_ids(self):
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evaluation scenario id")
        return self

    def content_fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump()).encode("utf-8")).hexdigest()


class PublishedSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: EvaluationSuite
    version: int = Field(ge=1)
    contentFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def fingerprint_matches(self):
        if self.contentFingerprint != self.suite.content_fingerprint():
            raise ValueError("published suite content fingerprint mismatch")
        return self

    def assert_unchanged(self, candidate: EvaluationSuite) -> None:
        if candidate.content_fingerprint() != self.contentFingerprint:
            raise ValueError("published suite version is immutable; publish a new version")


def publish_suite(suite: EvaluationSuite, *, version: int) -> PublishedSuite:
    return PublishedSuite(
        suite=suite,
        version=version,
        contentFingerprint=suite.content_fingerprint(),
    )
