"""Authentication service."""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.employee import Employee
from app.models.employee_credential import EmployeeCredential


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return a secure Argon2 password hash."""
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a password against its stored hash."""
    return password_hash.verify(password, hashed_password)


def create_access_token(employee_id: str) -> str:
    """Create a signed JWT for an authenticated employee."""
    secret_key = settings.jwt_secret_key
    if not secret_key:
        raise RuntimeError("JWT_SECRET_KEY is not configured")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": employee_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }

    return jwt.encode(payload, secret_key, algorithm=settings.jwt_algorithm)


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> Employee | None:
    """Return the employee only when their credentials are valid."""
    statement = select(Employee).where(Employee.email == email)
    employee = db.scalar(statement)

    if employee is None or not employee.is_active:
        return None

    credential = db.get(
        EmployeeCredential,
        employee.employee_id,
    )

    if credential is None:
        return None

    if not verify_password(password, credential.password_hash):
        return None

    return employee
