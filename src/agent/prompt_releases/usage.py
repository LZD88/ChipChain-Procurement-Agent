"""Per-model-call usage, including subagents, with idempotent final chunks."""
import os
import threading
import time

from langchain_core.callbacks import BaseCallbackHandler
from .bundles import digest


class UsageRecorder(BaseCallbackHandler):
    def __init__(self):
        self.started = time.perf_counter()
        self.calls = {}
        self.lock = threading.Lock()
        self.first_token_seconds = None

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        with self.lock:
            self.calls.setdefault(str(run_id), None)

    def on_llm_new_token(self, token, **kwargs):
        if token and self.first_token_seconds is None:
            self.first_token_seconds = time.perf_counter() - self.started

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = None
        for group in response.generations:
            for generation in group:
                message = getattr(generation, "message", None)
                current = getattr(message, "usage_metadata", None)
                if current:
                    usage = (int(current.get("input_tokens", 0)), int(current.get("output_tokens", 0)))
        if usage is None:
            current = (getattr(response, "llm_output", None) or {}).get("token_usage")
            if current:
                usage = (int(current.get("prompt_tokens", 0)), int(current.get("completion_tokens", 0)))
        with self.lock:
            self.calls[str(run_id)] = usage

    def result(self):
        with self.lock:
            values = list(self.calls.values())
        known = [v for v in values if v is not None]
        input_tokens = sum(v[0] for v in known)
        output_tokens = sum(v[1] for v in known)
        prices = [float(os.getenv("DEEPSEEK_INPUT_USD_PER_M_TOKENS", ".28")),
                  float(os.getenv("DEEPSEEK_OUTPUT_USD_PER_M_TOKENS", ".42"))]
        return {"durationSeconds": time.perf_counter() - self.started, "firstTokenSeconds": self.first_token_seconds,
                "modelCalls": len(values), "usageCalls": len(known),
                "usageState": "complete" if values and len(known) == len(values) else "partial" if known else "unavailable",
                "inputTokens": input_tokens if known else None, "outputTokens": output_tokens if known else None,
                "estimatedCostUsd": (input_tokens * prices[0] + output_tokens * prices[1]) / 1000000 if known else None,
                "priceFingerprint": digest(prices)}
