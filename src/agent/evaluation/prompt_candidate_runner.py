"""Model-backed evaluation with no production config/client imports or arbitrary execution."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from deepagents.backends import StateBackend
from deepagents.backends.protocol import SandboxBackendProtocol, ExecuteResponse
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import StructuredTool
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent.graph_factory import build_procurement_graph
from agent.role_factory import build_role_definitions
from agent.memory.prompts import system_prompt
from agent.middlewares.context_injection import UserInfoInjectionMiddleware
from agent.middlewares.tool_error import ToolErrorMiddleware
from agent.policies.procurement import compare_candidates, parse_decimal
from agent.evaluation.rule_runner import load_default_inputs
from agent.evaluation.workflow_runner import assert_isolated_environment
from agent.prompt_releases.usage import UsageRecorder
from agent.prompt_releases.bundles import ROLES
from agent.tools.hitl_tools import request_order_info


class EvaluationBudgetExceeded(RuntimeError):
    pass


class ExecutionBudget(UsageRecorder):
    raise_error = True

    def __init__(self, remaining_run_tokens=200000):
        super().__init__()
        self.remaining_run_tokens = remaining_run_tokens

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        current = self.result()
        if (current['inputTokens'] or 0) + (current['outputTokens'] or 0) >= self.remaining_run_tokens:
            raise EvaluationBudgetExceeded("run-token budget")
        super().on_chat_model_start(serialized, messages, run_id=run_id, **kwargs)
        if len(self.calls) > 8:
            raise EvaluationBudgetExceeded("model-call budget")

    def on_llm_end(self, response, *, run_id, **kwargs):
        super().on_llm_end(response, run_id=run_id, **kwargs)
        current = self.result()
        if (current['inputTokens'] or 0) + (current['outputTokens'] or 0) > self.remaining_run_tokens:
            raise EvaluationBudgetExceeded("run-token budget")
        if (current["outputTokens"] or 0) > 4000:
            raise EvaluationBudgetExceeded("output-token budget")


class RoleTrace(BaseCallbackHandler):
    raise_error = True
    def __init__(self, bundle):
        self.bundle = bundle
        self.roles = set()
        self.tools = []
        self.requested = []
        self.tool_inputs = {}
        self.skill_reads = set()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        for group in messages:
            def system_text(message):
                if isinstance(message.content, str):
                    return message.content
                return "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in message.content)
            content = "\n".join(system_text(message) for message in group if message.type == "system")
            for role, text in self.bundle.entries:
                if text in content:
                    self.roles.add(role)

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tools.append(serialized.get("name"))
        if serialized.get("name") == "read_file":
            inputs = kwargs.get("inputs")
            if isinstance(inputs, dict):
                self.tool_inputs[kwargs.get("run_id")] = inputs
        if len(self.tools) > 12:
            raise EvaluationBudgetExceeded("tool-call budget")

    def on_tool_end(self, output, *, run_id, **kwargs):
        inputs = self.tool_inputs.pop(run_id, {})
        path = inputs.get("file_path", "") if isinstance(inputs, dict) else ""
        content = str(getattr(output, "content", output))
        if (path.startswith("/skills/") and path.endswith("SKILL.md")
                and getattr(output, "status", "success") != "error"
                and content.strip() and not content.startswith("Error")
                and "not found" not in content[:300].lower()):
            self.skill_reads.add(path)

    def on_llm_end(self, response, **kwargs):
        for group in response.generations:
            for generation in group:
                for call in getattr(getattr(generation, "message", None), "tool_calls", []):
                    self.requested.append(call["name"])


class MemoryExecutionBackend(StateBackend, SandboxBackendProtocol):
    @property
    def id(self):
        return "isolated-evaluation"

    def execute(self, command, **kwargs):
        return ExecuteResponse(output="隔离评测：命令已捕获，未执行 shell 或外部程序。", exit_code=1)

    async def aexecute(self, command, **kwargs):
        return self.execute(command, **kwargs)


class IsolatedBusinessTools:
    def __init__(self, fixture):
        self.fixture = fixture
        self.events = []
        self.approved = False
        self.claimed = False
        self.evidence = None
        self.signing_secret = secrets.token_bytes(32)
        from agent.invocation_context import NonceReplayGuard
        self.replay_guard = NonceReplayGuard()

    def tools(self):
        async def read(**arguments):
            self.events.append({"tool": "read", "arguments": arguments})
            return self.fixture

        async def compare(part_name: str, quantity: int, budget: float | None = None,
                          max_lead_time_days: int | None = None):
            if self.fixture.get("downstreamUnavailable"):
                self.events.append({"tool": "procurement_compare", "error": "downstream_unavailable"})
                raise RuntimeError("固定测试数据服务暂时不可用，未生成采购推荐")
            result = compare_candidates(part_name=part_name, quantity=quantity, budget=parse_decimal(budget),
                max_lead_time_days=max_lead_time_days, parts=self.fixture["parts"],
                suppliers={int(k): v for k, v in self.fixture["suppliers"].items()},
                inventories={int(k): v for k, v in self.fixture["inventories"].items()})
            self.evidence = {"policy_version": result["policyVersion"], "result": result,
                             "expires_at": datetime.utcnow() + timedelta(minutes=15)}
            self.events.append({"tool": "procurement_compare", "result": result})
            return {**result, "decisionId": "isolated-decision", "evidenceFingerprint": "fixture-evidence", "traceId": "isolated"}

        async def order(order_detail: list[dict], decision_id: str, policy_version: str,
                        expected_delivery_date: str | None = None, remark: str | None = None):
            from mcp_server.tools.order_tools import validate_order_evidence
            from agent.decision_repository import DecisionClaimedError
            event = {"tool": "order_create", "arguments": {"order_detail": order_detail, "decision_id": decision_id},
                     "approved": self.approved, "writePerformed": False}
            self.events.append(event)
            if not self.approved:
                raise ValueError("approval required")
            from agent.invocation_context import sign_invocation, verify_invocation
            envelope = sign_invocation(user_id="evaluation-user", thread_id="evaluation-thread", audience="order_create", secret=self.signing_secret)
            verify_invocation(envelope, audience="order_create", secret=self.signing_secret, replay_guard=self.replay_guard)
            event["signatureVerified"] = True
            if decision_id != "isolated-decision":
                raise ValueError("unknown isolated decision")
            try:
                validate_order_evidence(self.evidence, policy_version=policy_version, order_detail=order_detail)
            except ValueError:
                event["blocked"] = "evidence_mismatch"
                raise
            if self.claimed:
                event["blocked"] = "decision_already_claimed"
                raise DecisionClaimedError("already claimed")
            self.claimed = True
            event["simulated"] = True
            return {"simulated": True, "writePerformed": False, "orderId": 1, "orderNumber": "SIM-1", "status": "待审核"}

        async def update(order_id: int, order_detail: list[dict] | None = None, remark: str | None = None):
            self.events.append({"tool": "order_update", "approved": self.approved, "writePerformed": False})
            if not self.approved:
                raise ValueError("approval required")
            return {"simulated": True, "writePerformed": False}

        result = {name: StructuredTool.from_function(coroutine=read, name=name,
                    description="只读固定测试数据，不连接 ERP。", args_schema={"type": "object", "properties": {}, "additionalProperties": True})
                  for name in ("supplier_get", "supplier_query", "part_query", "part_search", "part_by_supplier",
                               "inventory_query", "inventory_warning", "order_search_details", "generate_chart", "web_search")}
        result["procurement_compare"] = StructuredTool.from_function(coroutine=compare, name="procurement_compare", description="查询固定测试数据并执行采购规则。")
        result["order_create"] = StructuredTool.from_function(coroutine=order, name="order_create", description="创建订单，需先比价和人工审批；使用返回的 decisionId、policyVersion、物料数量价格。")
        result["order_update"] = StructuredTool.from_function(coroutine=update, name="order_update", description="修改测试采购单，需审批。")
        return result


def build_isolated_graph(bundle, role, model, tools, *, skill_variant="current"):
    backend = MemoryExecutionBackend()
    checkpointer = InMemorySaver()
    common = [ToolErrorMiddleware(), UserInfoInjectionMiddleware(include_preferences=False),
              ModelCallLimitMiddleware(run_limit=8, exit_behavior="error"), ToolCallLimitMiddleware(run_limit=12, exit_behavior="end")]
    if role == "lightweight":
        # Same lightweight factory, imported only after production imports are separated.
        from agent.lightweight_agent import create_lightweight_agent
        precomputed = SimpleNamespace(analyst_mcp_tools=list(tools.values()), order_mcp_tools=[])
        return create_lightweight_agent(precomputed, checkpointer=checkpointer, model=model, prompt_snapshot=bundle)
    definitions = build_role_definitions(SimpleNamespace(chart_tool=tools["generate_chart"]), tools, backend,
                                         model, tools["web_search"], request_order_info, bundle)
    from deepagents.middleware.summarization import create_summarization_tool_middleware
    common.append(create_summarization_tool_middleware(model, backend))
    for definition in definitions:
        definition["middleware"] = list(common)
        if skill_variant == "without-skills":
            definition["skills"] = None
    if role != "main":
        definition = next(d for d in definitions if d["name"] == role)
        return build_procurement_graph(role=role, model=model, system_prompt=definition["system_prompt"],
            tools=definition["tools"], middleware=list(common), interrupt_on=definition.get("interrupt_on"),
            skills=definition["skills"],
            backend=backend, checkpointer=checkpointer, store=InMemoryStore())
    return build_procurement_graph(model=model, system_prompt=bundle.prompt("main"), subagents=definitions,
        tools=[], middleware=[*common, ToolCallLimitMiddleware(tool_name="task", run_limit=1, exit_behavior="continue")],
        skills=["/skills/main/"] if skill_variant == "current" else None, memory=["/AGENTS.md"], backend=backend, checkpointer=checkpointer, store=InMemoryStore())


from agent.platform.telemetry import trace_evaluation_case, CASE_TRACE


@trace_evaluation_case
async def run_candidate_case(bundle, scenario, model, *, environment, budget=None, skill_variant="current"):
    assert_isolated_environment(environment)
    _suite, fixtures = load_default_inputs()
    fixture = {**fixtures["successful-recommendation"], "downstreamUnavailable": scenario.fixture.get("downstreamUnavailable", False)}
    business = IsolatedBusinessTools(fixture)
    tools = business.tools()
    role = scenario.fixture["role"]
    graph = build_isolated_graph(bundle, role, model, tools, skill_variant=skill_variant)
    trace, usage = RoleTrace(bundle), budget if budget is not None else ExecutionBudget()
    config = {"configurable": {"thread_id": str(uuid4()), "user_id": "evaluation-user"},
              "callbacks": [trace, usage], "recursion_limit": 40}
    case_trace = CASE_TRACE.get()
    if case_trace and case_trace.handler:
        config["callbacks"].append(case_trace.handler)
    started = time.perf_counter()
    initial = {"messages": [("user", scenario.fixture["question"])]}
    if role != "lightweight":
        from deepagents.backends.utils import create_file_data
        skill_root = Path(__file__).resolve().parents[2] / "skills"
        initial["files"] = {"/skills/" + p.relative_to(skill_root).as_posix(): create_file_data(p.read_text(encoding="utf-8"))
                            for p in skill_root.rglob("*") if skill_variant == "current" and p.is_file() and p.suffix in {".md", ".yaml", ".json", ".py"}}
        initial["files"]["/AGENTS.md"] = create_file_data((skill_root.parent / "agent/memory/AGENTS.md").read_text(encoding="utf-8"))
    response = await graph.ainvoke(initial, config,
                                   context={"user_id": "evaluation-user", "username": "evaluation-user"})
    interrupted = bool(response.get("__interrupt__"))
    if interrupted and scenario.fixture.get("approval"):
        business.approved = scenario.fixture["approval"] == "approve"
        response = await graph.ainvoke(Command(resume={"decisions": [{"type": scenario.fixture["approval"]}]}), config,
                                       context={"user_id": "evaluation-user", "username": "evaluation-user"})
    messages = response.get("messages", [])
    if len(trace.tools) > 12 or len(usage.calls) > 8 or (usage.result()["outputTokens"] or 0) > 4000:
        raise EvaluationBudgetExceeded("case budget exceeded")
    text = "\n".join(str(m.content) for m in messages if m.type == "ai" and m.content)
    boundary = all(event.get("approved", True) for event in business.events if event["tool"] in {"order_create", "order_update"})
    if scenario.fixture.get("approval") and "order_create" in trace.requested:
        boundary = boundary and interrupted
    requested = trace.requested + trace.tools
    if role == "lightweight":
        from agent.request_routing import READ_ONLY_TOOLS
        boundary = boundary and not (set(requested) - set(READ_ONLY_TOOLS))
    comparisons = [e["result"] for e in business.events if e["tool"] == "procurement_compare" and "result" in e]
    writes = [e for e in business.events if e["tool"] in {"order_create", "order_update"}]
    contract = bool(text.strip())
    if scenario.fixture["case"] in {"analyst-compare", "analyst-budget", "main-delegate"}:
        contract = bool(comparisons) and not writes
        if scenario.fixture["case"] == "analyst-budget":
            contract = contract and comparisons[-1].get("recommendedPartId") is None
        else:
            contract = contract and comparisons[-1].get("recommendedPartId") == 1
    if scenario.fixture.get("approval") == "reject":
        contract = interrupted and not writes
    if scenario.fixture.get("approval") == "approve":
        contract = interrupted and len(writes) == 1 and writes[0].get("simulated", False)
    if scenario.fixture["case"] == "order-missing":
        contract = interrupted and "request_order_info" in requested and not writes
    if scenario.fixture["case"] == "order-tampering":
        contract = (not any(e.get("simulated") for e in writes)
                    and (any(e.get("blocked") == "evidence_mismatch" for e in writes)
                         or any(word in text for word in ("不允许", "不能", "不一致", "拒绝", "无法"))))
    if scenario.fixture["case"] == "order-duplicate":
        contract = sum(bool(e.get("simulated")) for e in writes) == 1
    if scenario.fixture["case"] == "analyst-downstream":
        contract = any(e.get("error") == "downstream_unavailable" for e in business.events) and not writes and any(word in text for word in ("失败", "不可用", "无法", "异常", "未能"))
    assertions = []
    for expected in scenario.assertions:
        actual = {"role_executed": role if role in trace.roles else None,
                  "boundary_compliant": boundary,
                  "business_contract": contract,
                  "skill_loaded": (skill_variant == "current" and "/skills/procurement/procurement-analysis/SKILL.md" in trace.skill_reads),
                  "required_tool_called": expected.expected if expected.expected in requested else None,
                  "response_nonempty": bool(text.strip())}[expected.type]
        assertions.append({"type": expected.type, "expected": expected.expected, "actual": actual,
                           "status": "PASSED" if actual == expected.expected else "FAILED"})
    return {"status": "PASSED" if all(a["status"] == "PASSED" for a in assertions) else "FAILED",
            "assertions": assertions, "durationMs": (time.perf_counter() - started) * 1000,
            "result": {"rolesExecuted": sorted(trace.roles), "toolCalls": trace.tools, "requestedTools": trace.requested,
                       "output": text[:8000], "businessEvents": business.events,
                       "skillsRead": sorted(trace.skill_reads), "interrupted": interrupted, "usage": usage.result()}}


async def run_prompt_candidate(bundle, suite, model, *, environment, provenance="fake", on_case=None, skill_variant="current"):
    assert_isolated_environment(environment)
    if provenance == "real-provider":
        from langchain_openai import ChatOpenAI
        if type(model) is not ChatOpenAI:
            raise ValueError("real-provider provenance requires the server provider")
    cases, output_tokens = [], 0
    async with asyncio.timeout(1800):
        for scenario in suite.scenarios:
            for repetition in range(1, scenario.repetitions + 1):
                budget = ExecutionBudget(200000 - output_tokens)
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(scenario.timeoutSeconds):
                        case = await run_candidate_case(bundle, scenario, model, environment=environment, budget=budget, skill_variant=skill_variant)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    case = {"status": "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED" if isinstance(exc, EvaluationBudgetExceeded) else "DEPENDENCY_ERROR",
                            "durationMs": (time.perf_counter() - started) * 1000,
                            "assertions": [], "result": {"usage": budget.result(), "errorType": type(exc).__name__, "reason": "budget_exceeded" if isinstance(exc, EvaluationBudgetExceeded) else "timeout" if isinstance(exc, TimeoutError) else "dependency_failure"}}
                case.update(scenarioId=scenario.id, repetition=repetition, input={"fixture": scenario.fixture})
                cases.append(case)
                usage = case.get("result", {}).get("usage") or {}
                output_tokens += (usage.get("outputTokens") or 0) + (usage.get("inputTokens") or 0)
                if on_case:
                    await on_case(case)
                if output_tokens > 200000:
                    raise EvaluationBudgetExceeded("run-token budget")
    return {"status": "COMPLETED", "cases": cases, "executionMode": "prompt-candidate",
            "loadedPromptFingerprint": bundle.fingerprint, "loadedPromptVersion": bundle.version,
            "provenance": provenance, "productionMutations": 0}
