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
    prompt_snapshot=None,
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
    from agent.role_factory import build_role_definitions
    role_definitions = build_role_definitions(precomputed, mcp_tools, backend, SUMMARY_MODEL,
                                               web_search, request_order_info, prompt_snapshot)

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
    from agent.graph_factory import build_procurement_graph
    agent_graph = build_procurement_graph(
        factory=create_deep_agent,
        model=MAIN_MODEL,
        system_prompt=prompt_snapshot.prompt("main") if prompt_snapshot else system_prompt,
        skills=["/skills/main/"],
        memory=[AGENTS_MD_FILENAME, f"/memories/{user_id}/preferences.md"],
        tools=[web_search, assign_skill, download_sandbox_file],
        subagents=role_definitions,
        middleware=main_middleware,
        backend=backend,
        store=STORE,
        checkpointer=mysql_storage.checkpointer,
        context_schema=ProcurementContext,
    )

    logger.debug("Agent Graph 就绪：user=%s sandbox=%s", user_id, sandbox_backend.id)
    return agent_graph
