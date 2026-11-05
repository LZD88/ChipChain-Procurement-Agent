import asyncio
import json
from pathlib import Path
import sys

from fastmcp import Client
from langchain_mcp_adapters.client import MultiServerMCPClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.env_utils import ERP_MCP_URL
from mcp_server.server_main import mcp


# 直接运行本文件会真实更新该订单；按实际测试需求修改参数。
# MCP 顶层参数使用 snake_case，工具内部负责映射为 Java 的 camelCase。
ORDER_UPDATE_ARGS = {
    "order_id": 1,
    "actual_delivery_date": "2026-12-25",
    "order_number": "CL-DEMO-0001",
    "remark": "交货日期延后",
}
# 订单没有独立的 supplierId 参数。更换供应商需在 order_detail 中使用
# 新供应商对应的 partId，并提供完整明细（传入明细会替换原订单全部明细）。


async def mcp_client():
    """通过进程内 MCP 客户端，真实调用 Java 更新订单。"""
    async with Client(mcp) as client:
        # 其他工具同样直接传顶层参数，例如：
        # await client.call_tool("supplier_query", {"name": "教学供应商·晶穹一号"})
        print("调用 order_update：", json.dumps(ORDER_UPDATE_ARGS, ensure_ascii=False))
        result = await client.call_tool("order_update", ORDER_UPDATE_ARGS)
        print(json.dumps(result.data, ensure_ascii=False, indent=2))


async def agent_client():
    mcp_server_config = {
        "url": ERP_MCP_URL,
        "transport": "streamable_http",
    }
    # 创建一个mcp的客户端
    mcp_client = MultiServerMCPClient({
        "api": mcp_server_config,
    })
    all_tools = await mcp_client.get_tools(server_name="api")
    print(f"已加载 {len(all_tools)} 个 MCP 工具")
    tools = [tool for tool in all_tools if tool.name.startswith("part_")]
    print(f"零部件工具（{len(tools)} 个）：{', '.join(tool.name for tool in tools)}")

if __name__ == '__main__':
    asyncio.run(mcp_client())
    # asyncio.run(agent_client())
