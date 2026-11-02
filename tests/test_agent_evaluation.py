"""Deterministic planner evaluation and telemetry tests."""

import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import orchestrator
from app.agents.workflows import PlannerEvaluationResult, RequestPlan
from app.evaluation.runner import (
    evaluate_dataset,
    fixture_predictor,
    load_dataset,
    render_report,
)
from app.models import AuditEvent, Base, Employee, Permission, Role


@pytest.fixture
def tool_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        permissions = [
            Permission(code="documents:read", description="Read documents"),
            Permission(
                code="employees:read:self",
                description="Read own employee record",
            ),
        ]
        role = Role(name="evaluation-employee", permissions=permissions)
        employee = Employee(
            employee_id="EVAL-SYNTH",
            full_name="Synthetic Evaluation User",
            email="evaluation@example.test",
            department="evaluation",
            role=role,
            is_active=True,
        )
        db.add(employee)
        db.commit()
        yield db, {"EMP-ONE": employee}
    Base.metadata.drop_all(engine)
    engine.dispose()


def _stub_agent_models(monkeypatch, plan):
    from app.agents.workflows import GeneratedAnswer

    def fake_model(*, output_schema, **_kwargs):
        if output_schema is RequestPlan:
            return plan if isinstance(plan, str) else plan.model_dump_json()
        return GeneratedAnswer(answer="Synthetic grounded answer.").model_dump_json()

    monkeypatch.setattr(orchestrator, "_request_model_json", fake_model)


def test_synthetic_evaluation_dataset_covers_required_categories():
    dataset = load_dataset()

    assert len(dataset.cases) == 21
    assert {case.category for case in dataset.cases} == {
        "document_only",
        "employee_structured",
        "project_structured",
        "task_structured",
        "performance_structured",
        "mixed",
        "unsupported",
        "ambiguous",
        "unavailable_tool",
        "authorization_sensitive",
    }
    assert len({case.case_id for case in dataset.cases}) == 21
    assert all("@" not in case.query for case in dataset.cases)


def test_fixture_evaluation_measures_invalid_plans_and_renders_summary():
    dataset = load_dataset()
    report = evaluate_dataset(
        dataset,
        fixture_predictor,
        evaluation_mode="deterministic_fixture",
    )
    markdown = render_report(report)

    assert report["model_quality_evaluated"] is False
    assert report["metrics"]["case_count"] == 21
    assert report["metrics"]["routing_accuracy"] < 1
    assert report["metrics"]["invalid_plan_rate"] > 0
    assert report["metrics"]["invalid_argument_rate"] > 0
    assert report["metrics"]["tool_selection_accuracy"] < 1
    assert (
        report["categories"]["document_only"]["routing_accuracy"] == 1
    )
    assert report["metrics"]["execution_failure_rate"] is None
    assert "Overall" in markdown
    assert "unavailable_tool" in markdown


def test_live_evaluation_calls_planner_once_per_case(monkeypatch):
    from app.evaluation import runner

    dataset = load_dataset()
    calls = []

    def fake_planner(query):
        calls.append(query)
        return PlannerEvaluationResult(
            RequestPlan(mode="unsupported"),
            None,
            (),
        )

    monkeypatch.setattr(runner, "evaluate_planner_query", fake_planner)
    report = evaluate_dataset(
        dataset,
        runner.live_predictor,
        evaluation_mode="live_ollama",
    )

    assert len(calls) == len(dataset.cases)
    assert report["model_quality_evaluated"] is True


def test_planner_only_evaluation_uses_mocked_ollama_output(monkeypatch):
    response = RequestPlan(mode="unsupported").model_dump_json()
    calls = []

    def fake_ollama(**kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(orchestrator, "_request_model_json", fake_ollama)
    monkeypatch.setattr(
        orchestrator,
        "execute_tool",
        lambda *_args, **_kwargs: pytest.fail(
            "planner-only evaluation must not execute tools"
        ),
    )

    result = orchestrator.evaluate_planner_query(
        "A synthetic unsupported question."
    )

    assert result.plan == RequestPlan(mode="unsupported")
    assert result.error_category is None
    assert len(calls) == 1
    assert "registered_tools" in calls[0]["user_prompt"]


def test_missing_ollama_structured_content_has_safe_error_category():
    with pytest.raises(orchestrator.ModelResponseError) as error:
        orchestrator._extract_message_content({"message": {"content": ""}})

    assert error.value.error_category == "invalid_model_output"
    assert "content" not in str(error.value)


def test_planner_only_evaluation_reports_malformed_model_output(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        orchestrator,
        "_request_model_json",
        lambda **_kwargs: attempts.append(True) or "{not valid json",
    )

    result = orchestrator.evaluate_planner_query(
        "A synthetic malformed-output question."
    )

    assert len(attempts) == orchestrator.MAX_MODEL_ATTEMPTS
    assert result.plan is None
    assert result.error_category == "invalid_model_json"


def test_planner_only_evaluation_categorizes_valid_json_wrong_schema(
    monkeypatch,
):
    attempts = []
    monkeypatch.setattr(
        orchestrator,
        "_request_model_json",
        lambda **_kwargs: attempts.append(True)
        or '{"mode":"unsupported","tool_calls":[],"unexpected":true}',
    )

    result = orchestrator.evaluate_planner_query(
        "A synthetic invalid-schema question."
    )

    assert len(attempts) == orchestrator.MAX_MODEL_ATTEMPTS
    assert result.plan is None
    assert result.error_category == "invalid_model_schema"


def test_planner_only_evaluation_categorizes_ollama_timeout(monkeypatch):
    attempts = []

    def timeout(**_kwargs):
        attempts.append(True)
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(orchestrator, "_request_model_json", timeout)
    result = orchestrator.evaluate_planner_query(
        "A synthetic timeout question."
    )

    assert len(attempts) == orchestrator.MAX_MODEL_ATTEMPTS
    assert result.plan is None
    assert result.error_category == "model_timeout"


def test_live_evaluation_reports_planner_failures_and_category_metrics(
    monkeypatch,
):
    from app.evaluation import runner

    def fail_for_one_case(case):
        if case.case_id == "ambiguous-task-001":
            raise RuntimeError("sensitive exception text")
        return PlannerEvaluationResult(
            RequestPlan(
                mode=case.expected_mode,
                tool_calls=[
                    {"name": name, "arguments": {}}
                    for name in case.expected_tool_names
                ],
            ),
            None,
            tuple(case.expected_tool_names),
        )

    monkeypatch.setattr(runner, "live_predictor", fail_for_one_case)
    report = evaluate_dataset(
        load_dataset(),
        runner.live_predictor,
        evaluation_mode="live_ollama",
    )
    markdown = render_report(report)

    assert report["metrics"]["case_count"] == 21
    assert report["metrics"]["planner_call_failure_rate"] == round(1 / 21, 4)
    assert (
        report["categories"]["ambiguous"]["planner_call_failure_rate"] == 0.5
    )
    assert "ambiguous-task-001" in markdown
    assert "sensitive exception text" not in markdown


def test_graph_telemetry_has_trace_node_timing_and_no_confidential_text(
    tool_db,
    monkeypatch,
    caplog,
):
    import logging

    db, employees = tool_db
    documents_permission = db.scalar(
        select(Permission).where(Permission.code == "documents:read")
    )
    employees["EMP-ONE"].role.permissions.append(documents_permission)
    db.commit()
    _stub_agent_models(
        monkeypatch,
        RequestPlan(mode="rag"),
    )
    private_marker = "CONFIDENTIAL_DOCUMENT_BODY_8f17"
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: {
            "answer": private_marker,
            "conversation_id": "not-persisted",
            "sources": [
                {
                    "source_id": "S1",
                    "document_id": 91,
                    "filename": "private-file.pdf",
                    "version_number": 1,
                    "page_number": 1,
                    "chunk_id": 2,
                    "chunk_index": 0,
                    "relevance_score": 0.9,
                    "excerpt": private_marker,
                }
            ],
        },
    )

    with caplog.at_level(logging.INFO, logger="app.agent.telemetry"):
        result = orchestrator.run_agent_query(
            db,
            employees["EMP-ONE"],
            f"Question containing {private_marker}",
        )

    entries = [
        json.loads(record.message.split("agent_telemetry ", 1)[1])
        for record in caplog.records
        if "agent_telemetry " in record.message
    ]
    assert result["trace_id"]
    assert all(entry["trace_id"] == result["trace_id"] for entry in entries)
    assert any(
        entry["event"] == "node_execution"
        and entry["node"] == "retrieve_documents"
        and entry["duration_ms"] >= 0
        for entry in entries
    )
    assert any(entry["event"] == "model_call" for entry in entries)
    assert any(entry["event"] == "rag_execution" for entry in entries)
    assert any(entry["event"] == "final_response" for entry in entries)
    serialized = "\n".join(record.message for record in caplog.records)
    assert private_marker not in serialized
    assert "private-file.pdf" not in serialized
    assert "EMP-ONE" not in serialized
    assert all(
        set(entry).issubset(
            {
                "trace_id",
                "event",
                "outcome",
                "duration_ms",
                "mode",
                "node",
                "tool_name",
                "error_category",
            }
        )
        for entry in entries
    )


def test_tool_telemetry_logs_registered_name_and_outcome_only(
    tool_db,
    monkeypatch,
    caplog,
):
    import logging

    db, employees = tool_db
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="structured",
            tool_calls=[
                {"name": "get_employee_details", "arguments": {}},
            ],
        ),
    )
    with caplog.at_level(logging.INFO, logger="app.agent.telemetry"):
        result = orchestrator.run_agent_query(
            db, employees["EMP-ONE"], "Show my record"
        )

    entries = [
        json.loads(record.message.split("agent_telemetry ", 1)[1])
        for record in caplog.records
        if "agent_telemetry " in record.message
    ]
    tool_events = [
        entry for entry in entries if entry["event"] == "tool_execution"
    ]
    selections = [
        entry for entry in entries if entry["event"] == "tool_selection"
    ]
    assert len(tool_events) == 1
    assert len(selections) == 1
    assert selections[0]["tool_name"] == "get_employee_details"
    assert tool_events[0]["tool_name"] == "get_employee_details"
    assert tool_events[0]["outcome"] == "ok"
    assert "arguments" not in tool_events[0]
    assert result["trace_id"] == tool_events[0]["trace_id"]


def test_planner_unknown_tool_never_executes_or_leaks_arguments(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    confidential_argument = "SYNTHETIC_PRIVATE_SENTINEL"
    _stub_agent_models(
        monkeypatch,
        json.dumps(
            {
                "mode": "structured",
                "tool_calls": [
                    {
                        "name": "unregistered_lookup",
                        "arguments": {"secret": confidential_argument},
                    }
                ],
            }
        ),
    )
    called = []
    monkeypatch.setattr(
        orchestrator,
        "execute_tool",
        lambda *args, **kwargs: called.append(args),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "A synthetic question"
    )

    assert called == []
    assert result["sources"] == []
    assert result["answer"] == orchestrator.INSUFFICIENT_CONTEXT_ANSWER
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_type == "employee",
            AuditEvent.outcome == "success",
        )
    ) is None


def test_tool_failure_logs_only_safe_categories(
    tool_db,
    monkeypatch,
    caplog,
):
    import logging

    db, employees = tool_db
    exception_marker = "CONFIDENTIAL_DB_EXCEPTION_9a3e"
    query_marker = "CONFIDENTIAL_QUERY_MARKER_12"
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="structured",
            tool_calls=[
                {"name": "get_employee_details", "arguments": {}},
            ],
        ),
    )

    def fail_tool(*_args, **_kwargs):
        raise RuntimeError(exception_marker)

    monkeypatch.setattr(orchestrator, "execute_tool", fail_tool)
    with caplog.at_level(logging.INFO):
        result = orchestrator.run_agent_query(
            db,
            employees["EMP-ONE"],
            query_marker,
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert exception_marker not in logged
    assert query_marker not in logged
    assert result["answer"] == orchestrator.TOOL_FAILURE_ANSWER
    telemetry = [
        json.loads(record.message.split("agent_telemetry ", 1)[1])
        for record in caplog.records
        if "agent_telemetry " in record.message
    ]
    assert any(
        entry["event"] == "tool_execution"
        and entry["error_category"] == "tool_unavailable"
        for entry in telemetry
    )
