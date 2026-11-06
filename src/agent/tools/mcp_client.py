"""
MCP 工具客户端。

在 Agent 启动时连接所有 MCP Server，获取全部 MCP 工具，
并按分组筛选后分配给不同的子 Agent。

使用方式:
    from agent.tools.mcp_client import load_mcp_tools

    analyst_tools, order_tools, chart_tools = await load_mcp_tools()
"""

import logging

from langchain_mcp_adapters.client import MultiServerMCPClient
from agent.env_utils import ERP_MCP_URL, ANALYSIS_MCP_URL

logger = logging.getLogger(__name__)

# MCP Server 连接配置
MCP_SERVER_CONFIG = {
    "chip-supply-api": {
        "url": ERP_MCP_URL,
        "transport": "streamable_http",
    },
    "analysis": {
        "url": ANALYSIS_MCP_URL,
        "transport": "streamable_http",
    },
}

# 工具分组规则（前缀匹配）
ANALYST_TOOL_PREFIXES = ("supplier_", "part_", "inventory_", "procurement_")
ORDER_TOOL_PREFIXES = ("order_",)
CHART_TOOL_PREFIXES = ("generate_",)  # 可视化及表格工具


async def load_mcp_tools(
    server_config: dict | None = None,
) -> tuple[list, list, list]:
    """
    连接到所有 MCP Server，加载全部工具并分组。

    Args:
        server_config: MCP Server 连接配置，默认使用 MCP_SERVER_CONFIG。

    Returns:
        (analyst_tools, order_tools, chart_tools) 三元组
        - analyst_tools: 供应商查询 + 零部件查询 + 库存查询/预警工具
        - order_tools: 订单创建 + 订单更新 + 订单搜索工具
        - chart_tools: 图表/地图/可视化生成工具（来自魔塔社区 MCP Server，27 种）
    """
    if server_config is None:
        server_config = MCP_SERVER_CONFIG
    # 保留完整配置交给适配器；业务 MCP 是必需项，可视化 MCP 是增强项。
    mcp_client = MultiServerMCPClient(server_config)

    # 从 MCP Server 获取业务工具
    chip_supply_tools = await mcp_client.get_tools(server_name="chip-supply-api")
    logger.debug("MCP chip-supply-api 加载完成：%d 个工具", len(chip_supply_tools))

    # 从魔塔社区 MCP Server 获取图表工具
    analysis_tools = []
    if server_config.get("analysis", {}).get("url"):
        analysis_tools = await mcp_client.get_tools(server_name="analysis")
        logger.debug("MCP analysis 加载完成：%d 个工具", len(analysis_tools))

    # 按前缀分组：业务工具
    analyst_tools = [
        t for t in chip_supply_tools
        if t.name.startswith(ANALYST_TOOL_PREFIXES)
    ]
    order_tools = [
        t for t in chip_supply_tools
        if t.name.startswith(ORDER_TOOL_PREFIXES)
    ]

    # 图表工具（来自魔塔社区）
    chart_tools = [
        t for t in analysis_tools
        if t.name.startswith(CHART_TOOL_PREFIXES)
    ]

    return analyst_tools, order_tools, chart_tools
