"""Redacted structured telemetry for LangGraph executions."""

import json
import logging
from typing import Any

from app.observability.metrics import agent_telemetry_aggregator

logger = logging.getLogger("app.agent.telemetry")


def emit_agent_event(
    *,
    trace_id: str,
    event: str,
    outcome: str,
    duration_ms: float | None = None,
    mode: str | None = None,
    node: str | None = None,
    tool_name: str | None = None,
    error_category: str | None = None,
) -> None:
    """Emit only allowlisted, non-content telemetry fields."""
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "event": event,
        "outcome": outcome,
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 3)
    if mode is not None:
        payload["mode"] = mode
    if node is not None:
        payload["node"] = node
    if tool_name is not None:
        payload["tool_name"] = tool_name
    if error_category is not None:
        payload["error_category"] = error_category
    safe_payload = agent_telemetry_aggregator.sanitize_event(payload)
    try:
        agent_telemetry_aggregator.record(safe_payload)
    except Exception:
        logger.warning("Agent telemetry aggregation failed.")
    try:
        logger.info(
            "agent_telemetry %s",
            json.dumps(safe_payload, sort_keys=True),
        )
    except Exception:
        logger.warning("Agent telemetry log emission failed.")
