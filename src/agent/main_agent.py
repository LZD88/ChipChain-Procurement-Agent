"""
主 Agent 入口模块。

使用 DeepAgents `create_deep_agent` 将所有组件串联为一个可运行的
ERP 采购智能助手。采用 Graph Factory 模式：启动时预计算可复用组件，
按用户与沙箱代际创建并复用 graph，实现用户级沙箱隔离。

使用方式:
    from agent.main_agent import precompute_agent_context, create_main_agent

    # 启动时
    precomputed = await precompute_agent_context()

    # 首次准备或缓存失效时
    agent_graph = await create_main_agent(
        config,
        sandbox_backend=user_sandbox,
        precomputed=precomputed,
    )
"""

from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import dataclass, field

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.summarization import create_summarization_tool_middleware
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.runnables import RunnableConfig

from agent.config import (
    AGENTS_MD_FILENAME,
    DOWNLOAD_DIR,
    LOCAL_AGENTS_MD,
    MAIN_MODEL,
    SKILLS_STORE_NAMESPACE,
    STORE,
    SUMMARY_MODEL,
)
from agent.memory.prompts import (
    PROCUREMENT_ANALYST_PROMPT,
    PROCUREMENT_ORCHESTRATOR_PROMPT,
    PROCUREMENT_ORDER_PROMPT,
    system_prompt,
)
from agent.middleware_config import (
    create_analyst_middleware,
    create_order_middleware,
)
from agent.middlewares.context_injection import UserInfoInjectionMiddleware
from agent.middlewares.guardrail_response import GuardrailResponseMiddleware
from agent.middlewares.sandbox_breaker import SandboxCircuitBreakerMiddleware
from agent.middlewares.sandbox_health import SandboxHealthMiddleware
from agent.middlewares.skills_sync import SkillsSyncMiddleware
from agent.middlewares.tool_error import ToolErrorMiddleware
from agent.middlewares.user_skills_restore import UserSkillsRestoreMiddleware
from agent.schema import ProcurementContext
from agent.mysql_storage import mysql_storage
from agent.tools.chart_generator import create_unified_chart_tool
from agent.tools.hitl_tools import request_order_info
from agent.tools.assign_skill import create_assign_skill_tool
from agent.tools.download_sandbox_file import create_download_tool
from agent.tools.mcp_client import load_mcp_tools
from agent.tools.web_search import web_search
from agent.tools.scoped_mcp import wrap_signed_mcp_tool
from agent.backends.sandbox_sync import sync_lock, generation
from agent.backends.sandbox_proxy import SandboxBackendProxy
from api_view.observability import PREPARATION_STAGE, preparation_measure


logger = logging.getLogger(__name__)


def agent_fingerprint(precomputed):
    """Only deployment inputs; never run identity, credentials or approvals."""
    from agent.memory import prompts
    models = []
    for model in (MAIN_MODEL, SUMMARY_MODEL):
        models.append({key: getattr(model, key, None) for key in
                       ("model_name", "model", "temperature", "max_tokens", "streaming",
                        "top_p", "frequency_penalty", "presence_penalty", "seed")})
    tools = []
    if precomputed is not None:
        for tool in [*precomputed.analyst_mcp_tools, *precomputed.order_mcp_tools,
                     *precomputed.extra_mcp_tools, precomputed.chart_tool]:
            if tool is None:
                continue
            tools.append((tool.name, tool.description, tool.args))
    payload = {
        "prompts": {name: getattr(prompts, name) for name in
                    ("system_prompt", "PROCUREMENT_ANALYST_PROMPT", "PROCUREMENT_ORDER_PROMPT", "PROCUREMENT_ORCHESTRATOR_PROMPT")},
        "agents": LOCAL_AGENTS_MD.read_text(encoding="utf-8"),
        "models": models, "tools": tools, "precomputed_generation": id(precomputed),
        "tool_registration": [web_search.name, request_order_info.name,
                              repr(create_analyst_middleware), repr(create_order_middleware)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ============================================================
# PrecomputedContext — 启动时预计算的可复用组件
# ============================================================

@dataclass
class PrecomputedContext:
    """启动时预计算的 MCP 工具和图表工具。

    仅包含不依赖 sandbox_backend 的组件。
    sandbox 依赖的工具（assign_skill、download_sandbox_file）和 backend 依赖的
    中间件在 create_main_agent() 中按用户和沙箱代际创建。
    """
    analyst_mcp_tools: list = field(default_factory=list)
    order_mcp_tools: list = field(default_factory=list)
    extra_mcp_tools: list = field(default_factory=list)
    chart_tool: object = None


async def precompute_agent_context() -> PrecomputedContext:
    """加载 MCP 工具并创建图表入口，启动时执行一次，所有请求复用。"""
    analyst_mcp_tools, order_mcp_tools, chart_mcp_tools = await load_mcp_tools()
    chart_tool, extra_mcp_tools = create_unified_chart_tool(chart_mcp_tools)
    logger.info(
        "MCP 工具就绪：分析 %d，订单 %d，可视化 %d→1，独立工具 %d",
        len(analyst_mcp_tools), len(order_mcp_tools),
        len(chart_mcp_tools) - len(extra_mcp_tools), len(extra_mcp_tools),
    )
    return PrecomputedContext(
        analyst_mcp_tools=analyst_mcp_tools,
        order_mcp_tools=order_mcp_tools,
        extra_mcp_tools=extra_mcp_tools,
        chart_tool=chart_tool,
    )


# ============================================================
# create_main_agent — 基于 per-user 沙箱创建可租用的 agent graph
# ============================================================

async def create_main_agent(
    config: RunnableConfig,
    *,
    sandbox_backend: SandboxBackendProtocol,
    precomputed: PrecomputedContext,
):
    """创建 ERP 采购智能助手的用户 graph，供准备注册表缓存和租用。

    缓存缺失或失效时调用，使用预计算工具及传入的 per-user 沙箱。
    SandboxBackendProxy 保证沙箱热替换不丢引用。

    Args:
        config: 准备配置，含 user_id；实际 thread_id 在每次运行时传入。
        sandbox_backend: per-user 沙箱后端（SandboxBackendProxy）。
        precomputed: 启动时预计算的 MCP 工具和图表工具。
    """
    await mysql_storage.initialize()
    user_id = config["configurable"]["user_id"]

    # ---- Phase 1: CompositeBackend 实例 ----
    # graph 按用户创建，记忆命名空间绑定该用户，子 Agent 共享同一后端。
    # default 仍持有 SandboxBackendProxy，沙箱热替换不丢引用。
    backend = CompositeBackend(
        default=sandbox_backend,
        routes={
            "/memories/": StoreBackend(
                store=STORE,
                namespace=lambda _rt: (user_id,),
            ),
            "/persisted-skills/": StoreBackend(
                store=STORE,
                namespace=lambda _rt: SKILLS_STORE_NAMESPACE,
            ),
        },
    )

    agents_md_bytes = LOCAL_AGENTS_MD.read_text(encoding="utf-8").encode("utf-8")
    async with sync_lock(sandbox_backend):
        with preparation_measure(PREPARATION_STAGE, "sync"):
            agents_key = (generation(sandbox_backend), hashlib.sha256(agents_md_bytes).hexdigest())
            previous = getattr(sandbox_backend, "_agents_content_key", None)
            if previous != agents_key:
                results = await sandbox_backend.aupload_files([("/AGENTS.md", agents_md_bytes)])
                if not any(result.path == "/AGENTS.md" and result.error is None for result in results):
                    raise RuntimeError("AGENTS.md 初始化上传失败")
                if isinstance(sandbox_backend, SandboxBackendProxy):
                    sandbox_backend._agents_content_key = agents_key

    skills_sync = SkillsSyncMiddleware(sandbox_backend)
    skills_restore = UserSkillsRestoreMiddleware(sandbox_backend, SKILLS_STORE_NAMESPACE)
    # Complete first-run remote synchronization while preparing, before a model runs.
    await skills_sync._sync_files()
    await skills_restore.sync(STORE)

    # ---- Phase 2: 创建依赖当前用户沙箱的工具 ----
    assign_skill = create_assign_skill_tool(
        sandbox_backend,
        store=STORE,
        skills_namespace=SKILLS_STORE_NAMESPACE,
    )
    download_sandbox_file = create_download_tool(sandbox_backend, DOWNLOAD_DIR)

    # ---- Phase 3: 直接定义子 Agent，按完整名称绑定 MCP 工具对象 ----
    mcp_tools = {}
    for tool in [*precomputed.analyst_mcp_tools, *precomputed.order_mcp_tools]:
        mcp_tools[tool.name] = (
            wrap_signed_mcp_tool(tool)
            if tool.name in {"procurement_compare", "order_create"}
            else tool
        )
    procurement_analyst: SubAgent = {
        "name": "procurement-analyst",
        "description": (
            "采购分析专家。负责基于确定性规则的供应商比价、物料行情分析、采购策略建议、 "
            "市场调研、成本评估等需要多步数据收集和深度分析的任务。 "
            '当用户需求涉及"分析"、"对比"、"报告"、"建议"、"评估"、 '
            '"行情"、"比价"、"筛选"等关键词时，应委派给此子 Agent。'
        ),
        "system_prompt": PROCUREMENT_ANALYST_PROMPT,
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
        "middleware": create_analyst_middleware(SUMMARY_MODEL, backend),
    }
    procurement_order: SubAgent = {
        "name": "procurement-order",
        "description": (
            "采购订单管理专家。负责基于确定性比价结果创建待审核采购单、修改待审核订单的 "
            "交货日期/备注/物料明细，以及与采购订单相关的查询操作。订单状态流转由采购人员完成。 "
            '当用户需求涉及"下单"、"创建订单"、"修改采购单"、"更新订单"、 '
            '"订单状态查询"、"交期变更"等关键词时，应委派给此子 Agent。'
        ),
        "system_prompt": PROCUREMENT_ORDER_PROMPT,
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
        "system_prompt": PROCUREMENT_ORCHESTRATOR_PROMPT,
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

    # ---- Phase 4: 主 Agent 中间件栈 ----
    main_middleware = [
        GuardrailResponseMiddleware(),
        # 1. 沙箱健康守护：每次 agent step 前 ping → 失败自动恢复
        SandboxHealthMiddleware(
            sandbox_backend=sandbox_backend,
            user_id=user_id,
            agents_md_content=agents_md_bytes,
        ),
        # 2. 工具错误捕获：wrap_tool_call → ToolMessage(status="error")，防止单工具崩溃
        ToolErrorMiddleware(),
        # 3. 用户上下文注入
        UserInfoInjectionMiddleware(),
        # 4. 技能同步（本地 → 沙箱）
        skills_sync,
        # 5. 持久化技能恢复（StoreBackend → 沙箱）
        skills_restore,
        # 6. 对话摘要
        create_summarization_tool_middleware(SUMMARY_MODEL, backend),
        # 7. 沙箱熔断：连续沙箱错误 ≥ 阈值 → jump_to=end
        SandboxCircuitBreakerMiddleware(),
        # 8. 调用限制
        ModelCallLimitMiddleware(run_limit=50),
        # 同一轮只能委派一次。子 Agent 已返回后，主 Agent 必须整理结果并回答，
        # 防止模型重复委派甚至误派到另一个业务 Agent。
        ToolCallLimitMiddleware(
            tool_name="task", run_limit=1, exit_behavior="continue",
        ),
        ToolCallLimitMiddleware(run_limit=200),
    ]

    # ---- Phase 5: create_deep_agent ----
    agent_graph = create_deep_agent(
        model=MAIN_MODEL,
        system_prompt=system_prompt,
        skills=["/skills/main/"],
        memory=[AGENTS_MD_FILENAME],
        tools=[web_search, assign_skill, download_sandbox_file],
        subagents=[procurement_orchestrator, procurement_analyst, procurement_order],
        middleware=main_middleware,
        backend=backend,
        store=STORE,
        checkpointer=mysql_storage.checkpointer,
        context_schema=ProcurementContext,
    )

    logger.debug("Agent Graph 就绪：user=%s sandbox=%s", user_id, sandbox_backend.id)
    return agent_graph
