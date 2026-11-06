"""只读验证多 Agent 采购闭环依赖的 ERP MCP 工具。"""

import asyncio

from fastmcp import Client

from mcp_server.server_main import mcp


async def main() -> None:
    async with Client(mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}
        required = {
            "part_search", "inventory_query", "supplier_get",
            "order_search_details", "order_create",
        }
        missing = required - tools
        if missing:
            raise RuntimeError(f"闭环工具缺失: {sorted(missing)}")

        parts = (await client.call_tool(
            "part_search", {"name": "六轴惯性传感器"},
        )).data or []
        inventory_result = await client.call_tool(
            "inventory_query",
            {"part_name": "六轴惯性传感器", "current": 1, "size": 20},
        )
        inventory = inventory_result.data or []
        if not parts:
            raise RuntimeError("测试物料不存在")

        supplier = (await client.call_tool(
            "supplier_get", {"supplier_id": parts[0]["supplierId"]},
        )).data or {}
        if not supplier:
            raise RuntimeError("物料关联供应商不存在")

        print(
            "闭环只读验证通过："
            f"物料候选={len(parts)}，库存查询成功={not inventory_result.is_error}，"
            f"供应商状态={supplier.get('status')}，写入工具已注册（未执行）"
        )


if __name__ == "__main__":
    asyncio.run(main())
