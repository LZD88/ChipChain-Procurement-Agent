from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from agent.tools.scoped_mcp import wrap_scoped_mcp_tool


class PrivateCompareArgs(BaseModel):
    part_name: str = Field(description="Business argument visible to the model")
    quantity: int
    invocation_envelope: dict = Field(description="Server-only signed context")


@pytest.mark.asyncio
async def test_scoped_wrapper_hides_and_injects_invocation_envelope():
    received = {}

    async def fake_mcp_receiver(
        part_name: str,
        quantity: int,
        invocation_envelope: dict,
    ) -> dict:
        received.update({
            "part_name": part_name,
            "quantity": quantity,
            "invocation_envelope": invocation_envelope,
        })
        return {"ok": True}

    raw_tool = StructuredTool.from_function(
        coroutine=fake_mcp_receiver,
        name="procurement_compare",
        description="Compare procurement candidates",
        args_schema=PrivateCompareArgs,
    )
    wrapped = wrap_scoped_mcp_tool(
        raw_tool,
        lambda config: config["configurable"]["trusted_envelope"],
    )

    model_schema = wrapped.tool_call_schema.model_json_schema()
    assert set(model_schema["properties"]) == {"part_name", "quantity"}
    assert "invocation_envelope" not in model_schema["properties"]

    result = await wrapped.ainvoke(
        {"part_name": "六轴惯性传感器", "quantity": 500},
        config={"configurable": {"trusted_envelope": {"sub": "alice", "aud": "procurement_compare"}}},
    )

    assert result == {"ok": True}
    assert received == {
        "part_name": "六轴惯性传感器",
        "quantity": 500,
        "invocation_envelope": {"sub": "alice", "aud": "procurement_compare"},
    }


def test_scoped_wrapper_requires_internal_field_on_raw_tool():
    class PublicOnlyArgs(BaseModel):
        value: str

    async def receiver(value: str) -> str:
        return value

    raw_tool = StructuredTool.from_function(
        coroutine=receiver,
        name="read_only_tool",
        description="Read only",
        args_schema=PublicOnlyArgs,
    )

    with pytest.raises(ValueError, match="does not declare internal field"):
        wrap_scoped_mcp_tool(raw_tool, lambda _config: {"sub": "alice"})


@pytest.mark.asyncio
async def test_scoped_wrapper_supports_mcp_json_schema_and_injects_envelope():
    received = {}

    async def fake_mcp_receiver(**arguments) -> dict:
        received.update(arguments)
        return {"ok": True}

    json_schema = {
        "type": "object",
        "properties": {
            "part_name": {"type": "string", "description": "Part to source"},
            "quantity": {"type": "integer", "minimum": 1},
            "budget": {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None},
            "max_lead_time_days": {"type": "integer", "default": 14},
            "invocation_envelope": {"type": "object"},
        },
        "required": ["part_name", "quantity", "invocation_envelope"],
        "additionalProperties": False,
    }
    raw_tool = StructuredTool.from_function(
        coroutine=fake_mcp_receiver,
        name="procurement_compare",
        description="Compare procurement candidates",
        args_schema=json_schema,
        infer_schema=False,
    )
    wrapped = wrap_scoped_mcp_tool(
        raw_tool,
        lambda config: config["configurable"]["trusted_envelope"],
    )

    model_schema = wrapped.tool_call_schema.model_json_schema()
    assert set(model_schema["properties"]) == {
        "part_name",
        "quantity",
        "budget",
        "max_lead_time_days",
    }
    assert set(model_schema["required"]) == {"part_name", "quantity"}
    assert model_schema["properties"]["max_lead_time_days"]["default"] == 14
    assert model_schema["properties"]["budget"]["default"] is None
    execution_schema = wrapped.args_schema.model_json_schema()
    assert execution_schema["additionalProperties"] is False

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        await wrapped.ainvoke(
            {
                "part_name": "六轴惯性传感器",
                "quantity": 500,
                "invocation_envelope": {"sub": "mallory"},
            },
            config={"configurable": {"trusted_envelope": {"sub": "alice"}}},
        )

    result = await wrapped.ainvoke(
        {"part_name": "六轴惯性传感器", "quantity": 500},
        config={"configurable": {"trusted_envelope": {"sub": "alice"}}},
    )

    assert result == {"ok": True}
    assert received == {
        "part_name": "六轴惯性传感器",
        "quantity": 500,
        "budget": None,
        "max_lead_time_days": 14,
        "invocation_envelope": {"sub": "alice"},
    }


def test_scoped_wrapper_rejects_json_schema_without_internal_field():
    async def receiver(**arguments) -> dict:
        return arguments

    raw_tool = StructuredTool.from_function(
        coroutine=receiver,
        name="unsafe_tool",
        description="Missing its trusted context declaration",
        args_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        infer_schema=False,
    )

    with pytest.raises(ValueError, match="does not declare internal field"):
        wrap_scoped_mcp_tool(raw_tool, lambda _config: {"sub": "alice"})


@pytest.mark.asyncio
async def test_scoped_wrapper_preserves_content_and_artifact_response():
    received = {}

    async def fake_mcp_receiver(**arguments):
        received.update(arguments)
        return ([{"type": "text", "text": "comparison complete"}], {"decisionId": "DEC-1"})

    raw_tool = StructuredTool.from_function(
        coroutine=fake_mcp_receiver,
        name="procurement_compare",
        description="Compare procurement candidates",
        args_schema={
            "type": "object",
            "properties": {
                "part_name": {"type": "string"},
                "quantity": {"type": "integer"},
                "invocation_envelope": {"type": "object"},
            },
            "required": ["part_name", "quantity", "invocation_envelope"],
        },
        infer_schema=False,
        response_format="content_and_artifact",
    )
    wrapped = wrap_scoped_mcp_tool(
        raw_tool,
        lambda _config: {"sub": "alice", "aud": "procurement_compare"},
    )

    result = await wrapped.ainvoke({
        "type": "tool_call",
        "name": "procurement_compare",
        "id": "outer-call",
        "args": {"part_name": "六轴惯性传感器", "quantity": 500},
    })

    assert isinstance(result, ToolMessage)
    assert result.content == [{"type": "text", "text": "comparison complete"}]
    assert result.artifact == {"decisionId": "DEC-1"}
    assert received["invocation_envelope"] == {
        "sub": "alice",
        "aud": "procurement_compare",
    }
