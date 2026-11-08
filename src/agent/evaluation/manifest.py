"""Reproducible, secret-free evaluation run manifests."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.evidence_sanitizer import reject_secret_bearing
from agent.policies.canonical import canonical_json


class EvaluationRunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suiteId: str
    suiteVersion: int = Field(ge=1)
    suiteFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixtureVersion: str
    fixtureFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policyVersion: str
    policyFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    codeRevision: str
    promptFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    skillFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelId: str | None = None
    providerId: str | None = None
    modelConfigFingerprint: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    runnerVersion: str
    environment: Literal["local", "ci", "staging", "isolated-test"]
    executionMode: Literal["rule-contract", "workflow-safety"]
    startedAt: datetime
    repetitionCount: int = Field(ge=1, le=20)

    @model_validator(mode="before")
    @classmethod
    def reject_secrets_and_reasoning(cls, value):
        reject_secret_bearing(value, "evaluation.manifest")
        return value

    @model_validator(mode="after")
    def model_mode_requirements(self):
        if self.executionMode == "workflow-safety" and (not self.modelId or not self.providerId):
            raise ValueError("workflow-safety manifest requires model and provider identifiers")
        if self.executionMode == "rule-contract" and self.repetitionCount != 1:
            raise ValueError("rule-contract repetitionCount must be one")
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode("utf-8")).hexdigest()


def assertion_evidence_class(execution_mode: str) -> str:
    return "deterministic-contract" if execution_mode == "rule-contract" else "sampled-model-stability"
