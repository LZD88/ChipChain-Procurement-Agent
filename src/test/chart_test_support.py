"""图表测试共用原始 MCP schema、场景和真实 LangChain MCP 适配器。"""

import json
from pathlib import Path
import sys

from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.tools.chart_generator import create_unified_chart_tool

FIXTURES = Path(__file__).with_name('fixtures')
SCHEMAS = json.loads((FIXTURES / 'chart_mcp_tools.json').read_text())
SCENARIOS = json.loads((FIXTURES / 'chart_scenarios.json').read_text())


def make_tools(session):
    return [convert_mcp_tool_to_langchain_tool(session, Tool.model_validate(schema)) for schema in SCHEMAS]


def make_visualization(session):
    return create_unified_chart_tool(make_tools(session))[0]


async def invoke(tool, case, **kwargs):
    return await tool.ainvoke({
        'type': 'tool_call', 'id': case['id'], 'name': tool.name,
        'args': {key: case[key] for key in ('chart_type', 'chart_config')},
    }, **kwargs)
