from typing import Optional

from fastmcp import FastMCP, Context

GROUP_NAME = "inventory"


def register_inventory_tools(mcp: FastMCP):
    """注册库存管理分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_inventory(
        current: Optional[int] = 1,
        size: Optional[int] = 20,
        part_name: Optional[str] = None,
        warehouse_location: Optional[str] = None,
        ctx: Context = None,
    ) -> list:
        """分页查询库存，可按物料名称和仓库位置筛选。

        返回库存数量、安全库存、仓库位置及关联物料详情。采购规划时应先用
        此工具确认现有库存，再决定是否需要采购以及采购数量。

        Args:
            current: 当前页码，从 1 开始
            size: 每页大小
            part_name: 物料名称或型号关键词
            warehouse_location: 仓库位置关键词
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        params = {"current": current, "size": size}
        if part_name is not None:
            params["partName"] = part_name.strip()
        if warehouse_location is not None:
            params["warehouseLocation"] = warehouse_location.strip()

        try:
            response = await http_client.get("/inventory/page", params=params)
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]
            return result.get("data", {}).get("records", [])
        except Exception as e:
            return [f"没有查询到任何信息，而且报错: {e}"]

    @mcp.tool(name=f"{GROUP_NAME}_warning")
    async def list_inventory_warnings(ctx: Context) -> list:
        """
        查询库存预警列表。
        返回所有库存不足（当前库存低于安全库存）的物料及对应的零部件详情。

        无需传参。
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/inventory/warning")
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
