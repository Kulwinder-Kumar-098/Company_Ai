"""Authorization and behavior tests for structured-data tools."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.tools.registry import TOOLS, execute_tool
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
    with pytest.raises(ValidationError):
        execute_tool(
            "get_employee_details",
            {"actor_employee_id": "ADMIN"},
            db=db,
            actor=employees["EMP-ONE"],
        )
