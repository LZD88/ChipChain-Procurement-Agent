"""Bounded, low-cardinality operational metrics for evaluation workers."""

from __future__ import annotations

from prometheus_client import Counter, Histogram


EXECUTION_MODES = frozenset({"rule-contract", "workflow-safety"})
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "PARTIAL", "CANCELLED", "TIMED_OUT"})
ENVIRONMENTS = frozenset({"local", "ci", "staging", "isolated-test"})
LABELS = ("execution_mode", "status", "environment")

EVALUATION_RUNS = Counter(
    "procurement_evaluation_runs_total",
    "Terminal procurement evaluation runs by bounded mode, status, and environment.",
    LABELS,
)
EVALUATION_DURATION = Histogram(
    "procurement_evaluation_run_duration_seconds",
    "Terminal procurement evaluation run duration by bounded mode, status, and environment.",
    LABELS,
    buckets=(.1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)


def observe_evaluation_run(*, execution_mode: str, status: str,
                           environment: str, duration_seconds: float) -> None:
    if execution_mode not in EXECUTION_MODES:
        raise ValueError("unknown evaluation execution mode")
    if status not in TERMINAL_STATUSES:
        raise ValueError("unknown evaluation terminal status")
    if environment not in ENVIRONMENTS:
        raise ValueError("unknown evaluation environment")
    if duration_seconds < 0:
        raise ValueError("evaluation duration cannot be negative")
    labels = (execution_mode, status, environment)
    EVALUATION_RUNS.labels(*labels).inc()
    EVALUATION_DURATION.labels(*labels).observe(duration_seconds)
