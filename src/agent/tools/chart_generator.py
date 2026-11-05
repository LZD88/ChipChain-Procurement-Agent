"""将 26 种可视化合并为一个入口；表格及未知工具保留原接口。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import ConfigDict, Field, create_model

from agent.tools.chart_validation import ChartConfigValidator

REFERENCE_PATH = "/skills/analyis_chart_params.md"

# 按用途分组，同构的数据格式共用一行；描述只展示当前服务已安装的类型。
_COMPACT_ROWS = (
    ("Compare", ("bar", "column"), "data:[{category,value,group?}]; group/stack exclusive"),
    ("Compare", ("pie", "funnel"), "data:[{category,value}]; pie.innerRadius:0..1"),
    ("Compare", ("waterfall",), "data:[{category,value?,isIntermediateTotal?,isTotal?}]; non-total needs value"),
    ("Trend", ("line", "area"), "data:[{time,value,group?}]; area.stack needs group on every row"),
    ("Trend", ("dual_axes",), "categories:[str],series:[{type:column|line,data:[num],axisYTitle?}]; equal lengths"),
    ("Distribution", ("boxplot", "violin"), "data:[{category,value,group?}]; raw samples"),
    ("Distribution", ("histogram",), "data:[num]; binNumber:positive integer"),
    ("Distribution", ("scatter",), "data:[{x,y,group?}]"),
    ("Indicator", ("radar",), "data:[{name,value,group?}]"),
    ("Indicator", ("liquid",), "percent:0..1; shape:circle|rect|pin|triangle; NO data"),
    ("Indicator", ("word_cloud",), "data:[{text,value}]"),
    ("Hierarchy", ("treemap",), "data:[{name,value,children?}]; array of trees, 3 levels"),
    ("Hierarchy", ("organization", "mind_map", "fishbone_diagram"), "data:{name,children?}; tree, 4 levels; organization allows description/orient"),
    ("Relation", ("sankey",), "data:[{source,target,value}]; nodeAlign:left|right|justify|center"),
    ("Relation", ("venn",), "data:[{sets:[str],value,label?}]"),
    ("Relation", ("flow_diagram", "network_graph"), "data:{nodes:[{name}],edges:[{source,target,name?}]}; edges reference node names"),
    ("Map", ("district_map",), "title,data:{name,subdistricts?:[{name,dataValue?}],...}; China regions"),
    ("Map", ("pin_map",), "title,data:[str]; Chinese POIs; markerPopup?"),
    ("Map", ("path_map",), "title,data:[{data:[str]}]; each item is a POI route"),
)

CHART_GROUPS = {
    group: tuple(kind for row_group, kinds, _ in _COMPACT_ROWS if row_group == group for kind in kinds)
    for group in dict.fromkeys(row[0] for row in _COMPACT_ROWS)
}
_TOOL_NAMES = {
    kind: f"generate_{kind}" if kind.endswith(("_map", "_diagram", "_graph")) else f"generate_{kind}_chart"
    for kinds in CHART_GROUPS.values() for kind in kinds
}


def _build_chart_type_reference(available) -> str:
    rows = []
    for group, kinds, shape in _COMPACT_ROWS:
        installed = [kind for kind in kinds if kind in available]
        if installed:
            rows.append(f"{group} {'/'.join(installed)}: {shape}")
    return "\n".join(rows)


_COMPACT_TABLE = _build_chart_type_reference(_TOOL_NAMES)
_STRUCTURE_TYPES = {"organization", "mind_map", "fishbone_diagram", "flow_diagram", "network_graph"}


def _build_tool_description(tool_map: dict[str, BaseTool]) -> str:
    return (
        "Generate charts/maps/diagrams; return original MCP content and artifact. "
        "chart_config shapes below: []=array, ?=optional; data arrays are nonempty, numeric values finite.\n"
        + (_COMPACT_TABLE if set(tool_map) == set(_TOOL_NAMES) else _build_chart_type_reference(tool_map))
        + "\nOnly width/height are universal. Maps require title (<=16 chars), have no theme/style. "
        "organization/mind_map/fishbone_diagram/flow_diagram/network_graph have no title; style only texture. "
        "Prefer default/academy themes for these structures; dark may fall back with notice. "
        "Other title/theme/style/axis options vary by type; waterfall style.palette is an object, liquid uses style.color. "
        f"Advanced options and JSON examples: read_file('{REFERENCE_PATH}')."
    )


async def _invoke_chart(mcp_tool: BaseTool, arguments: dict, config: RunnableConfig):
    # ToolCall 形式才能保留 content_and_artifact 工具的 artifact。
    result = await mcp_tool.ainvoke(
        {"type": "tool_call", "id": uuid4().hex, "name": mcp_tool.name, "args": arguments},
        config=config,
    )
    if isinstance(result, ToolMessage):
        if result.status == "error":
            raise ToolException(str(result.content))
        return result.content, result.artifact
    return result, None


def _render_error(kind: str, exc: Exception) -> ToolException:
    if kind in {"pin_map", "path_map"} and "reading 'length'" in str(exc):
        return ToolException(f"{kind}: 远端地图服务内部异常，本次未生成图片。请检查地图 MCP 服务，不要重复提交相同参数。")
    if isinstance(exc, ToolException):
        return ToolException(str(exc))
    return ToolException(f"{kind}: MCP request failed ({type(exc).__name__}); retry later.")


async def _render_chart(kind: str, mcp_tool: BaseTool, arguments: dict, config: RunnableConfig):
    try:
        return await _invoke_chart(mcp_tool, arguments, config)
    except Exception as exc:
        # 仅对已实测的结构图暗色主题故障重试一次；不改数据、不重试网络错误。
        retry_theme = (
            kind in _STRUCTURE_TYPES and arguments.get("theme") == "dark"
            and any(marker in str(exc) for marker in ("reading 'key'", "reading 'colors'"))
        )
        if not retry_theme:
            raise _render_error(kind, exc) from exc
    try:
        content, artifact = await _invoke_chart(mcp_tool, {**arguments, "theme": "default"}, config)
    except Exception as exc:
        raise _render_error(kind, exc) from exc
    notice = "兼容提示：远端暂不支持该结构图的 dark 主题，已使用 default 主题生成；数据和其他选项保持原样。"
    if isinstance(content, str):
        content = f"{content}\n{notice}"
    else:
        content = [*content, {"type": "text", "text": notice}]
    return content, artifact


def create_unified_chart_tool(chart_mcp_tools: list[BaseTool]) -> tuple[StructuredTool, list[BaseTool]]:
    """返回统一图表入口及独立工具；参数约束来自本次加载的原始 MCP schema。"""
    names = {name: kind for kind, name in _TOOL_NAMES.items()}
    tool_map, other_tools = {}, []
    for mcp_tool in chart_mcp_tools:
        kind = names.get(mcp_tool.name)
        if kind is None:
            other_tools.append(mcp_tool)
        elif kind in tool_map:
            raise ValueError(f"Duplicate chart tool: {mcp_tool.name}")
        else:
            tool_map[kind] = mcp_tool
    if not tool_map:
        raise ValueError("MCP service did not provide any supported visualization tools")

    validators = {kind: ChartConfigValidator(kind, mcp_tool.args_schema) for kind, mcp_tool in tool_map.items()}
    input_schema = create_model(
        "VisualizationInput",
        __config__=ConfigDict(extra="forbid", strict=True),
        chart_type=(Literal[tuple(sorted(tool_map))], Field(description="Visualization type")),
        chart_config=(dict[str, Any], Field(description="Parameters for the selected type; see tool description")),
    )

    async def generate_chart(chart_type: str, chart_config: dict, config: RunnableConfig):
        arguments = validators[chart_type].prepare(chart_config)
        return await _render_chart(chart_type, tool_map[chart_type], arguments, config)

    return StructuredTool(
        name="generate_chart",
        description=_build_tool_description(tool_map),
        args_schema=input_schema,
        coroutine=generate_chart,
        response_format="content_and_artifact",
        handle_tool_error=True,
        handle_validation_error=lambda exc: f"Invalid visualization arguments: {exc}. See {REFERENCE_PATH}",
    ), other_tools