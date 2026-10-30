"""Resource-level authorization and audit tests."""

from datetime import date, datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.api.employee_routes import router as employee_router
from app.api.project_routes import router as project_router
from app.core.config import settings
from app.models import (
    AuditEvent,
    Base,
    Employee,
    Permission,
    Project,
    Role,
    Task,
)


@pytest.fixture
def resource_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as db:
        permission_codes = {
            "employees:read:all",
            "employees:read:self",
            "projects:read:all",
            "projects:read:assigned",
            "tasks:read:assigned",
        }
        permissions = {
            code: Permission(code=code, description=code)
            for code in permission_codes
        }
        roles = {
            name: Role(name=name)
            for name in ("admin", "employee", "hr", "manager", "guest")
        }
        roles["admin"].permissions.extend(permissions.values())
        roles["hr"].permissions.append(permissions["employees:read:all"])
        roles["manager"].permissions.extend(
            [
                permissions["projects:read:assigned"],
                permissions["tasks:read:assigned"],
            ]
        )
        roles["employee"].permissions.extend(
            [
                permissions["employees:read:self"],
                permissions["projects:read:assigned"],
                permissions["tasks:read:assigned"],
            ]
        )
        db.add_all([*permissions.values(), *roles.values()])
        db.flush()

        employees = {
            key: Employee(
                employee_id=key,
                full_name=f"Test {key}",
                email=f"{key.lower()}@example.test",
                department=department,
                role=roles[role_name],
                is_active=True,
            )
            for key, (role_name, department) in (
                ("EMP-ADMIN", ("admin", "executive")),
                ("EMP-HR", ("hr", "people")),
                ("EMP-MANAGER", ("manager", "engineering")),
                ("EMP-ONE", ("employee", "engineering")),
                ("EMP-TWO", ("employee", "sales")),
                ("EMP-GUEST", ("guest", "legal")),
            )
        }
        db.add_all(employees.values())

        def project(
            project_id: str,
            manager_id: str | None,
        ) -> Project:
            return Project(
                project_id=project_id,
                project_name=f"Project {project_id}",
                department="test",
                project_manager_id=manager_id,
                start_date=date(2026, 1, 1),
                planned_end_date=date(2026, 12, 31),
                actual_end_date=None,
                status="active",
                priority="normal",
                budgeted_hours=100,
                completed_hours=10,
                completion_percentage=10.0,
                risk_level="low",
            )

        projects = [
            project("P-MANAGED", "EMP-MANAGER"),
            project("P-ASSIGNED", "EMP-TWO"),
            project("P-OTHER", "EMP-TWO"),
        ]
        db.add_all(projects)
        db.flush()
        db.add_all(
            [
                Task(
                    task_id="T-MGR-ONE",
                    project_id="P-MANAGED",
                    task_name="Manager task",
                    assigned_employee_id="EMP-ONE",
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
                Task(
                    task_id="T-MGR-TWO",
                    project_id="P-MANAGED",
                    task_name="Other managed task",
                    assigned_employee_id="EMP-TWO",
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
                Task(
                    task_id="T-ASSIGNED",
                    project_id="P-ASSIGNED",
                    task_name="Assigned task",
                    assigned_employee_id="EMP-MANAGER",
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
                Task(
                    task_id="T-OTHER",
                    project_id="P-OTHER",
                    task_name="Unassigned task",
                    assigned_employee_id="EMP-TWO",
                    status="active",
                    priority="normal",
                    estimated_hours=4,
                ),
            ]
        )
        db.commit()

        app = FastAPI()
        app.include_router(employee_router)
        app.include_router(project_router)

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.clear()

    Base.metadata.drop_all(engine)
    engine.dispose()


def _token(employee_id: str) -> str:
    return jwt.encode(
        {
            "sub": employee_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _headers(employee_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(employee_id)}"}


def test_employee_can_only_read_own_employee_record(resource_client):
    client, db = resource_client

    own = client.get("/employees/EMP-ONE", headers=_headers("EMP-ONE"))
    other = client.get("/employees/EMP-TWO", headers=_headers("EMP-ONE"))
    listing = client.get("/employees", headers=_headers("EMP-ONE"))

    assert own.status_code == 200
    assert other.status_code == 403
    assert [row["employee_id"] for row in listing.json()] == ["EMP-ONE"]
    denied = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-ONE",
            AuditEvent.resource_id == "EMP-TWO",
            AuditEvent.outcome == "denied",
        )
    )
    assert denied is not None


def test_hr_can_read_employees_across_departments(resource_client):
    client, _ = resource_client

    response = client.get("/employees", headers=_headers("EMP-HR"))

    assert response.status_code == 200
    assert len(response.json()) == 6
    assert len({employee["department"] for employee in response.json()}) > 1


def test_employee_without_employee_read_permission_is_forbidden(resource_client):
    client, db = resource_client

    response = client.get("/employees", headers=_headers("EMP-GUEST"))

    assert response.status_code == 403
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-GUEST",
            AuditEvent.resource_type == "permission",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_employee_endpoints_require_jwt(resource_client):
    client, _ = resource_client

    response = client.get("/employees")

    assert response.status_code == 401


def test_manager_sees_managed_and_task_assigned_projects_only(resource_client):
    client, _ = resource_client

    response = client.get("/projects", headers=_headers("EMP-MANAGER"))

    assert response.status_code == 200
    assert {project["project_id"] for project in response.json()} == {
        "P-MANAGED",
        "P-ASSIGNED",
    }


def test_assigned_employee_sees_only_assigned_project(resource_client):
    client, _ = resource_client

    response = client.get("/projects", headers=_headers("EMP-ONE"))

    assert response.status_code == 200
    assert {project["project_id"] for project in response.json()} == {
        "P-MANAGED",
    }


def test_manager_sees_all_tasks_in_managed_project(resource_client):
    client, _ = resource_client

    response = client.get(
        "/projects/P-MANAGED/tasks",
        headers=_headers("EMP-MANAGER"),
    )

    assert response.status_code == 200
    assert {task["task_id"] for task in response.json()} == {
        "T-MGR-ONE",
        "T-MGR-TWO",
    }


def test_assigned_user_sees_only_own_task_in_assigned_project(resource_client):
    client, _ = resource_client

    response = client.get(
        "/projects/P-MANAGED/tasks",
        headers=_headers("EMP-ONE"),
    )

    assert response.status_code == 200
    assert [task["task_id"] for task in response.json()] == ["T-MGR-ONE"]


def test_manager_assigned_to_project_sees_only_own_task(resource_client):
    client, _ = resource_client

    response = client.get(
        "/projects/P-ASSIGNED/tasks",
        headers=_headers("EMP-MANAGER"),
    )

    assert response.status_code == 200
    assert [task["task_id"] for task in response.json()] == ["T-ASSIGNED"]


def test_project_outside_assignment_is_not_found(resource_client):
    client, db = resource_client

    response = client.get(
        "/projects/P-OTHER",
        headers=_headers("EMP-MANAGER"),
    )

    assert response.status_code == 404
    denied = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP-MANAGER",
            AuditEvent.resource_type == "project",
            AuditEvent.resource_id == "P-OTHER",
            AuditEvent.outcome == "denied",
        )
    )
    assert denied is not None


def test_admin_can_read_all_projects(resource_client):
    client, _ = resource_client

    response = client.get("/projects", headers=_headers("EMP-ADMIN"))

    assert response.status_code == 200
    assert {project["project_id"] for project in response.json()} == {
        "P-MANAGED",
        "P-ASSIGNED",
        "P-OTHER",
    }
