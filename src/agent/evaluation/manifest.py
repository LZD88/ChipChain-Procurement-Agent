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
    executionMode: Literal["rule-contract", "workflow-safety", "prompt-candidate", "experiment"]
    promptVersion: str | None = None
    deploymentFingerprint: str | None = None
    startedAt: datetime
    repetitionCount: int = Field(ge=1, le=20)
    experimentId: str | None = Field(None, max_length=36)
    experimentArm: Literal["baseline", "candidate"] | None = None
    skillVariant: Literal["current", "without-skills"] = "current"
    failureCaseId: str | None = Field(None, max_length=36)
    inputPricePer1k: float | None = Field(None, ge=0, allow_inf_nan=False)
    outputPricePer1k: float | None = Field(None, ge=0, allow_inf_nan=False)
    priceCurrency: str | None = Field(None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="before")
    @classmethod
    def reject_secrets_and_reasoning(cls, value):
        reject_secret_bearing(value, "evaluation.manifest")
        return value

    @model_validator(mode="after")
    def model_mode_requirements(self):
        if self.executionMode in {"workflow-safety", "prompt-candidate"} and (not self.modelId or not self.providerId):
            raise ValueError("workflow-safety manifest requires model and provider identifiers")
        if self.executionMode == "rule-contract" and self.repetitionCount != 1:
            raise ValueError("rule-contract repetitionCount must be one")
        if self.executionMode == "prompt-candidate" and (not self.promptVersion or not self.deploymentFingerprint or self.repetitionCount < 3):
            raise ValueError("prompt-candidate requires a version, deployment fingerprint and at least three repetitions")
        if self.executionMode == "experiment" and (not self.promptVersion or not self.deploymentFingerprint or not self.modelConfigFingerprint or not self.modelId):
            raise ValueError("experiment requires server-owned prompt, deployment and model configuration")
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode("utf-8")).hexdigest()


def assertion_evidence_class(execution_mode: str) -> str:
    return "deterministic-contract" if execution_mode == "rule-contract" else "sampled-model-stability"
