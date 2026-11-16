"""Tests for admin-only employee deletion API."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.employee_routes import router as employee_router
from app.core.config import settings
from app.core.database import get_db
from app.models import AuditEvent, Base, Document, Employee, Permission, Project, Role, Task
from app.models.employee_credential import EmployeeCredential


@pytest.fixture
def deletion_client(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-key-for-deletion-min-32-bytes-long!")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as db:
        manage_permission = Permission(
            code="roles:manage",
            description="Manage roles and system administration",
        )
        read_all_permission = Permission(
            code="employees:read:all",
            description="Read all employees",
        )
        admin_role = Role(
            name="admin",
            permissions=[manage_permission, read_all_permission],
        )
        employee_role = Role(name="employee")

        admin = Employee(
            employee_id="ADMIN001",
            full_name="Admin Boss",
            email="admin@example.test",
            department="Executive",
            role=admin_role,
            is_active=True,
        )
        target = Employee(
            employee_id="EMP099",
            full_name="Target Worker",
            email="target@example.test",
            department="Engineering",
            role=employee_role,
            is_active=True,
        )
        cred = EmployeeCredential(
            employee_id="EMP099",
            password_hash="$argon2id$mock-hash",
        )
        db.add_all([admin_role, employee_role, admin, target, cred])
        db.commit()

        app = FastAPI()
        app.include_router(employee_router)

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


def test_admin_can_delete_employee(deletion_client):
    client, db = deletion_client

    response = client.delete(
        "/employees/EMP099",
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["employee_id"] == "EMP099"

    # Verify database state
    assert db.get(Employee, "EMP099") is None
    assert db.get(EmployeeCredential, "EMP099") is None

    # Verify audit event
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "delete",
            AuditEvent.resource_id == "EMP099",
            AuditEvent.outcome == "success",
        )
    )
    assert audit is not None


def test_admin_cannot_delete_self(deletion_client):
    client, db = deletion_client

    response = client.delete(
        "/employees/ADMIN001",
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 400
    assert "Cannot delete your own active administrator account" in response.json()["detail"]
    assert db.get(Employee, "ADMIN001") is not None


def test_non_admin_cannot_delete_employee(deletion_client):
    client, db = deletion_client

    response = client.delete(
        "/employees/EMP099",
        headers=_headers("EMP099"),
    )

    assert response.status_code == 403
    assert db.get(Employee, "EMP099") is not None


def test_deleting_nonexistent_employee_returns_404(deletion_client):
    client, db = deletion_client

    response = client.delete(
        "/employees/DOES_NOT_EXIST",
        headers=_headers("ADMIN001"),
    )

    assert response.status_code == 404
