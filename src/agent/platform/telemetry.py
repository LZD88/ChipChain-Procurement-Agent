"""Optional asynchronous Langfuse tracing; local releases are authoritative.

The SDK queues telemetry in its background exporter. Prompt synchronization runs
outside chat requests. This module never configures the global OTel provider.
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import hashlib
import os
import re
from uuid import uuid4

from .runtime import is_enabled, runtime_configuration


def redact(data, **_kwargs):
    if isinstance(data, dict):
        return {key: "[REDACTED]" if re.search(r"password|secret|api.?key|authorization|cookie|access.?token", str(key), re.I)
                else redact(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact(value) for value in data]
    if isinstance(data, str):
        value = re.sub(r"\b(?:sk-|pk-lf-|sk-lf-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}", "[REDACTED]", data)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        return re.sub(r'''(?i)\b(password|api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|密码|密钥)\s*[:=：]\s*["']?[^\s,;"']+''',
                      r"\1=[REDACTED]", value)
    return data


def prompt_role(bundle, messages):
    # DeepAgents uses typed content blocks; str(list) escapes newlines and
    # would silently lose the main Agent's immutable prompt association.
    def content_text(content):
        if isinstance(content, str):
            return content
        return "\n".join(block.get("text", "") if isinstance(block, dict) else str(block)
                         for block in content)
    text = "\n".join(content_text(message.content) for group in messages
                     for message in group if message.type == "system")
    matches = [role for role, prompt in bundle.entries if prompt in text]
    return matches[0] if len(matches) == 1 else None


class TraceHandle:
    def __init__(self, integration, root=None, handler=None, name=None):
        self.integration, self.root, self.handler = integration, root, handler
        self.name = name
        self.trace_id = root.trace_id if root else None
        self.closed = False

    def finish(self, outcome, *, output=None, metadata=None, score=None):
        if self.closed or self.root is None:
            return
        self.closed = True
        try:
            self.root.update(output=output, metadata={**(metadata or {}), "outcome": outcome},
                             level="ERROR" if outcome == "failure" else "DEFAULT")
            if self.name:
                self.root.update_trace(name=self.name, output=output)
            if score is not None:
                self.integration.client.create_score(trace_id=self.trace_id, name="case_passed",
                                                     value=float(score), data_type="NUMERIC")
            self.root.end()
        except Exception:
            self.integration.status = "degraded"


class LangfuseIntegration:
    def __init__(self):
        self.client = None
        self.status = "disabled"
        self.prompts = {}
        self.error_type = None

    def initialize(self):
        if not is_enabled("LANGFUSE_ENABLED"):
            return
        try:
            from langfuse import Langfuse
            from opentelemetry.sdk.trace import TracerProvider
            if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
                raise ValueError("Langfuse credentials are not configured")
            self.client = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                base_url=os.getenv("LANGFUSE_BASE_URL", "http://localhost:13000"),
                tracer_provider=TracerProvider(), timeout=3, flush_at=32, flush_interval=2,
                environment="local", mask=redact)
            self.status = "configured"
        except Exception as exc:
            self.status, self.error_type = "degraded", type(exc).__name__

    def sync_bundle(self, bundle):
        if self.client is None:
            return
        try:
            for role, _text in bundle.entries:
                key = (bundle.fingerprint, role)
                if key in self.prompts:
                    continue
                # A distinct immutable name prevents a remote 'latest' label
                # from changing which prompt a local version actually uses.
                name = f"chipchain/{bundle.version}/{role}"
                try:
                    prompt = self.client.get_prompt(name, version=1, cache_ttl_seconds=3600)
                    if prompt.prompt != bundle.prompt(role):
                        raise ValueError("Langfuse prompt differs from authoritative local bundle")
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404:
                        raise
                    prompt = self.client.create_prompt(name=name, prompt=bundle.prompt(role),
                        labels=["project-owned"], tags=["chipchain", bundle.version],
                        config={"localVersion": bundle.version, "fingerprint": bundle.fingerprint,
                                "role": role, "authority": "project-mysql"})
                self.prompts[key] = prompt
            self.status, self.error_type = "connected", None
        except Exception as exc:
            self.status, self.error_type = "degraded", type(exc).__name__

    def begin(self, *, name, request_id, thread_id, user_id, bundle=None, metadata=None):
        if self.client is None:
            return TraceHandle(self)
        try:
            from langfuse.langchain import CallbackHandler
            integration = self
            class PromptCallback(CallbackHandler):
                def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
                    super().on_chat_model_start(serialized, messages, run_id=run_id, **kwargs)
                    if bundle is None or run_id not in self.runs:
                        return
                    try:
                        role = prompt_role(bundle, messages)
                        if role:
                            prompt = integration.prompts.get((bundle.fingerprint, role))
                            if prompt:
                                self.runs[run_id].update(prompt=prompt)
                    except Exception:
                        integration.status = "degraded"

            trace_id = hashlib.sha256(request_id.encode()).hexdigest()[:32]
            snapshot = runtime_configuration.snapshot()
            root = self.client.start_observation(name=name, trace_context={"trace_id": trace_id},
                metadata={"modelConfigRevision": snapshot.revision,
                          "modelConfigFingerprint": snapshot.fingerprint,
                          **(metadata or {}), "requestId": request_id,
                          "promptVersion": bundle.version if bundle else None,
                          "promptFingerprint": bundle.fingerprint if bundle else None})
            root.update_trace(name=name, session_id=thread_id, user_id=user_id,
                              tags=["chipchain", "evaluation" if name.startswith("evaluation") else "chat"])
            handler = PromptCallback(public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                                    trace_context={"trace_id": trace_id, "parent_span_id": root.id})
            return TraceHandle(self, root, handler, name=name)
        except Exception as exc:
            self.status, self.error_type = "degraded", type(exc).__name__
            return TraceHandle(self)

    def public(self):
        return {"status": self.status, "errorType": self.error_type, "syncedPrompts": len(self.prompts),
                "url": os.getenv("LANGFUSE_BASE_URL", "http://localhost:13000")}

    def shutdown(self):
        if self.client:
            self.client.shutdown()


langfuse_integration = LangfuseIntegration()
EVALUATION_RUN = ContextVar("langfuse_evaluation_run", default=None)
CASE_TRACE = ContextVar("langfuse_evaluation_case", default=None)


def trace_evaluation_case(function):
    @wraps(function)
    async def wrapped(bundle, scenario, *args, **kwargs):
        handle = langfuse_integration.begin(name="evaluation.case", request_id=str(uuid4()),
            thread_id=EVALUATION_RUN.get() or "isolated-evaluation", user_id="evaluation-worker", bundle=bundle,
            metadata={"evaluationRunId": EVALUATION_RUN.get(), "scenarioId": scenario.id})
        token = CASE_TRACE.set(handle)
        result = None
        try:
            result = await function(bundle, scenario, *args, **kwargs)
            if handle.trace_id:
                result["result"]["langfuseTraceId"] = handle.trace_id
            return result
        finally:
            passed = result is not None and result.get("status") == "PASSED"
            handle.finish("success" if passed else "failure", score=passed,
                          metadata={"status": result.get("status") if result else "ERROR"},
                          output=(result or {}).get("assertions"))
            CASE_TRACE.reset(token)
    return wrapped
