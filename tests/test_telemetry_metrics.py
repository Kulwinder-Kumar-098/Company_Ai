"""Tests for protected and redacted agent telemetry aggregation."""

import json
import logging

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.observability_routes import router as observability_router
from app.core.config import settings
from app.core.database import get_db
from app.models import Base, Employee, Permission, Role
from app.observability.agent_telemetry import emit_agent_event
from app.observability.metrics import (
    AgentTelemetryAggregator,
    VALID_TOOLS,
    agent_telemetry_aggregator,
)
from app.tools.registry import TOOLS


def test_aggregator_calculates_counts_latency_and_trace_outcomes():
    aggregator = AgentTelemetryAggregator()
    trace_id = "00000000-0000-0000-0000-000000000001"

    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "graph_execution",
            "outcome": "success",
            "mode": "structured",
            "duration_ms": 40,
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "tool_selection",
            "outcome": "selected",
            "tool_name": "get_employee_details",
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "tool_execution",
            "outcome": "ok",
            "tool_name": "get_employee_details",
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "model_call",
            "outcome": "failure",
            "error_category": "model_timeout",
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "node_execution",
            "outcome": "completed",
            "node": "plan",
            "duration_ms": 10,
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "node_execution",
            "outcome": "completed",
            "node": "plan",
            "duration_ms": 20,
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "node_execution",
            "outcome": "completed",
            "node": "plan",
            "duration_ms": 30,
        }
    )
    aggregator.record(
        {
            "trace_id": trace_id,
            "event": "final_response",
            "outcome": "success",
        }
    )

    metrics = aggregator.snapshot()

    assert metrics["total_graph_executions"] == 1
    assert metrics["execution_mode_distribution"] == {"structured": 1}
    assert metrics["graph_outcomes"] == {
        "success": 1,
        "failure": 0,
        "denied": 0,
    }
    assert metrics["structured_tools"]["success"] == 1
    assert metrics["ollama"]["timeout"] == 1
    assert metrics["error_categories"] == {"model_timeout": 1}
    assert metrics["latencies"]["node:plan"] == {
        "sample_count": 3,
        "average_ms": 20.0,
        "p50_ms": 20.0,
        "p95_ms": 29.0,
    }
    assert metrics["latencies"]["graph"]["p50_ms"] == 40
    assert metrics["recent_traces"][0]["trace_id"] == trace_id
    assert metrics["recent_traces"][0]["outcome"] == "success"
    assert metrics["recent_traces"][0]["selected_tools"] == [
        "get_employee_details"
    ]
    assert metrics["recent_traces"][0]["tool_outcomes"] == [
        {"tool_name": "get_employee_details", "outcome": "ok"}
    ]


def test_smalltalk_execution_mode_is_retained_in_telemetry():
    aggregator = AgentTelemetryAggregator()
    aggregator.record(
        {
            "trace_id": "00000000-0000-0000-0000-000000000099",
            "event": "graph_execution",
            "outcome": "success",
            "mode": "smalltalk",
        }
    )

    assert aggregator.snapshot()["execution_mode_distribution"] == {
        "smalltalk": 1,
    }


def test_aggregator_redacts_unknown_and_sensitive_fields():
    aggregator = AgentTelemetryAggregator()
    confidential = "PRIVATE_SYNTHETIC_SENTINEL"
    aggregator.record(
        {
            "trace_id": "00000000-0000-0000-0000-000000000002",
            "event": "tool_execution",
            "outcome": "ok",
            "tool_name": confidential,
            "query": confidential,
            "arguments": {"secret": confidential},
            "answer": confidential,
            "mode": confidential,
            "error_category": confidential,
        }
    )

    snapshot = json.dumps(aggregator.snapshot(), sort_keys=True)

    assert confidential not in snapshot
    assert "query" not in snapshot
    assert "arguments" not in snapshot
    assert '"tool_name"' not in snapshot


def test_telemetry_tool_allowlist_tracks_the_registered_tools():
    assert VALID_TOOLS == set(TOOLS)


def test_telemetry_aggregation_failure_does_not_escape_or_log_payload(
    monkeypatch,
    caplog,
):
    private_marker = "PRIVATE_TELEMETRY_FAILURE_SENTINEL"

    def fail_record(_event):
        raise RuntimeError(private_marker)

    monkeypatch.setattr(agent_telemetry_aggregator, "record", fail_record)
    with caplog.at_level(logging.WARNING, logger="app.agent.telemetry"):
        emit_agent_event(
            trace_id="00000000-0000-0000-0000-000000000003",
            event="graph_execution",
            outcome="failure",
            error_category="graph_failure",
        )

    assert "Agent telemetry aggregation failed." in caplog.text
    assert private_marker not in caplog.text


@pytest.fixture
def metrics_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        metrics_permission = Permission(
            code="agent:metrics:read",
            description="Read agent metrics",
        )
        admin_role = Role(
            name="admin",
            permissions=[metrics_permission],
        )
        employee_role = Role(name="employee")
        db.add_all(
            [
                Employee(
                    employee_id="METRICS-ADMIN",
                    full_name="Synthetic Admin",
                    email="metrics-admin@example.test",
                    department="operations",
                    role=admin_role,
                    is_active=True,
                ),
                Employee(
                    employee_id="METRICS-EMPLOYEE",
                    full_name="Synthetic Employee",
                    email="metrics-employee@example.test",
                    department="operations",
                    role=employee_role,
                    is_active=True,
                ),
            ]
        )
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-" * 4)
    application = FastAPI()
    application.include_router(observability_router)
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client
    Base.metadata.drop_all(engine)
    engine.dispose()


def _token(employee_id: str) -> str:
    return jwt.encode(
        {"sub": employee_id},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def test_metrics_endpoint_requires_authentication_and_explicit_permission(
    metrics_client,
):
    missing_token = metrics_client.get("/internal/agent-metrics")
    employee_token = _token("METRICS-EMPLOYEE")
    employee_response = metrics_client.get(
        "/internal/agent-metrics",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    admin_token = _token("METRICS-ADMIN")
    admin_response = metrics_client.get(
        "/internal/agent-metrics",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert missing_token.status_code == 401
    assert employee_response.status_code == 403
    assert admin_response.status_code == 200
    assert "total_graph_executions" in admin_response.json()
