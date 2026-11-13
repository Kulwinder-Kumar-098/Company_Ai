"""Authorization and behavior tests for structured-data tools."""

from datetime import date, timedelta
import json

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import orchestrator, smalltalk
from app.agents.workflows import GeneratedAnswer, RequestPlan
from app.api.dependencies import get_db
from app.api.tool_routes import router as tool_router
from app.core.config import settings
from app.models import (
    AuditEvent,
    Base,
    Employee,
    PerformanceMetric,
    Permission,
    Project,
    Role,
    Task,
)
from app.schemas.tools import (
    EmployeeSearchInput,
    EmployeeTasksInput,
    EmployeeToolInput,
    OverdueTasksInput,
    PerformanceDepartmentInput,
    PerformanceMetricsInput,
    ProjectTaskInput,
    ProjectTeamInput,
    ProjectToolInput,
    TaskSearchInput,
    TaskToolInput,
)
from app.tools.hr_tools import (
    get_department_performance_summary,
    get_employee_details,
    get_employee_performance_metrics,
    get_employee_performance_summary,
    get_employee_tasks,
    search_employees,
)
from app.tools.project_tools import (
    get_project_details,
    get_project_tasks,
    get_project_team,
)
from app.tools.registry import TOOLS, ToolArgumentsError, execute_tool
from app.tools.technical_tools import (
    get_overdue_tasks,
    get_task_details,
    search_tasks,
)


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
        codes = (
            "employees:read:all",
            "employees:read:self",
            "projects:read:all",
            "projects:read:assigned",
            "tasks:read:assigned",
            "performance:read:self",
            "performance:read:department",
            "performance:read:all",
            "documents:read",
        )
        permissions = {
            code: Permission(code=code, description=code) for code in codes
        }
        roles = {name: Role(name=name) for name in (
            "admin", "hr", "manager", "employee", "guest"
        )}
        roles["admin"].permissions.extend(permissions.values())
        roles["hr"].permissions.extend(
            [
                permissions["employees:read:all"],
                permissions["performance:read:department"],
            ]
        )
        roles["manager"].permissions.extend(
            [
                permissions["projects:read:assigned"],
                permissions["tasks:read:assigned"],
                permissions["performance:read:self"],
            ]
        )
        roles["employee"].permissions.extend(
            [
                permissions["employees:read:self"],
                permissions["projects:read:assigned"],
                permissions["tasks:read:assigned"],
                permissions["performance:read:self"],
            ]
        )
        db.add_all([*permissions.values(), *roles.values()])
        db.flush()

        people: dict[str, Employee] = {}
        for employee_id, (role_name, department) in {
            "ADMIN": ("admin", "executive"),
            "HR": ("hr", "people"),
            "MGR": ("manager", "engineering"),
            "EMP-ONE": ("employee", "engineering"),
            "EMP-TWO": ("employee", "sales"),
            "OTHER": ("employee", "sales"),
            "GUEST": ("guest", "legal"),
        }.items():
            people[employee_id] = Employee(
                employee_id=employee_id,
                full_name=f"Person {employee_id}",
                email=f"{employee_id.lower()}@example.test",
                department=department,
                job_title="Engineer",
                role=roles[role_name],
                is_active=True,
            )
        db.add_all(people.values())

        def make_project(project_id: str, manager_id: str) -> Project:
            return Project(
                project_id=project_id,
                project_name=f"Project {project_id}",
                department="engineering",
                project_manager_id=manager_id,
                start_date=date(2026, 1, 1),
                planned_end_date=date(2026, 12, 31),
                actual_end_date=None,
                status="active",
                priority="normal",
                budgeted_hours=100,
                completed_hours=25,
                completion_percentage=25.0,
                risk_level="low",
            )

        db.add_all(
            [
                make_project("P-MANAGED", "MGR"),
                make_project("P-ASSIGNED", "OTHER"),
                make_project("P-UNRELATED", "OTHER"),
            ]
        )
        db.flush()

        today = date.today()
        db.add_all(
            [
                Task(
                    task_id="T-MGR-ONE",
                    project_id="P-MANAGED",
                    task_name="Manager visible task",
                    assigned_employee_id="EMP-ONE",
                    due_date=today - timedelta(days=2),
                    status="active",
                    priority="high",
                    estimated_hours=8,
                ),
                Task(
                    task_id="T-OWN",
                    project_id="P-ASSIGNED",
                    task_name="My assigned task",
                    assigned_employee_id="EMP-ONE",
                    due_date=today - timedelta(days=1),
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
                Task(
                    task_id="T-OTHER-ASSIGNED",
                    project_id="P-ASSIGNED",
                    task_name="Someone else's task",
                    assigned_employee_id="OTHER",
                    due_date=today - timedelta(days=3),
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
                Task(
                    task_id="T-COMPLETED",
                    project_id="P-ASSIGNED",
                    task_name="Completed task",
                    assigned_employee_id="EMP-ONE",
                    due_date=today - timedelta(days=10),
                    completed_date=today - timedelta(days=9),
                    status="completed",
                    priority="normal",
                    estimated_hours=2,
                ),
                Task(
                    task_id="T-DUE-TODAY",
                    project_id="P-ASSIGNED",
                    task_name="Due today",
                    assigned_employee_id="EMP-ONE",
                    due_date=today,
                    status="active",
                    priority="normal",
                    estimated_hours=2,
                ),
                Task(
                    task_id="T-UNRELATED",
                    project_id="P-UNRELATED",
                    task_name="Unrelated task",
                    assigned_employee_id="OTHER",
                    due_date=today - timedelta(days=1),
                    status="active",
                    priority="normal",
                    estimated_hours=3,
                ),
            ]
        )

        def metric(
            record_id: str,
            employee_id: str,
            department: str,
            *,
            goals_completed: int,
            goals_total: int,
            quality_score: float,
            quarter: str = "2026Q1",
        ) -> PerformanceMetric:
            return PerformanceMetric(
                record_id=record_id,
                employee_id=employee_id,
                quarter=quarter,
                review_date=date(2026, 3, 31),
                department=department,
                project_id=None,
                goals_completed=goals_completed,
                goals_total=goals_total,
                task_completion_rate=quality_score / 100,
                quality_score=quality_score,
                collaboration_score=quality_score - 5,
                attendance_rate=0.95,
                manager_review_status="approved",
                performance_category="strong",
            )

        db.add_all(
            [
                metric(
                    "M-ONE-1",
                    "EMP-ONE",
                    "engineering",
                    goals_completed=2,
                    goals_total=4,
                    quality_score=80,
                ),
                metric(
                    "M-ONE-2",
                    "EMP-ONE",
                    "engineering",
                    goals_completed=4,
                    goals_total=5,
                    quality_score=100,
                    quarter="2026Q2",
                ),
                metric(
                    "M-MGR",
                    "MGR",
                    "engineering",
                    goals_completed=3,
                    goals_total=3,
                    quality_score=90,
                ),
                metric(
                    "M-HR",
                    "HR",
                    "people",
                    goals_completed=1,
                    goals_total=2,
                    quality_score=70,
                ),
            ]
        )
        db.commit()
        yield db, people
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def tool_client(tool_db):
    db, employees = tool_db
    app = FastAPI()
    app.include_router(tool_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, employees
    app.dependency_overrides.clear()


def _token(employee_id: str) -> str:
    return jwt.encode(
        {"sub": employee_id},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _headers(employee_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(employee_id)}"}


def test_employee_self_access_and_sensitive_output_fields(tool_db):
    db, employees = tool_db

    own = get_employee_details(db, employees["EMP-ONE"], EmployeeToolInput())
    other = get_employee_details(
        db,
        employees["EMP-ONE"],
        EmployeeToolInput(employee_id="EMP-TWO"),
    )

    assert own.status == "ok"
    assert own.data.employee_id == "EMP-ONE"
    assert set(type(own.data).model_fields) == {
        "employee_id",
        "full_name",
        "email",
        "department",
        "job_title",
        "is_active",
    }
    assert other.status == "denied"
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == "EMP-TWO",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_hr_and_admin_can_read_employee_records_across_departments(tool_db):
    db, employees = tool_db

    hr_result = get_employee_details(
        db, employees["HR"], EmployeeToolInput(employee_id="EMP-ONE")
    )
    admin_result = search_employees(
        db,
        employees["ADMIN"],
        EmployeeSearchInput(department="sales"),
    )

    assert hr_result.status == "ok"
    assert hr_result.data.department == "engineering"
    assert {row.department for row in admin_result.data} == {"sales"}


def test_employee_search_is_authorized_paginated_and_empty_safe(tool_db):
    db, employees = tool_db

    empty = search_employees(
        db,
        employees["EMP-ONE"],
        EmployeeSearchInput(query="no matching person"),
    )
    own = search_employees(
        db,
        employees["EMP-ONE"],
        EmployeeSearchInput(limit=1),
    )

    assert empty.status == "ok"
    assert empty.data == []
    assert [row.employee_id for row in own.data] == ["EMP-ONE"]
    with pytest.raises(ValidationError):
        EmployeeSearchInput(limit=101)


def test_manager_can_read_managed_project_but_not_unrelated_project(tool_db):
    db, employees = tool_db

    managed = get_project_details(
        db, employees["MGR"], ProjectToolInput(project_id="P-MANAGED")
    )
    unrelated = get_project_details(
        db, employees["MGR"], ProjectToolInput(project_id="P-UNRELATED")
    )
    tasks = get_project_tasks(
        db, employees["MGR"], ProjectTaskInput(project_id="P-MANAGED")
    )
    team = get_project_team(
        db, employees["MGR"], ProjectTeamInput(project_id="P-MANAGED")
    )

    assert managed.status == "ok"
    assert unrelated.status == "denied"
    assert [task.task_id for task in tasks.data] == ["T-MGR-ONE"]
    assert {member.employee_id for member in team.data} == {"MGR", "EMP-ONE"}
    assert not hasattr(team.data[0], "email")


def test_assigned_employee_can_read_only_own_permitted_tasks(tool_db):
    db, employees = tool_db

    own = get_task_details(db, employees["EMP-ONE"], TaskToolInput(task_id="T-OWN"))
    other = get_task_details(
        db,
        employees["EMP-ONE"],
        TaskToolInput(task_id="T-OTHER-ASSIGNED"),
    )
    employee_tasks = get_employee_tasks(
        db, employees["EMP-ONE"], EmployeeTasksInput()
    )

    assert own.status == "ok"
    assert other.status == "denied"
    assert {task.task_id for task in employee_tasks.data} == {
        "T-MGR-ONE",
        "T-OWN",
        "T-COMPLETED",
        "T-DUE-TODAY",
    }


def test_task_search_filters_before_returning_and_handles_empty_results(tool_db):
    db, employees = tool_db

    visible = search_tasks(
        db,
        employees["EMP-ONE"],
        TaskSearchInput(limit=100),
    )
    empty = search_tasks(
        db,
        employees["EMP-ONE"],
        TaskSearchInput(query="not a real task"),
    )

    assert {task.task_id for task in visible.data} == {
        "T-MGR-ONE",
        "T-OWN",
        "T-COMPLETED",
        "T-DUE-TODAY",
    }
    assert empty.data == []


def test_overdue_tasks_exclude_completed_and_not_yet_overdue(tool_db):
    db, employees = tool_db

    result = get_overdue_tasks(
        db,
        employees["EMP-ONE"],
        OverdueTasksInput(limit=100),
    )

    assert {task.task_id for task in result.data} == {"T-MGR-ONE", "T-OWN"}


def test_performance_self_scope_and_explicit_permission(tool_db):
    db, employees = tool_db

    own = get_employee_performance_metrics(
        db,
        employees["EMP-ONE"],
        PerformanceMetricsInput(),
    )
    other = get_employee_performance_metrics(
        db,
        employees["EMP-ONE"],
        PerformanceMetricsInput(employee_id="EMP-TWO"),
    )
    guest = get_employee_performance_metrics(
        db,
        employees["GUEST"],
        PerformanceMetricsInput(),
    )

    assert {row.record_id for row in own.data} == {"M-ONE-1", "M-ONE-2"}
    assert other.status == "denied"
    assert guest.status == "denied"


def test_hr_department_performance_scope_and_aggregation(tool_db):
    db, employees = tool_db

    allowed = get_department_performance_summary(
        db, employees["HR"], PerformanceDepartmentInput(department="people")
    )
    denied_result = get_department_performance_summary(
        db,
        employees["HR"],
        PerformanceDepartmentInput(department="engineering"),
    )
    summary = get_employee_performance_summary(
        db,
        employees["EMP-ONE"],
        EmployeeToolInput(),
    )

    assert allowed.status == "ok"
    assert allowed.data.metric_count == 1
    assert allowed.data.employee_count == 1
    assert allowed.data.goals_completed == 1
    assert allowed.data.goals_total == 2
    assert allowed.data.average_quality_score == pytest.approx(70)
    assert denied_result.status == "denied"
    assert summary.data.metric_count == 2
    assert summary.data.goals_completed == 6
    assert summary.data.goals_total == 9
    assert summary.data.average_quality_score == pytest.approx(90)


def test_admin_department_aggregate_and_unknown_ids(tool_db):
    db, employees = tool_db

    aggregate = get_department_performance_summary(
        db,
        employees["ADMIN"],
        PerformanceDepartmentInput(department="engineering"),
    )
    missing_employee = get_employee_details(
        db, employees["ADMIN"], EmployeeToolInput(employee_id="MISSING")
    )
    missing_project = get_project_details(
        db, employees["ADMIN"], ProjectToolInput(project_id="MISSING")
    )
    missing_task = get_task_details(
        db, employees["ADMIN"], TaskToolInput(task_id="MISSING")
    )

    assert aggregate.status == "ok"
    assert aggregate.data.metric_count == 3
    assert aggregate.data.employee_count == 2
    assert aggregate.data.goals_completed == 9
    assert aggregate.data.goals_total == 12
    assert missing_employee.status == "not_found"
    assert missing_project.status == "not_found"
    assert missing_task.status == "not_found"


def test_audit_records_sensitive_reads_and_denials(tool_db):
    db, employees = tool_db

    get_employee_details(db, employees["EMP-ONE"], EmployeeToolInput())
    get_employee_details(
        db, employees["EMP-ONE"], EmployeeToolInput(employee_id="EMP-TWO")
    )
    get_task_details(
        db, employees["EMP-ONE"], TaskToolInput(task_id="T-OTHER-ASSIGNED")
    )

    outcomes = db.scalars(
        select(AuditEvent.outcome).where(
            AuditEvent.actor_employee_id == "EMP-ONE"
        )
    ).all()
    assert "success" in outcomes
    assert outcomes.count("denied") == 2


def test_registry_requires_trusted_actor_and_rejects_identity_arguments(tool_db):
    db, employees = tool_db

    assert "actor_employee_id" not in TOOLS["get_employee_details"].input_schema.model_fields
    own = execute_tool(
        "get_employee_details",
        {},
        db=db,
        actor=employees["EMP-ONE"],
    )
    assert own.data.employee_id == "EMP-ONE"
    with pytest.raises(ToolArgumentsError):
        execute_tool(
            "get_employee_details",
            {"actor_employee_id": "ADMIN"},
            db=db,
            actor=employees["EMP-ONE"],
        )


def test_tool_endpoint_requires_valid_jwt(tool_client):
    client, _, _ = tool_client

    missing = client.post(
        "/tools/execute",
        json={"tool_name": "get_employee_details", "arguments": {}},
    )
    invalid = client.post(
        "/tools/execute",
        json={"tool_name": "get_employee_details", "arguments": {}},
        headers={"Authorization": "Bearer invalid"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_employee_self_tool_and_cross_employee_denial_are_audited(tool_client):
    client, db, _ = tool_client

    own = client.post(
        "/tools/execute",
        json={"tool_name": "get_employee_details", "arguments": {}},
        headers=_headers("EMP-ONE"),
    )
    other = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_employee_details",
            "arguments": {"employee_id": "EMP-TWO"},
        },
        headers=_headers("EMP-ONE"),
    )

    assert own.status_code == 200
    assert own.json()["result"]["data"]["employee_id"] == "EMP-ONE"
    assert other.status_code == 403
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "structured_tool",
            AuditEvent.resource_id == "EMP-TWO",
            AuditEvent.outcome == "denied",
        )
    )
    assert event is not None
    assert event.details == {"tool_name": "get_employee_details"}


def test_manager_cannot_read_unrelated_project_via_endpoint(tool_client):
    client, db, _ = tool_client

    response = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_project_details",
            "arguments": {"project_id": "P-UNRELATED"},
        },
        headers=_headers("MGR"),
    )

    assert response.status_code == 403
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "MGR",
            AuditEvent.resource_type == "structured_tool",
            AuditEvent.resource_id == "P-UNRELATED",
            AuditEvent.outcome == "denied",
        )
    ) is not None


@pytest.mark.parametrize("actor_id", ["HR", "ADMIN"])
def test_hr_and_admin_employee_scopes_via_endpoint(tool_client, actor_id):
    client, _, _ = tool_client

    response = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_employee_details",
            "arguments": {"employee_id": "EMP-ONE"},
        },
        headers=_headers(actor_id),
    )

    assert response.status_code == 200
    assert response.json()["result"]["data"]["employee_id"] == "EMP-ONE"


def test_unknown_tool_and_invalid_arguments_are_safe_client_errors(tool_client):
    client, _, _ = tool_client

    unknown = client.post(
        "/tools/execute",
        json={"tool_name": "run_sql", "arguments": {}},
        headers=_headers("EMP-ONE"),
    )
    invalid = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_employee_details",
            "arguments": {"employee_id": "EMP-ONE", "unexpected": "value"},
        },
        headers=_headers("EMP-ONE"),
    )

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Unknown tool."
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Invalid tool arguments."


def test_endpoint_rejects_actor_spoofing_at_both_request_levels(tool_client):
    client, _, _ = tool_client

    nested_spoof = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_employee_details",
            "arguments": {"actor_employee_id": "ADMIN"},
        },
        headers=_headers("EMP-ONE"),
    )
    top_level_spoof = client.post(
        "/tools/execute",
        json={
            "tool_name": "get_employee_details",
            "arguments": {},
            "employee_id": "ADMIN",
        },
        headers=_headers("EMP-ONE"),
    )

    assert nested_spoof.status_code == 422
    assert nested_spoof.json()["detail"] == "Invalid tool arguments."
    assert top_level_spoof.status_code == 422


def test_tool_exception_does_not_expose_internal_details(tool_client, monkeypatch):
    from app.api import tool_routes

    client, db, _ = tool_client

    def fail_execution(*args, **kwargs):
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(tool_routes, "execute_tool", fail_execution)
    response = client.post(
        "/tools/execute",
        json={"tool_name": "get_employee_details", "arguments": {}},
        headers=_headers("EMP-ONE"),
    )

    assert response.status_code == 500
    assert "private database connection detail" not in response.text
    assert response.json()["detail"] == "Tool execution failed."
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "structured_tool",
            AuditEvent.outcome == "failure",
        )
    ) is not None


def test_tool_is_not_reported_successful_when_endpoint_audit_fails(
    tool_client,
    monkeypatch,
):
    from app.api import tool_routes

    client, _, _ = tool_client

    def fail_audit(*args, **kwargs):
        raise SQLAlchemyError("private audit backend detail")

    monkeypatch.setattr(tool_routes, "record_audit_event", fail_audit)
    response = client.post(
        "/tools/execute",
        json={"tool_name": "get_employee_details", "arguments": {}},
        headers=_headers("EMP-ONE"),
    )

    assert response.status_code == 503
    assert "private audit backend detail" not in response.text
    assert response.json()["detail"] == "Tool execution is temporarily unavailable."


def _stub_agent_models(
    monkeypatch,
    plan,
    *,
    answer="Grounded answer.",
    capture=None,
):
    def request_model_json(*, system_prompt, user_prompt, output_schema):
        if output_schema is RequestPlan:
            return (
                plan
                if isinstance(plan, str)
                else plan.model_dump_json()
            )
        if capture is not None:
            capture.append(user_prompt)
        return GeneratedAnswer(answer=answer).model_dump_json()

    monkeypatch.setattr(orchestrator, "_request_model_json", request_model_json)


def _rag_response(answer="Document answer."):
    return {
        "answer": answer,
        "conversation_id": "stateless-request",
        "sources": [
            {
                "source_id": "S1",
                "document_id": 41,
                "filename": "approved-policy.pdf",
                "version_number": 1,
                "page_number": 2,
                "chunk_id": 15,
                "chunk_index": 3,
                "relevance_score": 0.91,
                "excerpt": "Authorized policy excerpt.",
            }
        ],
    }


@pytest.mark.parametrize(
    ("query", "expected_answer"),
    [
        (
            "hi",
            "Hi! I'm the Company AI assistant. What question can I help with?",
        ),
        (
            "hlo",
            "Hi! I'm the Company AI assistant. What question can I help with?",
        ),
        (
            "how are you",
            "I'm doing well, thanks for asking! What question can I help with?",
        ),
        (
            "how can u help me",
            "I can answer questions from company documents and policies, and, "
            "depending on your access, help with project, task, and employee "
            "information. What question can I help with?",
        ),
    ],
)
def test_smalltalk_gets_local_answer_without_planner_or_rag(
    tool_db,
    monkeypatch,
    query,
    expected_answer,
):
    db, employees = tool_db
    monkeypatch.setattr(settings, "smalltalk_enabled", True)
    monkeypatch.setattr(
        orchestrator,
        "request_planner_json",
        lambda **_kwargs: pytest.fail("small-talk message called planner"),
    )
    monkeypatch.setattr(
        orchestrator,
        "_ollama_client",
        type(
            "FailingOllamaClient",
            (),
            {
                "chat": lambda *_args, **_kwargs: pytest.fail(
                    "small-talk message called Ollama"
                )
            },
        )(),
    )
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail("small-talk message called RAG"),
    )
    monkeypatch.setattr(
        orchestrator,
        "has_permission",
        lambda *_args, **_kwargs: pytest.fail(
            "small-talk message checked document permission"
        ),
    )

    result = orchestrator.run_agent_query(
        db,
        employees["GUEST"],
        query,
    )

    assert result["answer"] == expected_answer
    assert result["sources"] == []


def test_successful_unsupported_plan_can_use_local_smalltalk_fallback(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    _stub_agent_models(monkeypatch, RequestPlan(mode="unsupported"))
    monkeypatch.setattr(settings, "smalltalk_enabled", True)
    monkeypatch.setattr(settings, "smalltalk_llm_fallback_enabled", True)
    captured = {}

    class FakeSmalltalkClient:
        def __init__(self, *, host, timeout):
            captured["host"] = host
            captured["timeout"] = timeout

        def chat(self, **kwargs):
            captured["request"] = kwargs
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "kind": "smalltalk",
                            "reply": (
                                "I'm glad to chat! What question can I help with?"
                            ),
                        }
                    )
                }
            }

    monkeypatch.setattr(
        "app.core.ollama_client.ollama.Client",
        FakeSmalltalkClient,
    )
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail(
            "unsupported small-talk fallback called RAG"
        ),
    )

    result = orchestrator.run_agent_query(
        db,
        employees["GUEST"],
        "Tell me a fun fact.",
    )

    assert result["answer"] == (
        "I'm glad to chat! What question can I help with?"
    )
    assert result["sources"] == []
    assert captured["timeout"] == settings.smalltalk_llm_timeout_seconds
    assert captured["request"]["messages"][1]["content"] == (
        "Tell me a fun fact."
    )


def test_agent_graph_routes_document_only_question_to_secure_rag(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    employees["EMP-ONE"].role.permissions.append(
        db.scalar(select(Permission).where(Permission.code == "documents:read"))
    )
    db.commit()
    rag_calls = []
    _stub_agent_models(monkeypatch, RequestPlan(mode="rag"))

    def fake_rag(session, actor, query, **kwargs):
        rag_calls.append((session, actor, query, kwargs))
        return _rag_response()

    monkeypatch.setattr(orchestrator, "answer_rag_query", fake_rag)
    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "What does the policy say?"
    )

    assert result["answer"] == "Document answer."
    assert result["sources"][0]["name"] == "approved-policy.pdf"
    assert result["sources"][0]["source_id"] == "S1"
    assert result["sources"][0]["chunk_index"] == 3
    assert rag_calls == [
        (
            db,
            employees["EMP-ONE"],
            "What does the policy say?",
            {
                "top_k": min(
                    settings.chat_rag_top_k,
                    settings.rag_max_top_k,
                ),
                "trace_id": result["trace_id"],
            },
        )
    ]


def test_agent_graph_executes_only_registered_structured_tool(
    tool_db,
    monkeypatch,
):
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
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail("RAG should not be called"),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Show my employee profile"
    )

    assert result["answer"] == "Grounded answer."
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "employee",
            AuditEvent.outcome == "success",
        )
    )
    assert event is not None


def test_structured_only_denies_user_without_required_tool_permission(
    tool_db,
    monkeypatch,
):
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

    result = orchestrator.run_agent_query(
        db, employees["GUEST"], "Show my employee profile"
    )

    assert result["answer"] == orchestrator.ACCESS_DENIED_ANSWER
    assert result["sources"] == []
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "GUEST",
            AuditEvent.resource_type == "employee",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_rag_only_requires_documents_read_and_audits_denial(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    _stub_agent_models(monkeypatch, RequestPlan(mode="rag"))
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail(
            "RAG must not execute without documents:read"
        ),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "What does company policy say?"
    )

    assert result["answer"] == orchestrator.ACCESS_DENIED_ANSWER
    assert result["sources"] == []
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "permission",
            AuditEvent.resource_id == "documents:read",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_rag_only_succeeds_after_documents_permission_check(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    documents_permission = db.scalar(
        select(Permission).where(Permission.code == "documents:read")
    )
    employees["EMP-ONE"].role.permissions.append(documents_permission)
    db.commit()
    _stub_agent_models(monkeypatch, RequestPlan(mode="rag"))
    rag_calls = []
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *args, **kwargs: rag_calls.append((args, kwargs))
        or _rag_response(),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "What does company policy say?"
    )

    assert result["answer"] == "Document answer."
    assert result["sources"][0]["source_id"] == "S1"
    assert len(rag_calls) == 1


def test_agent_graph_mixed_path_aggregates_rag_and_structured_results(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    employees["EMP-ONE"].role.permissions.append(
        db.scalar(select(Permission).where(Permission.code == "documents:read"))
    )
    db.commit()
    prompts = []
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="mixed",
            tool_calls=[
                {"name": "get_employee_details", "arguments": {}},
            ],
        ),
        capture=prompts,
    )
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: _rag_response(),
    )

    result = orchestrator.run_agent_query(
        db,
        employees["EMP-ONE"],
        "Use the policy and my employee details",
    )

    assert result["answer"] == "Grounded answer."
    assert result["sources"][0]["name"] == "approved-policy.pdf"
    assert len(prompts) == 1
    assert "Authorized policy excerpt." in prompts[0]
    assert "EMP-ONE" in prompts[0]


def test_mixed_request_without_documents_permission_denies_before_tools(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="mixed",
            tool_calls=[
                {"name": "get_employee_details", "arguments": {}},
            ],
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail("Unauthorized RAG attempted"),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Mix policy and my employee profile"
    )

    assert result["answer"] == orchestrator.ACCESS_DENIED_ANSWER
    assert result["sources"] == []
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "employee",
            AuditEvent.outcome == "success",
        )
    ) is None


def test_mixed_request_tool_denial_suppresses_rag_and_all_results(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    documents_permission = db.scalar(
        select(Permission).where(Permission.code == "documents:read")
    )
    employees["EMP-ONE"].role.permissions.append(documents_permission)
    db.commit()
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="mixed",
            tool_calls=[
                {
                    "name": "get_employee_details",
                    "arguments": {"employee_id": "EMP-TWO"},
                },
            ],
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail(
            "Mixed all-or-nothing execution must skip RAG after tool denial"
        ),
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Policy and EMP-TWO's employee details"
    )

    assert result["answer"] == orchestrator.ACCESS_DENIED_ANSWER
    assert result["sources"] == []
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "employee",
            AuditEvent.resource_id == "EMP-TWO",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_agent_graph_unsupported_question_returns_insufficient_response(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    _stub_agent_models(monkeypatch, RequestPlan(mode="unsupported"))
    monkeypatch.setattr(
        orchestrator,
        "answer_rag_query",
        lambda *_args, **_kwargs: pytest.fail("Unsupported route invoked RAG"),
    )

    result = orchestrator.run_agent_query(
        db, employees["GUEST"], "Tell me a fictional fact"
    )

    assert result["answer"] == (
        "The available documents do not provide enough information to answer "
        "that question."
    )
    assert result["sources"] == []


@pytest.mark.parametrize(
    "plan",
    [
        json.dumps(
            {
                "mode": "structured",
                "tool_calls": [{"name": "unknown_tool", "arguments": {}}],
            }
        ),
        json.dumps(
            {
                "mode": "structured",
                "tool_calls": [
                    {
                        "name": "get_employee_details",
                        "arguments": {"actor_employee_id": "ADMIN"},
                    }
                ],
            }
        ),
    ],
)
def test_agent_graph_rejects_unknown_tools_and_spoofed_arguments(
    tool_db,
    monkeypatch,
    plan,
):
    db, employees = tool_db
    _stub_agent_models(monkeypatch, plan)

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Look up a profile"
    )

    assert result["answer"] == (
        "The available documents do not provide enough information to answer "
        "that question."
    )
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "employee",
        )
    ) is None


def test_agent_graph_preserves_tool_authorization_denial(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    prompts = []
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="structured",
            tool_calls=[
                {
                    "name": "get_employee_details",
                    "arguments": {"employee_id": "EMP-TWO"},
                }
            ],
        ),
        capture=prompts,
    )

    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Show EMP-TWO's employee details"
    )

    assert result["answer"] == orchestrator.ACCESS_DENIED_ANSWER
    assert result["sources"] == []
    assert prompts == []
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_type == "employee",
            AuditEvent.resource_id == "EMP-TWO",
            AuditEvent.outcome == "denied",
        )
    )
    assert event is not None


def test_agent_graph_handles_model_unavailability_and_malformed_output(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    calls = []

    def unavailable(**_kwargs):
        calls.append(True)
        raise ConnectionError("internal Ollama host")

    monkeypatch.setattr(orchestrator, "_request_model_json", unavailable)
    result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "A question"
    )
    assert len(calls) == 2
    assert result["answer"] == orchestrator.MODEL_UNAVAILABLE_ANSWER

    malformed_calls = []

    def malformed(**_kwargs):
        malformed_calls.append(True)
        return "not json"

    monkeypatch.setattr(orchestrator, "_request_model_json", malformed)
    malformed_result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "Another question"
    )
    assert len(malformed_calls) == 2
    assert malformed_result["sources"] == []


def test_agent_graph_bounds_tool_calls_and_sanitizes_tool_failures(
    tool_db,
    monkeypatch,
):
    db, employees = tool_db
    excessive = json.dumps(
        {
            "mode": "structured",
            "tool_calls": [
                {"name": "get_employee_details", "arguments": {}}
                for _ in range(orchestrator.MAX_TOOL_CALLS + 1)
            ],
        }
    )
    _stub_agent_models(monkeypatch, excessive)
    excessive_result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "A question"
    )
    assert excessive_result["answer"] == (
        "The available documents do not provide enough information to answer "
        "that question."
    )

    prompt_values = []
    _stub_agent_models(
        monkeypatch,
        RequestPlan(
            mode="structured",
            tool_calls=[
                {"name": "get_employee_details", "arguments": {}},
            ],
        ),
        capture=prompt_values,
        answer="Safe fallback.",
    )

    def fail_tool(*_args, **_kwargs):
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(orchestrator, "execute_tool", fail_tool)
    failed_result = orchestrator.run_agent_query(
        db, employees["EMP-ONE"], "A question"
    )
    assert failed_result["answer"] == orchestrator.TOOL_FAILURE_ANSWER
    assert prompt_values == []
