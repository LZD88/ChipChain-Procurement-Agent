"""Prometheus 指标与可选 OpenTelemetry 自动埋点。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("agent_requests_total", "Agent 流请求数", ("endpoint", "status"))
# Agent 请求包含沙箱冷启动和多轮工具调用，需要覆盖分钟级耗时。
LATENCY_BUCKETS = (.005, .01, .025, .05, .075, .1, .25, .5, .75, 1, 2.5, 5,
                   10, 20, 30, 60, 90, 120, 180, 300, 600)
REQUEST_DURATION = Histogram("agent_request_duration_seconds", "Agent 流请求耗时", ("endpoint",), buckets=LATENCY_BUCKETS)
ACTIVE_REQUESTS = Gauge("agent_active_requests", "当前活跃 Agent 请求", ("endpoint",))
FIRST_TOKEN = Histogram("agent_first_token_seconds", "首 Token 延迟", ("endpoint", "model"), buckets=LATENCY_BUCKETS)
STREAM_CHARS = Counter("agent_stream_characters_total", "SSE 输出字符数", ("model",))
TOOL_CALLS = Counter("agent_tool_calls_total", "工具调用数", ("tool", "status"))
LLM_INPUT_TOKENS = Counter("agent_llm_input_tokens_total", "LLM 输入 Token", ("model",))
LLM_OUTPUT_TOKENS = Counter("agent_llm_output_tokens_total", "LLM 输出 Token", ("model",))
LLM_COST_USD = Counter("agent_llm_cost_usd_total", "估算模型成本（美元）", ("model",))

# 在首个请求之前暴露零值，Prometheus 才能计算首次调用的增量。
_DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
for _endpoint in ("chat", "resume"):
    REQUEST_DURATION.labels(_endpoint)
    ACTIVE_REQUESTS.labels(_endpoint)
    FIRST_TOKEN.labels(_endpoint, _DEFAULT_MODEL)
    for _status in ("success", "failure", "interrupted", "cancelled"):
        REQUESTS.labels(_endpoint, _status)
for _counter in (LLM_INPUT_TOKENS, LLM_OUTPUT_TOKENS, LLM_COST_USD):
    _counter.labels(_DEFAULT_MODEL)


def _model_name(token) -> str:
    metadata = getattr(token, "response_metadata", None) or {}
    return str(metadata.get("model_name") or metadata.get("model") or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))


def _usage(token) -> tuple[int, int]:
    usage = getattr(token, "usage_metadata", None) or {}
    metadata = getattr(token, "response_metadata", None) or {}
    usage = usage or metadata.get("token_usage") or metadata.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(input_tokens), int(output_tokens)


@dataclass
class StreamObservation:
    endpoint: str
    started: float = 0.0
    first_token_seen: bool = False
    model: str = "deepseek-chat"
    input_tokens: int = 0
    output_tokens: int = 0
    closed: bool = False

    def start(self) -> "StreamObservation":
        self.started = time.perf_counter()
        ACTIVE_REQUESTS.labels(self.endpoint).inc()
        return self

    def token(self, token, text: str = "") -> None:
        self.model = _model_name(token)
        if text:
            STREAM_CHARS.labels(self.model).inc(len(text))
            if not self.first_token_seen:
                FIRST_TOKEN.labels(self.endpoint, self.model).observe(time.perf_counter() - self.started)
                self.first_token_seen = True
        input_tokens, output_tokens = _usage(token)
        # LangChain 通常只在最后一个 chunk 返回累计 usage，保留最大值避免重复累计。
        self.input_tokens = max(self.input_tokens, input_tokens)
        self.output_tokens = max(self.output_tokens, output_tokens)

    def tool(self, name: str, status: str) -> None:
        TOOL_CALLS.labels(name or "unknown", status).inc()

    def finish(self, status: str) -> None:
        if self.closed:
            return
        self.closed = True
        elapsed = time.perf_counter() - self.started
        ACTIVE_REQUESTS.labels(self.endpoint).dec()
        REQUEST_DURATION.labels(self.endpoint).observe(elapsed)
        REQUESTS.labels(self.endpoint, status).inc()
        LLM_INPUT_TOKENS.labels(self.model).inc(self.input_tokens)
        LLM_OUTPUT_TOKENS.labels(self.model).inc(self.output_tokens)
        input_price = float(os.getenv("DEEPSEEK_INPUT_USD_PER_M_TOKENS", "0.28"))
        output_price = float(os.getenv("DEEPSEEK_OUTPUT_USD_PER_M_TOKENS", "0.42"))
        cost = (self.input_tokens * input_price + self.output_tokens * output_price) / 1_000_000
        LLM_COST_USD.labels(self.model).inc(cost)


def configure_opentelemetry(app) -> None:
    """有 OTLP 地址时启用 FastAPI/httpx 自动追踪；缺少组件时不阻断应用。"""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "chipchain-procurement-agent")
        }))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
    except Exception:
        # 可观测性必须 fail-open，Collector 暂停时业务仍可运行。
        return
