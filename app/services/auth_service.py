
"""Authentication service."""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.employee import Employee
from app.models.employee_credential import EmployeeCredential


password_hash = PasswordHash.recommended()

# Generated once per application process.
# Used to perform password verification when an account
# cannot proceed to normal credential verification.
DUMMY_PASSWORD_HASH = password_hash.hash(
    "company-ai-dummy-password-verification"
)


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
        "exp": now + timedelta(
            minutes=settings.access_token_expire_minutes
        ),
    }

    return jwt.encode(
        payload,
        secret_key,
        algorithm=settings.jwt_algorithm,
    )


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> Employee | None:
    """Return the employee only when credentials are valid."""

    normalized_email = email.strip().lower()

    statement = select(Employee).where(
        func.lower(Employee.email) == normalized_email
    )

    employee = db.scalar(statement)

    # Unknown account: perform dummy Argon2 verification.
    if employee is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    credential = db.get(
        EmployeeCredential,
        employee.employee_id,
    )

    # Inactive account or missing credentials:
    # perform the same dummy verification.
    if not employee.is_active or credential is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    # Existing account: verify the actual password hash.
    if not verify_password(password, credential.password_hash):
        return None

    return employee
