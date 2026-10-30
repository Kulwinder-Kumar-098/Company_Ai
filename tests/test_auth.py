"""Authentication tests."""

"""Authentication service and configuration tests."""

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pydantic import ValidationError

from app.core.config import Settings
from app.models import Base, Employee, Role
from app.models.employee_credential import EmployeeCredential
from app.services.auth_service import (
    authenticate_user,
    hash_password,
    DUMMY_PASSWORD_HASH,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    TestSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    with TestSession() as db:
        role = Role(name="employee")

        employee = Employee(
            employee_id="EMP001",
            full_name="Test Employee",
            email="employee@example.com",
            department="engineering",
            role=role,
            is_active=True,
        )

        credential = EmployeeCredential(
            employee_id="EMP001",
            password_hash=hash_password("correct-password"),
        )

        db.add_all([employee, credential])
        db.commit()

        yield db

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_valid_credentials(db_session):
    employee = authenticate_user(
        db_session,
        "employee@example.com",
        "correct-password",
    )

    assert employee is not None
    assert employee.employee_id == "EMP001"


def test_email_normalization(db_session):
    employee = authenticate_user(
        db_session,
        " EMPLOYEE@EXAMPLE.COM ",
        "correct-password",
    )

    assert employee is not None


def test_invalid_password(db_session):
    employee = authenticate_user(
        db_session,
        "employee@example.com",
        "wrong-password",
    )

    assert employee is None


def test_unknown_email(db_session):
    employee = authenticate_user(
        db_session,
        "unknown@example.com",
        "wrong-password",
    )

    assert employee is None


def test_inactive_employee(db_session):
    employee = db_session.get(Employee, "EMP001")
    employee.is_active = False
    db_session.commit()

    result = authenticate_user(
        db_session,
        "employee@example.com",
        "correct-password",
    )

    assert result is None


def test_production_requires_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            app_env="production",
        )


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            app_env="production",
            jwt_secret_key="short-secret",
        )


def test_production_accepts_strong_length_secret():
    config = Settings(
        _env_file=None,
        database_url="sqlite://",
        app_env="production",
        jwt_secret_key="a" * 32,
    )

    assert config.app_env == "production"


def test_unknown_email_uses_dummy_hash(
    db_session,
    monkeypatch,
):
    from app.services import auth_service

    verified_hashes = []

    def fake_verify(password, hashed_password):
        verified_hashes.append(hashed_password)
        return False

    monkeypatch.setattr(
        auth_service,
        "verify_password",
        fake_verify,
    )

    result = authenticate_user(
        db_session,
        "unknown@example.com",
        "wrong-password",
    )

    assert result is None
    assert verified_hashes == [DUMMY_PASSWORD_HASH]
