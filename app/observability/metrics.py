"""Bounded in-process aggregation for redacted agent telemetry."""

from collections import Counter, OrderedDict, defaultdict, deque
import re
from threading import Lock
from typing import Any

MAX_LATENCY_SAMPLES = 5000
MAX_TRACE_SUMMARIES = 500
MAX_NODE_NAMES = {
    "plan",
    "authorize_plan",
    "retrieve_documents",
    "execute_structured_tools",
    "aggregate_results",
    "generate_answer",
}
VALID_MODES = {"rag", "structured", "mixed", "unsupported"}
VALID_ERRORS = {
    "authorization_denied",
    "data_unavailable",
    "documents_read_denied",
    "graph_failure",
    "invalid_arguments",
    "invalid_model_json",
    "invalid_model_output",
    "invalid_model_schema",
    "invalid_plan",
    "invalid_tool_plan",
    "model_timeout",
    "model_unavailable",
    "node_failure",
    "permission_denied",
    "provider_authentication",
    "provider_bad_request",
    "provider_error",
    "provider_misconfigured",
    "provider_rate_limit",
    "provider_timeout",
    "provider_unavailable",
    "planning_unavailable",
    "rag_generation_failure",
    "retrieval_unavailable",
    "retry_succeeded",
    "tool_unavailable",
}
VALID_EVENTS = {
    "final_response",
    "graph_execution",
    "model_call",
    "node_execution",
    "permission_check",
    "rag_execution",
    "tool_execution",
    "tool_selection",
}
VALID_OUTCOMES = {
    "abstention",
    "allowed",
    "completed",
    "denied",
    "failure",
    "no_results",
    "ok",
    "selected",
    "success",
}
VALID_PROVIDERS = {"groq", "ollama"}
SAFE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")
VALID_TOOLS = {
    "get_department_performance_summary",
    "get_employee_details",
    "get_employee_performance_metrics",
    "get_employee_performance_summary",
    "get_employee_tasks",
    "get_overdue_tasks",
    "get_performance_metrics",
    "get_project_details",
    "get_project_status",
    "get_project_tasks",
    "get_project_team",
    "get_task_details",
    "search_employees",
    "search_tasks",
}


def _percentile(samples: list[float], percentile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        3,
    )


def _latency_summary(samples: deque[float]) -> dict[str, float | int | None]:
    values = list(samples)
    return {
        "sample_count": len(values),
        "average_ms": (
            round(sum(values) / len(values), 3) if values else None
        ),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
    }


class AgentTelemetryAggregator:
    """Keep aggregate counters and a bounded set of recent trace summaries."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[str] = Counter()
        self._by_mode: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._model_providers: Counter[str] = Counter()
        self._models: Counter[str] = Counter()
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=MAX_LATENCY_SAMPLES)
        )
        self._traces: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @staticmethod
    def sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
        """Retain only bounded, low-cardinality telemetry dimensions."""
        raw_event = event.get("event")
        raw_outcome = event.get("outcome")
        safe: dict[str, Any] = {
            "trace_id": (
                event["trace_id"][:64]
                if isinstance(event.get("trace_id"), str)
                else "unknown"
            ),
            "event": (
                raw_event
                if isinstance(raw_event, str) and raw_event in VALID_EVENTS
                else "unknown"
            ),
            "outcome": (
                raw_outcome
                if isinstance(raw_outcome, str)
                and raw_outcome in VALID_OUTCOMES
                else "other"
            ),
        }
        mode = event.get("mode")
        if isinstance(mode, str) and mode in VALID_MODES:
            safe["mode"] = mode
        node = event.get("node")
        if isinstance(node, str) and node in MAX_NODE_NAMES:
            safe["node"] = node
        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name in VALID_TOOLS:
            safe["tool_name"] = tool_name
        error = event.get("error_category")
        if isinstance(error, str):
            safe["error_category"] = (
                error if error in VALID_ERRORS else "other"
            )
        provider = event.get("provider")
        if isinstance(provider, str) and provider in VALID_PROVIDERS:
            safe["provider"] = provider
        model = event.get("model")
        if isinstance(model, str) and SAFE_MODEL_PATTERN.fullmatch(model):
            safe["model"] = model
        duration = event.get("duration_ms")
        if (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and 0 <= duration <= 600_000
        ):
            safe["duration_ms"] = round(float(duration), 3)
        return safe

    def record(self, event: dict[str, Any]) -> None:
        safe = self.sanitize_event(event)
        with self._lock:
            self._record_counters(safe)
            self._record_latency(safe)
            self._record_trace(safe)

    def _record_counters(self, event: dict[str, Any]) -> None:
        event_name = event["event"]
        outcome = event["outcome"]
        error = event.get("error_category")
        if error:
            self._errors[error] += 1
        if event_name == "graph_execution":
            self._counters["graph_executions"] += 1
            mode = event.get("mode")
            if mode:
                self._by_mode[mode] += 1
            if outcome in {"success", "abstention"}:
                self._counters["graph_successes"] += 1
            elif outcome == "denied":
                self._counters["graph_denials"] += 1
            else:
                self._counters["graph_failures"] += 1
        elif event_name == "rag_execution":
            self._counters["rag_executions"] += 1
            self._counters[
                "rag_successes" if outcome in {"success", "no_results"}
                else "rag_failures"
            ] += 1
        elif event_name == "tool_execution":
            self._counters["structured_tool_executions"] += 1
            self._counters[
                "structured_tool_successes" if outcome == "ok"
                else "structured_tool_failures"
            ] += 1
        elif event_name == "model_call":
            provider = event.get("provider", "ollama")
            self._model_providers[provider] += 1
            model = event.get("model")
            if model:
                self._models[model] += 1
            prefix = "groq" if provider == "groq" else "ollama"
            self._counters[f"{prefix}_calls"] += 1
            if outcome == "success":
                self._counters[f"{prefix}_successes"] += 1
            else:
                self._counters[f"{prefix}_failures"] += 1
                if error in {"model_timeout", "provider_timeout"}:
                    self._counters[f"{prefix}_timeouts"] += 1

    def _record_latency(self, event: dict[str, Any]) -> None:
        duration = event.get("duration_ms")
        if duration is None:
            return
        if event["event"] == "node_execution":
            node = event.get("node")
            if node:
                self._latencies[f"node:{node}"].append(duration)
        elif event["event"] == "graph_execution":
            self._latencies["graph"].append(duration)

    def _record_trace(self, event: dict[str, Any]) -> None:
        trace_id = event["trace_id"]
        trace = self._traces.setdefault(
            trace_id,
            {"trace_id": trace_id},
        )
        self._traces.move_to_end(trace_id)
        if "mode" in event:
            trace["mode"] = event["mode"]
        if "provider" in event:
            trace["provider"] = event["provider"]
        if "model" in event:
            trace["model"] = event["model"]
        if (
            event["event"] == "tool_selection"
            and event.get("tool_name")
        ):
            selected = trace.setdefault("selected_tools", [])
            if event["tool_name"] not in selected:
                selected.append(event["tool_name"])
        if (
            event["event"] == "tool_execution"
            and event.get("tool_name")
        ):
            outcomes = trace.setdefault("tool_outcomes", [])
            outcomes.append(
                {
                    "tool_name": event["tool_name"],
                    "outcome": event["outcome"],
                }
            )
        if event["event"] == "final_response":
            trace["outcome"] = event["outcome"]
        elif event["event"] == "graph_execution":
            trace["outcome"] = event["outcome"]
            trace["error_category"] = event.get("error_category")
            trace["duration_ms"] = event.get("duration_ms")
        if event.get("error_category"):
            trace["error_category"] = event["error_category"]
        while len(self._traces) > MAX_TRACE_SUMMARIES:
            self._traces.popitem(last=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            graph_executions = self._counters["graph_executions"]
            graph_successes = self._counters["graph_successes"]
            graph_failures = self._counters["graph_failures"]
            rag_executions = self._counters["rag_executions"]
            rag_successes = self._counters["rag_successes"]
            rag_failures = self._counters["rag_failures"]
            tool_executions = self._counters["structured_tool_executions"]
            tool_successes = self._counters["structured_tool_successes"]
            tool_failures = self._counters["structured_tool_failures"]
            ollama_calls = self._counters["ollama_calls"]
            ollama_successes = self._counters["ollama_successes"]
            ollama_failures = self._counters["ollama_failures"]
            return {
                "aggregation": "process_local_cumulative",
                "retained_trace_summaries": len(self._traces),
                "total_graph_executions": graph_executions,
                "execution_mode_distribution": dict(self._by_mode),
                "graph_outcomes": {
                    "success": graph_successes,
                    "failure": graph_failures,
                    "denied": self._counters["graph_denials"],
                },
                "graph_success_rate": (
                    round(graph_successes / graph_executions, 4)
                    if graph_executions
                    else None
                ),
                "graph_failure_rate": (
                    round(graph_failures / graph_executions, 4)
                    if graph_executions
                    else None
                ),
                "rag": {
                    "total": rag_executions,
                    "success": rag_successes,
                    "failure": rag_failures,
                    "success_rate": (
                        round(rag_successes / rag_executions, 4)
                        if rag_executions
                        else None
                    ),
                },
                "structured_tools": {
                    "total": tool_executions,
                    "success": tool_successes,
                    "failure": tool_failures,
                    "success_rate": (
                        round(tool_successes / tool_executions, 4)
                        if tool_executions
                        else None
                    ),
                },
                "ollama": {
                    "total": ollama_calls,
                    "success": ollama_successes,
                    "failure": ollama_failures,
                    "timeout": self._counters["ollama_timeouts"],
                    "success_rate": (
                        round(ollama_successes / ollama_calls, 4)
                        if ollama_calls
                        else None
                    ),
                },
                "groq": {
                    "total": self._counters["groq_calls"],
                    "success": self._counters["groq_successes"],
                    "failure": self._counters["groq_failures"],
                    "timeout": self._counters["groq_timeouts"],
                    "success_rate": (
                        round(
                            self._counters["groq_successes"]
                            / self._counters["groq_calls"],
                            4,
                        )
                        if self._counters["groq_calls"]
                        else None
                    ),
                },
                "model_providers": dict(self._model_providers),
                "models": dict(self._models),
                "error_categories": dict(self._errors),
                "latencies": {
                    key: _latency_summary(values)
                    for key, values in sorted(self._latencies.items())
                },
                "recent_traces": list(self._traces.values())[
                    -MAX_TRACE_SUMMARIES:
                ],
            }

    def reset(self) -> None:
        """Reset process metrics; intended for isolated test setup."""
        with self._lock:
            self._counters.clear()
            self._by_mode.clear()
            self._errors.clear()
            self._model_providers.clear()
            self._models.clear()
            self._latencies.clear()
            self._traces.clear()


agent_telemetry_aggregator = AgentTelemetryAggregator()


def get_agent_metrics() -> dict[str, Any]:
    """Return a safe snapshot for the protected internal metrics endpoint."""
    return agent_telemetry_aggregator.snapshot()
