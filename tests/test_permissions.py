"""Permission and knowledge endpoint authorization tests."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import knowledge
from app.core.config import settings
from app.core.database import get_db
from app.core.security import UserContext, has_permission
from app.models import AuditEvent, Base, Employee, Permission, Role
from app.models.role import role_permissions as role_permissions_table


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db_session, monkeypatch):
    role = Role(name="employee")
    employee = Employee(
        employee_id="EMP001",
        full_name="Test Employee",
        email="employee@example.test",
        department="engineering",
        role=role,
        is_active=True,
    )
    policy_permission = Permission(
        code="policy:read",
        description="Read company policies",
    )
    role.permissions.append(policy_permission)
    db_session.add(employee)
    db_session.commit()

    def override_get_db():
        yield db_session

    app = FastAPI()
    app.include_router(knowledge.router)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        knowledge,
        "answer_question",
        lambda question: {"answer": "Test answer", "sources": []},
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _token(employee_id: str = "EMP001") -> str:
    return jwt.encode(
        {
            "sub": employee_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def test_employee_with_policy_permission_can_read_policy(client, db_session):
    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "Test answer", "sources": []}
    assert db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP001",
            AuditEvent.resource_type == "policy",
            AuditEvent.outcome == "success",
        )
    ) is not None


def test_employee_without_policy_permission_is_forbidden(client, db_session):
    employee = db_session.get(Employee, "EMP001")
    employee.role.permissions.clear()
    db_session.commit()

    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 403
    assert db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.actor_employee_id == "EMP001",
            AuditEvent.resource_id == "policy:read",
            AuditEvent.outcome == "denied",
        )
    ) is not None


def test_policy_endpoint_requires_jwt(client):
    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
    )

    assert response.status_code == 401


def test_swagger_uses_http_bearer_scheme(client):
    schema = client.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }


@pytest.mark.parametrize("token_kind", ["invalid", "expired"])
def test_invalid_or_expired_jwt_is_unauthorized(client, token_kind):
    if token_kind == "invalid":
        token = "not-a-valid-jwt"
    else:
        token = jwt.encode(
            {
                "sub": "EMP001",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_knowledge_value_error_is_still_bad_request(client, monkeypatch):
    def raise_value_error(question):
        raise ValueError("Invalid question")

    monkeypatch.setattr(knowledge, "answer_question", raise_value_error)
    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid question"


def test_knowledge_unexpected_error_is_still_internal_server_error(
    client,
    monkeypatch,
):
    def raise_runtime_error(question):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(knowledge, "answer_question", raise_runtime_error)
    response = client.post(
        "/knowledge/ask",
        json={"question": "What is the policy?"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 500


def test_seeded_roles_receive_policy_read_permission(
    db_session,
    monkeypatch,
):
    from app import seed

    employee_role = Role(name="employee")
    unrelated_permission = Permission(
        code="custom:retained",
        description="Existing unrelated permission",
    )
    employee_role.permissions.append(unrelated_permission)
    db_session.add(employee_role)
    db_session.commit()

    monkeypatch.setattr(seed, "SessionLocal", lambda: db_session)
    seed.seed_data()

    permission_rows = db_session.execute(
        select(Role.name, Permission.code)
        .join(Role.permissions)
        .where(Permission.code == "policy:read")
    ).all()

    assert {role_name for role_name, _ in permission_rows} == {
        "admin",
        "employee",
        "hr",
        "manager",
    }
    assert unrelated_permission in employee_role.permissions

    before = (
        db_session.scalar(select(func.count()).select_from(Permission)),
        db_session.scalar(select(func.count()).select_from(Role)),
        db_session.scalar(select(func.count()).select_from(role_permissions_table)),
    )
    seed.seed_data()
    after = (
        db_session.scalar(select(func.count()).select_from(Permission)),
        db_session.scalar(select(func.count()).select_from(Role)),
        db_session.scalar(select(func.count()).select_from(role_permissions_table)),
    )
    assert after == before


def test_employee_cannot_read_performance():
    user = UserContext(employee_id="E001", role="employee", department="engineering")
    assert not has_permission(user, "performance:read")


def test_hr_can_read_performance():
    user = UserContext(employee_id="E002", role="hr", department="people")
    assert has_permission(user, "performance:read")
