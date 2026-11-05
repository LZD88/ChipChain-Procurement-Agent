"""一键执行 MCP 工具的真实 Java 集成测试。

直接在 IDE 中运行本文件，或执行 python -B tests/test_all_tools.py。
在顶部修改 MODE：mcp（进程内 MCP）、agent（HTTP 适配器）、all（两者）。
两种模式都真实请求 Java；mcp 模式只需启动 Java，agent 模式还需启动 MCP。
服务地址读取项目 .env 中的 JAVA_API_BASE_URL 和 ERP_MCP_URL。

默认运行查询测试。ENABLE_ORDER_WRITE_TESTS=True 时额外创建测试订单，
使用真实物料的 MOQ 和 purchasePrice；只更新本次创建的订单，结束时取消。
取消后的测试订单仍保留在数据库；工具直调测试不经过 Agent 人工审批。
测试失败返回退出码 1，模式配置错误返回 2。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
from unittest import SkipTest
from uuid import uuid4

from fastmcp import Client
from langchain_mcp_adapters.client import MultiServerMCPClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.env_utils import ERP_MCP_URL, JAVA_API_BASE_URL
from mcp_server.server_main import mcp


# ===== 一键启动配置 =====
MODE = "mcp"  # mcp / agent / all
SUPPLIER_NAME = "教学供应商·光阱五号"
SUPPLIER_ID = 5  # part_by_supplier 的独立测试参数，填写 Java 中存在且关联物料的供应商 ID。
PART_NAME = "数字温度传感器"
CATEGORY = "传感芯片"
ENABLE_ORDER_WRITE_TESTS = False
SHOW_RESULTS = True  # 只打印业务数据一次，不打印整个 CallToolResult。

MCP_SERVER_CONFIG = {
    "erp": {"url": ERP_MCP_URL, "transport": "streamable_http"},
}


def _show_result(name, data):
    if SHOW_RESULTS:
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
    elif isinstance(data, list):
        print(f"[{name}] 返回 {len(data)} 条记录", flush=True)
    else:
        print(f"[{name}] 返回订单 ID={data.get('id')}", flush=True)


@asynccontextmanager
async def _mcp_caller():
    async with Client(mcp) as client:
        async def call(name, arguments):
            print(f"\n[MCP] {name}: {json.dumps(arguments, ensure_ascii=False)}", flush=True)
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(f"{name} 调用失败：{result.content}")
            _show_result(name, result.data)
            return result.data
        yield call


@asynccontextmanager
async def _agent_caller():
    client = MultiServerMCPClient(MCP_SERVER_CONFIG)
    tools = {tool.name: tool for tool in await client.get_tools(server_name="erp")}

    async def call(name, arguments):
        if name not in tools:
            raise RuntimeError(f"MCP 服务未暴露工具 {name}，请核对 ERP_MCP_URL 和服务版本")
        print(f"\n[Agent adapter] {name}: {json.dumps(arguments, ensure_ascii=False)}", flush=True)
        # ToolCall 形式保留 ToolMessage.artifact 中的 MCP structuredContent。
        result = await tools[name].ainvoke({
            "type": "tool_call", "id": "test-" + uuid4().hex,
            "name": name, "args": arguments,
        })
        if result.status == "error":
            raise RuntimeError(f"{name} 调用失败：{result.content}")
        artifact = result.artifact
        structured = getattr(artifact, "structured_content", None)
        if structured is not None:
            data = structured["result"] if set(structured) == {"result"} else structured
        else:
            content = result.content
            text = content if isinstance(content, str) else "\n".join(
                block["text"] for block in content if block.get("type") == "text"
            )
            data = json.loads(text) if text else []
        _show_result(name, data)
        return data

    yield call


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


async def _find_part(call):
    parts = await call("part_search", {"name": PART_NAME})
    _require(isinstance(parts, list) and parts, f"没有匹配 {PART_NAME!r} 的真实物料，请修改 PART_NAME")
    return parts[0]


def _create_arguments(part, label):
    """MCP 顶层使用 snake_case；order_detail 内保留 Java 字段名。"""
    quantity = max(1, int(part.get("minOrderQuantity") or 1))
    price = Decimal(str(part["purchasePrice"]))
    _require(price.is_finite() and price > 0 and price == price.quantize(Decimal("0.01")),
             "真实采购价必须大于 0 且最多两位小数")
    return {
        "order_number": "MCPTEST-" + uuid4().hex[:16],
        "order_detail": [{
            "partId": part["id"], "quantity": quantity, "unitPrice": float(price),
            "remark": "MCP 集成测试明细",
        }],
        "status": 1,
        "remark": f"{label} 集成测试订单",
    }


@asynccontextmanager
async def _test_order(call, label):
    """所有写入测试共用：新建独立订单，失败时也尝试取消。"""
    if not ENABLE_ORDER_WRITE_TESTS:
        raise SkipTest("ENABLE_ORDER_WRITE_TESTS=False，跳过真实订单写入")
    part = await _find_part(call)
    arguments = _create_arguments(part, label)
    created = await call("order_create", arguments)
    order_id = created["id"]
    _require(isinstance(order_id, int) and order_id > 0, "创建结果缺少有效订单 ID")
    print(f"[测试订单] ID={order_id}，编号={arguments['order_number']}", flush=True)
    try:
        detail = arguments["order_detail"][0]
        expected = Decimal(str(detail["unitPrice"])) * detail["quantity"]
        _require(Decimal(str(created["totalAmount"])) == expected, "Java 计算的订单总金额不正确")
        _require(created["orderNumber"] == arguments["order_number"], "订单编号未正确传递")
        _require(created["status"] == 1, "新建测试订单应为待审核状态")
        yield created, arguments
    finally:
        try:
            cancelled = await call("order_update", {
                "order_id": order_id, "status": 5,
                "remark": f"{label} 集成测试结束，已取消",
            })
            _require(cancelled["id"] == order_id and cancelled["status"] == 5, "测试订单取消失败")
            print(f"[清理] 测试订单 {order_id} 已取消，数据库保留记录", flush=True)
        except Exception:
            print(f"[清理失败] 请检查测试订单 ID={order_id}，编号={arguments['order_number']}", flush=True)
            raise


async def test_mcp_supplier_query():
    async with _mcp_caller() as call:
        records = await call("supplier_query", {"name": SUPPLIER_NAME})
        _require(isinstance(records, list) and records, f"未查到供应商 {SUPPLIER_NAME!r}")
        _require(all(SUPPLIER_NAME in row["name"] for row in records), "供应商名称筛选未生效")
        return records


async def test_mcp_part_query():
    async with _mcp_caller() as call:
        records = await call("part_query", {"current": 1, "size": 5, "category": CATEGORY})
        _require(isinstance(records, list) and len(records) <= 5, "分页结果格式或数量不正确")
        _require(all(row["category"] == CATEGORY for row in records), "物料分类筛选未生效")
        return records


async def test_mcp_part_search():
    async with _mcp_caller() as call:
        records = await call("part_search", {"name": PART_NAME})
        _require(isinstance(records, list) and records, f"未查到物料 {PART_NAME!r}")
        _require(len({row["id"] for row in records}) == len(records), "返回重复物料 ID")
        return records


async def test_mcp_part_by_supplier():
    """直接按配置的供应商 ID 测试，不额外调用 part_search 准备参数。"""
    async with _mcp_caller() as call:
        records = await call("part_by_supplier", {"supplier_id": SUPPLIER_ID})
        _require(isinstance(records, list) and records,
                 f"供应商 ID={SUPPLIER_ID} 未查到关联物料，请核对顶部 SUPPLIER_ID")
        _require(all(row["supplierId"] == SUPPLIER_ID for row in records), "返回其他供应商的物料")
        return records


async def test_mcp_order_create():
    if not ENABLE_ORDER_WRITE_TESTS:
        raise SkipTest("ENABLE_ORDER_WRITE_TESTS=False，跳过真实订单写入")
    async with _mcp_caller() as call:
        async with _test_order(call, "mcp_client create") as (created, _):
            return created


async def test_mcp_order_update():
    if not ENABLE_ORDER_WRITE_TESTS:
        raise SkipTest("ENABLE_ORDER_WRITE_TESTS=False，跳过真实订单写入")
    async with _mcp_caller() as call:
        async with _test_order(call, "mcp_client update") as (created, arguments):
            order_id = created["id"]
            # 场景 1：显式替换本次测试订单明细，保持合法数量与单价。
            detail = {**arguments["order_detail"][0], "remark": "更新测试明细"}
            updated = await call("order_update", {
                "order_id": order_id, "order_detail": [detail], "remark": "已更新测试明细",
            })
            _require(updated["id"] == order_id, "更新了错误的订单")
            _require(updated["orderDetail"][0]["partId"] == detail["partId"], "明细更新未生效")
            # 场景 2：仅改备注，不传编号、下单时间、明细或审核状态。
            remark = "仅更新测试备注"
            updated = await call("order_update", {"order_id": order_id, "remark": remark})
            _require(updated["id"] == order_id and updated["remark"] == remark, "备注更新未生效")
            return updated


async def test_mcp_order_search_details():
    async with _mcp_caller() as call:
        records = await call("order_search_details", {"part_name": PART_NAME})
        _require(isinstance(records, list), "订单明细应返回列表")
        for row in records:
            _require(row["partDetail"]["id"] == row["partId"], "订单物料关联不正确")
            _require("supplier" in row["partDetail"], "订单明细缺少嵌套供应商信息")
        dated = await call("order_search_details", {
            "part_name": PART_NAME, "start_date": "2000-01-01", "end_date": date.today().isoformat(),
        })
        _require(isinstance(dated, list), "日期筛选应返回列表")
        return records


async def test_mcp_inventory_warning():
    async with _mcp_caller() as call:
        records = await call("inventory_warning", {})
        _check_inventory(records)
        return records


def _check_inventory(records):
    _require(isinstance(records, list), "库存预警应返回列表")
    for row in records:
        _require(row["currentQuantity"] <= row["safetyStock"], "返回的库存未达到预警条件")
        _require(row["partDetail"]["id"] == row["partId"], "库存物料关联不正确")


async def test_agent_order_create():
    if not ENABLE_ORDER_WRITE_TESTS:
        raise SkipTest("ENABLE_ORDER_WRITE_TESTS=False，跳过真实订单写入")
    async with _agent_caller() as call:
        async with _test_order(call, "agent_client create") as (created, _):
            return created


async def test_agent_inventory_warning():
    async with _agent_caller() as call:
        records = await call("inventory_warning", {})
        _check_inventory(records)
        return records


async def _run_tests(label, cases):
    results = {}
    for name, test_fn in cases:
        print(f"\n[{label}] 测试 {name}", flush=True)
        try:
            await test_fn()
        except SkipTest as error:
            results[name] = "跳过"
            print(f"[SKIP] {error}", flush=True)
        except Exception as error:
            results[name] = "失败"
            print(f"[ERROR] {name}: {error}", flush=True)
        else:
            results[name] = "通过"
    print(f"\n[汇总] {label}", flush=True)
    for name, status in results.items():
        print(f"  {name}: {status}", flush=True)
    return all(status != "失败" for status in results.values())


async def run_all_mcp_client_tests():
    return await _run_tests("mcp_client", [
        ("supplier_query", test_mcp_supplier_query),
        ("part_query", test_mcp_part_query),
        ("part_search", test_mcp_part_search),
        ("part_by_supplier", test_mcp_part_by_supplier),
        ("order_create", test_mcp_order_create),
        ("order_update", test_mcp_order_update),
        ("order_search_details", test_mcp_order_search_details),
        ("inventory_warning", test_mcp_inventory_warning),
    ])


async def run_all_agent_client_tests():
    return await _run_tests("agent_client", [
        ("order_create", test_agent_order_create),
        ("inventory_warning", test_agent_inventory_warning),
    ])


async def _run_mode(mode):
    results = []
    if mode in ("mcp", "all"):
        results.append(await run_all_mcp_client_tests())
    if mode in ("agent", "all"):
        results.append(await run_all_agent_client_tests())
    return all(results)


def main(mode=None) -> int:
    mode = MODE if mode is None else mode
    if mode not in ("mcp", "agent", "all"):
        print(f"未知模式 {mode!r}，请将 MODE 设为 mcp / agent / all", flush=True)
        return 2
    print(f"测试模式：{mode}；Java 服务：{JAVA_API_BASE_URL}", flush=True)
    if mode in ("agent", "all"):
        print(f"MCP HTTP 地址：{ERP_MCP_URL}", flush=True)
    print(f"订单写入测试：{'启用（创建后取消，数据库保留记录）' if ENABLE_ORDER_WRITE_TESTS else '跳过'}", flush=True)
    try:
        return 0 if asyncio.run(_run_mode(mode)) else 1
    except Exception as error:
        print(f"[ERROR] 测试启动失败：{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
