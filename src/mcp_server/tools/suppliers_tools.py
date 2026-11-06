from fastmcp import FastMCP, Context

# 分组名称
GROUP_NAME = "supplier"
def register_supplier_tools(mcp: FastMCP):
    """注册供应商分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_get")
    async def get_supplier(supplier_id: int, ctx: Context) -> dict:
        """按供应商 ID 查询供应商详情和信用评级。

        物料记录中的 supplierId 可直接传给本工具。采购规划必须核对供应商
        状态与信用评级，已终止合作的供应商不得进入推荐方案。

        Args:
            supplier_id: 供应商 ID
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        try:
            response = await http_client.get(f"/suppliers/get/{supplier_id}")
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return {"error": f"API error: code={result.get('code')}"}
            return result.get("data", {})
        except Exception as e:
            return {"error": f"没有查询到供应商信息，而且报错: {e}"}

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_suppliers(name: str, ctx: Context) -> list:
        """
        按名称模糊搜索供应商。

        Args:
            name: 供应商名称（模糊查询），必填
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/suppliers/search", params={"name": name.strip()})
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]
            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
