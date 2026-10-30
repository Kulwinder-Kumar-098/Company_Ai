"""Employee registration authorization and transaction tests."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth_routes import router
from app.core.config import settings
from app.core.database import get_db
from app.models import AuditEvent, Base, Employee, Permission, Role
from app.models.employee_credential import EmployeeCredential
from app.services.auth_service import verify_password


@pytest.fixture
def registration_client(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-key-for-registration")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as db:
        create_permission = Permission(
            code="employees:create",
            description="Create employee records",
        )
        admin_role = Role(name="admin", permissions=[create_permission])
        hr_role = Role(name="hr", permissions=[create_permission])
        employee_role = Role(name="employee")
        manager_role = Role(name="manager")
        admin = Employee(
            employee_id="ADMIN001",
            full_name="Test Admin",
            email="admin@example.test",
            department="IT",
            role=admin_role,
            is_active=True,
        )
        hr = Employee(
            employee_id="HR001",
            full_name="Test HR",
            email="hr@example.test",
            department="People",
            role=hr_role,
            is_active=True,
        )
        employee = Employee(
            employee_id="EMP001",
            full_name="Test Employee",
            email="employee@example.test",
            department="Engineering",
            role=employee_role,
            is_active=True,
        )
        db.add_all(
            [
                admin,
                hr,
                employee,
                manager_role,
            ]
        )
        db.commit()

        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app, raise_server_exceptions=False) as client:
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


def _registration(
    db,
    *,
    employee_id: str = "NEW001",
    email: str = "new@example.test",
    role_name: str = "employee",
) -> dict[str, object]:
    role = db.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    return {
        "employee_id": employee_id,
        "full_name": "New Employee",
        "email": email,
        "department": "Engineering",
        "job_title": "Developer",
        "password": "StrongPassword123!",
        "role_id": role.id,
    }


def test_admin_can_register_employee_and_assign_privileged_role(
    registration_client,
):
    client, db = registration_client
    payload = _registration(db, role_name="manager")

    response = client.post(
        "/auth/register",
        json=payload,
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 201
    employee = db.get(Employee, "NEW001")
    credential = db.get(EmployeeCredential, "NEW001")
    assert employee is not None
    assert employee.role.name == "manager"
    assert credential is not None
    assert credential.password_hash.startswith("$argon2")
    assert verify_password(payload["password"], credential.password_hash)
    assert credential.password_hash != payload["password"]


def test_hr_can_register_employee_role(registration_client):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json=_registration(db),
        headers=_headers("HR001"),
    )

    assert response.status_code == 201


def test_hr_cannot_assign_privileged_role(registration_client):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json=_registration(db, role_name="admin"),
        headers=_headers("HR001"),
    )

    assert response.status_code == 403
    assert db.get(Employee, "NEW001") is None
    assert db.get(EmployeeCredential, "NEW001") is None
    denial = db.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "HR001",
            AuditEvent.resource_id == "NEW001",
            AuditEvent.outcome == "denied",
        )
    )
    assert denial is not None


def test_employee_without_create_permission_is_forbidden(registration_client):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json=_registration(db),
        headers=_headers("EMP001"),
    )

    assert response.status_code == 403
    assert db.get(Employee, "NEW001") is None


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer invalid-token"}, 401),
    ],
)
def test_missing_or_invalid_jwt_is_unauthorized(
    registration_client,
    headers,
    expected_status,
):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json={
            "employee_id": "NEW001",
            "full_name": "New Employee",
            "email": "new@example.test",
            "department": "Engineering",
            "password": "StrongPassword123!",
            "role_id": 1,
        },
        headers=headers,
    )

    assert response.status_code == expected_status
    assert db.get(Employee, "NEW001") is None


def test_duplicate_employee_id_is_rejected(registration_client):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json=_registration(db, employee_id="EMP001"),
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 409


def test_duplicate_email_is_rejected_case_insensitively(registration_client):
    client, db = registration_client

    response = client.post(
        "/auth/register",
        json=_registration(db, email="EMPLOYEE@EXAMPLE.TEST"),
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 409


def test_invalid_role_id_is_rejected(registration_client):
    client, db = registration_client
    payload = _registration(db)
    payload["role_id"] = 99999

    response = client.post(
        "/auth/register",
        json=payload,
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 400
    assert db.get(Employee, "NEW001") is None


def test_invalid_request_data_is_rejected(registration_client):
    client, db = registration_client
    payload = _registration(db)
    payload["password"] = "short"

    response = client.post(
        "/auth/register",
        json=payload,
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 422
    assert db.get(Employee, "NEW001") is None


def test_registration_integrity_error_rolls_back_employee_and_credential(
    registration_client,
    monkeypatch,
):
    client, db = registration_client
    original_commit = db.commit

    def fail_commit():
        db.flush()
        raise IntegrityError(
            "registration test failure",
            {},
            Exception("simulated unique constraint violation"),
        )

    monkeypatch.setattr(db, "commit", fail_commit)
    response = client.post(
        "/auth/register",
        json=_registration(db),
        headers=_headers("ADMIN001"),
    )
    monkeypatch.setattr(db, "commit", original_commit)

    assert response.status_code == 409
    assert db.get(Employee, "NEW001") is None
    assert db.get(EmployeeCredential, "NEW001") is None
    assert db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == "NEW001",
            AuditEvent.action == "employee_registered",
        )
    ) is None
