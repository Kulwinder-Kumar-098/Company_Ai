"""Login rate-limit behavior and concurrency tests."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth_routes import router as auth_router
from app.core.config import settings
from app.core.database import get_db
from app.models import Base, Employee, Role
from app.models.employee_credential import EmployeeCredential
from app.models.login_rate_limit import LoginRateLimit
from app.services import rate_limit_service
from app.services.auth_service import hash_password


@pytest.fixture
def rate_limit_db(monkeypatch):
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        "test-rate-limit-secret-with-more-than-thirty-two-bytes",
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as db:
        yield db

    Base.metadata.drop_all(engine)
    engine.dispose()


def _counter(db, key_hash: str) -> LoginRateLimit | None:
    return db.get(LoginRateLimit, key_hash)


def test_ip_email_counter_locks_out_after_five_failed_attempts(
    rate_limit_db,
):
    db = rate_limit_db
    ip_address = "192.0.2.10"
    email = "person@example.test"

    for _ in range(rate_limit_service.IP_EMAIL_MAX_ATTEMPTS):
        assert rate_limit_service.get_retry_after_seconds(
            db,
            ip_address,
            email,
        ) == 0
        rate_limit_service.record_failed_login(db, ip_address, email)

    assert rate_limit_service.get_retry_after_seconds(
        db,
        ip_address,
        email,
    ) > 0


def test_ip_counter_locks_out_after_ten_failed_attempts(rate_limit_db):
    db = rate_limit_db
    ip_address = "192.0.2.11"

    for number in range(rate_limit_service.IP_MAX_ATTEMPTS):
        email = f"person-{number}@example.test"
        assert rate_limit_service.get_retry_after_seconds(
            db,
            ip_address,
            email,
        ) == 0
        rate_limit_service.record_failed_login(db, ip_address, email)

    assert rate_limit_service.get_retry_after_seconds(
        db,
        ip_address,
        "another-person@example.test",
    ) > 0


def test_retry_after_is_rounded_up_until_window_expires(
    rate_limit_db,
    monkeypatch,
):
    db = rate_limit_db
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limit_service, "_now", lambda: now)

    for _ in range(rate_limit_service.IP_EMAIL_MAX_ATTEMPTS):
        rate_limit_service.record_failed_login(
            db,
            "192.0.2.12",
            "person@example.test",
        )

    monkeypatch.setattr(
        rate_limit_service,
        "_now",
        lambda: now + timedelta(seconds=899.2),
    )
    assert rate_limit_service.get_retry_after_seconds(
        db,
        "192.0.2.12",
        "person@example.test",
    ) == 1


def test_expired_window_allows_login_and_next_failure_starts_new_window(
    rate_limit_db,
    monkeypatch,
):
    db = rate_limit_db
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limit_service, "_now", lambda: now)
    ip_address = "192.0.2.13"
    email = "person@example.test"

    for _ in range(rate_limit_service.IP_EMAIL_MAX_ATTEMPTS):
        rate_limit_service.record_failed_login(db, ip_address, email)

    later = now + timedelta(minutes=15, microseconds=1)
    monkeypatch.setattr(rate_limit_service, "_now", lambda: later)
    assert rate_limit_service.get_retry_after_seconds(
        db,
        ip_address,
        email,
    ) == 0

    rate_limit_service.record_failed_login(db, ip_address, email)
    _, email_key = rate_limit_service._get_keys(ip_address, email)
    row = _counter(db, email_key)
    assert row is not None
    assert row.attempt_count == 1
    assert row.window_started_at.replace(tzinfo=timezone.utc) == later


def test_successful_login_resets_only_matching_ip_email_counter(
    rate_limit_db,
):
    db = rate_limit_db
    ip_address = "192.0.2.14"
    email = "person@example.test"
    other_email = "other@example.test"

    for _ in range(3):
        rate_limit_service.record_failed_login(db, ip_address, email)
    rate_limit_service.record_failed_login(db, ip_address, other_email)

    ip_key, email_key = rate_limit_service._get_keys(ip_address, email)
    _, other_email_key = rate_limit_service._get_keys(ip_address, other_email)
    rate_limit_service.reset_email_failures(db, ip_address, email)

    assert _counter(db, email_key) is None
    assert _counter(db, other_email_key) is not None
    assert _counter(db, ip_key) is not None


def test_login_endpoint_returns_429_and_retry_after(rate_limit_db):
    db = rate_limit_db
    role = Role(name="employee")
    employee = Employee(
        employee_id="EMP-RATE",
        full_name="Rate Limit Test",
        email="rate-limit@example.test",
        department="test",
        role=role,
        is_active=True,
    )
    credential = EmployeeCredential(
        employee_id=employee.employee_id,
        password_hash=hash_password("correct-password"),
    )
    db.add_all([employee, credential])
    db.commit()

    app = FastAPI()
    app.include_router(auth_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        for _ in range(rate_limit_service.IP_EMAIL_MAX_ATTEMPTS):
            response = client.post(
                "/auth/login",
                json={
                    "email": employee.email,
                    "password": "wrong-password",
                },
            )
            assert response.status_code == 401

        limited = client.post(
            "/auth/login",
            json={
                "email": employee.email,
                "password": "correct-password",
            },
        )

    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= (
        rate_limit_service.WINDOW_MINUTES * 60
    )
    app.dependency_overrides.clear()


def test_successful_login_resets_email_counter(rate_limit_db):
    db = rate_limit_db
    role = Role(name="employee")
    employee = Employee(
        employee_id="EMP-RESET",
        full_name="Reset Test",
        email="reset@example.test",
        department="test",
        role=role,
        is_active=True,
    )
    db.add_all(
        [
            employee,
            EmployeeCredential(
                employee_id=employee.employee_id,
                password_hash=hash_password("correct-password"),
            ),
        ]
    )
    db.commit()

    ip_address = "testclient"
    for _ in range(3):
        rate_limit_service.record_failed_login(
            db,
            ip_address,
            employee.email,
        )

    app = FastAPI()
    app.include_router(auth_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "email": employee.email,
                "password": "correct-password",
            },
        )

    assert response.status_code == 200
    _, email_key = rate_limit_service._get_keys(ip_address, employee.email)
    assert _counter(db, email_key) is None
    app.dependency_overrides.clear()


@pytest.mark.skipif(
    not os.getenv("RATE_LIMIT_TEST_DATABASE_URL"),
    reason="Set RATE_LIMIT_TEST_DATABASE_URL to run PostgreSQL concurrency test",
)
def test_postgresql_concurrent_attempts_do_not_pass_threshold():
    database_url = os.environ["RATE_LIMIT_TEST_DATABASE_URL"]
    engine = create_engine(database_url, pool_size=12, max_overflow=0)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    ip_address = f"rate-limit-concurrency-{uuid4()}"
    email = f"{uuid4()}@example.test"
    ip_key, email_key = rate_limit_service._get_keys(ip_address, email)
    workers = 8
    barrier = Barrier(workers)

    try:
        with test_session() as db:
            for _ in range(rate_limit_service.IP_EMAIL_MAX_ATTEMPTS - 1):
                rate_limit_service.record_failed_login(
                    db,
                    ip_address,
                    email,
                )

        def attempt_login() -> int:
            barrier.wait(timeout=10)
            with test_session() as db:
                retry_after = rate_limit_service.get_retry_after_seconds(
                    db,
                    ip_address,
                    email,
                )
                if retry_after:
                    db.rollback()
                    return retry_after
                rate_limit_service.record_failed_login(
                    db,
                    ip_address,
                    email,
                )
                return 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            outcomes = list(executor.map(lambda _: attempt_login(), range(workers)))

        assert outcomes.count(0) == 1
        assert all(value > 0 for value in outcomes if value)
    finally:
        with test_session() as db:
            db.execute(
                delete(LoginRateLimit).where(
                    LoginRateLimit.key_hash.in_((ip_key, email_key))
                )
            )
            db.commit()
        engine.dispose()
