"""Shared role definitions for live execution and isolated prompt evaluation."""
from deepagents import SubAgent
from agent.memory.prompts import PROCUREMENT_ANALYST_PROMPT, PROCUREMENT_ORDER_PROMPT, PROCUREMENT_ORCHESTRATOR_PROMPT
from agent.middleware_config import create_analyst_middleware, create_order_middleware


def build_role_definitions(precomputed, mcp_tools, backend, summary_model, web_search, request_order_info, prompt_snapshot=None):
    procurement_analyst: SubAgent = {
        "name": "procurement-analyst",
        "description": (
            "采购分析专家。负责基于确定性规则的供应商比价、物料行情分析、采购策略建议、 "
            "市场调研、成本评估等需要多步数据收集和深度分析的任务。 "
            '当用户需求涉及"分析"、"对比"、"报告"、"建议"、"评估"、 '
            '"行情"、"比价"、"筛选"等关键词时，应委派给此子 Agent。'
        ),
        "system_prompt": prompt_snapshot.prompt("procurement-analyst") if prompt_snapshot else PROCUREMENT_ANALYST_PROMPT,
        "tools": [
            mcp_tools["supplier_query"],
            mcp_tools["part_query"],
            mcp_tools["part_search"],
            mcp_tools["part_by_supplier"],
            mcp_tools["order_search_details"],
            mcp_tools["inventory_warning"],
            mcp_tools["procurement_compare"],
            precomputed.chart_tool,
            web_search,
        ],
        "skills": ["/skills/procurement/"],
        "middleware": create_analyst_middleware(summary_model, backend),
    }
    procurement_order: SubAgent = {
        "name": "procurement-order",
        "description": (
            "采购订单管理专家。负责基于确定性比价结果创建待审核采购单、修改待审核订单的 "
            "交货日期/备注/物料明细，以及与采购订单相关的查询操作。订单状态流转由采购人员完成。 "
            '当用户需求涉及"下单"、"创建订单"、"修改采购单"、"更新订单"、 '
            '"订单状态查询"、"交期变更"等关键词时，应委派给此子 Agent。'
        ),
        "system_prompt": prompt_snapshot.prompt("procurement-order") if prompt_snapshot else PROCUREMENT_ORDER_PROMPT,
        "tools": [
            mcp_tools["procurement_compare"],
            mcp_tools["order_create"],
            mcp_tools["order_update"],
            mcp_tools["order_search_details"],
            web_search,
            request_order_info,
        ],
        "skills": ["/skills/order/"],
        "interrupt_on": {
            "order_create": {"allowed_decisions": ["approve", "reject"]},
            "order_update": {"allowed_decisions": ["approve", "reject"]},
        },
        "middleware": create_order_middleware(),
    }
    procurement_orchestrator: SubAgent = {
        "name": "procurement-orchestrator",
        "description": (
            "端到端采购闭环编排专家。负责把自然语言采购需求依次转换为物料匹配、"
            "库存核验、供应商状态与信用核验、预算/交期/MOQ 校验、可解释推荐方案，"
            "并在人工批准后创建待审核采购订单。用户说‘帮我采购/需要采购/从需求到下单/"
            "给出方案并执行’时必须委派给此 Agent；不要拆分给分析和订单两个 Agent。"
        ),
        "system_prompt": prompt_snapshot.prompt("procurement-orchestrator") if prompt_snapshot else PROCUREMENT_ORCHESTRATOR_PROMPT,
        "tools": [
            mcp_tools["part_query"],
            mcp_tools["part_search"],
            mcp_tools["part_by_supplier"],
            mcp_tools["inventory_query"],
            mcp_tools["inventory_warning"],
            mcp_tools["supplier_get"],
            mcp_tools["supplier_query"],
            mcp_tools["order_search_details"],
            mcp_tools["procurement_compare"],
            mcp_tools["order_create"],
            request_order_info,
        ],
        "skills": ["/skills/procurement/", "/skills/order/"],
        "interrupt_on": {
            "order_create": {"allowed_decisions": ["approve", "reject"]},
        },
        "middleware": create_order_middleware(),
    }

    return [procurement_orchestrator, procurement_analyst, procurement_order]
