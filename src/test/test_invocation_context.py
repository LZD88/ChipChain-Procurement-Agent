from copy import deepcopy

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from agent.invocation_context import (
    InvocationContextError,
    NonceReplayGuard,
    sign_invocation,
    verify_invocation,
)
from agent.tools.scoped_mcp import wrap_signed_mcp_tool


SECRET = b"test-signing-secret-at-least-32-bytes-long"


def signed(audience="procurement_compare", now=1000, nonce="nonce-1"):
    return sign_invocation(
        user_id="alice", thread_id="thread-1", audience=audience,
        correlation_id="REQ-1", trace_id="TRC-1", now=now, ttl_seconds=60,
        nonce=nonce, secret=SECRET,
    )


def test_valid_signature_and_identity_round_trip():
    claims = verify_invocation(signed(), audience="procurement_compare", now=1020, secret=SECRET)
    assert claims["sub"] == "alice"
    assert claims["thread"] == "thread-1"
    assert claims["trace"] == "TRC-1"


def test_forged_expired_wrong_audience_and_missing_envelopes_are_rejected():
    forged = deepcopy(signed())
    forged["sub"] = "mallory"
    with pytest.raises(InvocationContextError, match="signature"):
        verify_invocation(forged, audience="procurement_compare", now=1020, secret=SECRET)
    with pytest.raises(InvocationContextError, match="expired"):
        verify_invocation(signed(), audience="procurement_compare", now=1061, secret=SECRET)
    with pytest.raises(InvocationContextError, match="audience"):
        verify_invocation(signed(), audience="order_create", now=1020, secret=SECRET)
    with pytest.raises(InvocationContextError, match="required"):
        verify_invocation(None, audience="procurement_compare", now=1020, secret=SECRET)


def test_replayed_nonce_is_rejected():
    guard = NonceReplayGuard(clock=lambda: 1020)
    envelope = signed()
    verify_invocation(envelope, audience="procurement_compare", now=1020, secret=SECRET, replay_guard=guard)
    with pytest.raises(InvocationContextError, match="already used"):
        verify_invocation(envelope, audience="procurement_compare", now=1020, secret=SECRET, replay_guard=guard)


@pytest.mark.asyncio
async def test_signed_wrapper_hides_model_supplied_identity(monkeypatch):
    monkeypatch.setenv("MCP_INVOCATION_SIGNING_SECRET", SECRET.decode())

    class Args(BaseModel):
        part_name: str
        invocation_envelope: dict

    received = {}

    async def receiver(part_name: str, invocation_envelope: dict):
        received.update(invocation_envelope)
        return part_name

    raw = StructuredTool.from_function(
        coroutine=receiver, name="procurement_compare", description="compare", args_schema=Args,
    )
    wrapped = wrap_signed_mcp_tool(raw)
    assert "invocation_envelope" not in wrapped.tool_call_schema.model_json_schema()["properties"]
    result = await wrapped.ainvoke(
        {"part_name": "sensor"},
        config={"configurable": {"user_id": "alice", "thread_id": "thread-1"}},
    )
    assert result == "sensor"
    assert received["sub"] == "alice"
    assert received["aud"] == "procurement_compare"
    assert "sig" in received
