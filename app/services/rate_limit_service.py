
"""PostgreSQL-backed login rate limiting."""

import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.login_rate_limit import LoginRateLimit


WINDOW_MINUTES = 15
IP_MAX_ATTEMPTS = 10
IP_EMAIL_MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_key_hash(value: str) -> str:
    secret = settings.jwt_secret_key

    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is required for login rate limiting"
        )

    return hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get_keys(ip_address: str, email: str) -> tuple[str, str]:
    ip_key = _make_key_hash(f"ip:{ip_address}")
    email_key = _make_key_hash(
        f"ip_email:{ip_address}:{email.strip().lower()}"
    )
    return ip_key, email_key


def _lock_login_keys(
    db: Session,
    keys: tuple[str, str],
) -> None:
    """Serialize login checks and failed-count updates for this IP/email."""
    if db.get_bind().dialect.name != "postgresql":
        return

    lock_ids = sorted(
        int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
        for key in keys
    )
    for lock_id in lock_ids:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )


def _is_window_active(
    window_started_at: datetime,
    now: datetime,
) -> bool:
    # SQLite may return naive datetimes in tests.
    if window_started_at.tzinfo is None:
        window_started_at = window_started_at.replace(
            tzinfo=timezone.utc
        )

    return now - window_started_at < timedelta(
        minutes=WINDOW_MINUTES
    )


def get_retry_after_seconds(
    db: Session,
    ip_address: str,
    email: str,
) -> int:
    """Return remaining lockout seconds, or zero if not limited."""

    ip_key, email_key = _get_keys(ip_address, email)
    _lock_login_keys(db, (ip_key, email_key))
    now = _now()

    limits = {
        ip_key: IP_MAX_ATTEMPTS,
        email_key: IP_EMAIL_MAX_ATTEMPTS,
    }

    rows = db.scalars(
        select(LoginRateLimit).where(
            LoginRateLimit.key_hash.in_(limits.keys())
        )
    ).all()

    retry_after = 0

    for row in rows:
        if (
            row.attempt_count >= limits[row.key_hash]
            and _is_window_active(row.window_started_at, now)
        ):
            started_at = row.window_started_at

            if started_at.tzinfo is None:
                started_at = started_at.replace(
                    tzinfo=timezone.utc
                )

            elapsed = (now - started_at).total_seconds()
            remaining = math.ceil(WINDOW_MINUTES * 60 - elapsed)

            retry_after = max(retry_after, remaining)

    return max(0, retry_after)


def is_login_rate_limited(
    db: Session,
    ip_address: str,
    email: str,
) -> bool:
    return (
        get_retry_after_seconds(db, ip_address, email) > 0
    )


def record_failed_login(
    db: Session,
    ip_address: str,
    email: str,
) -> None:
    """Atomically increment both failed-login counters."""

    ip_key, email_key = _get_keys(ip_address, email)
    now = _now()
    cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    dialect = db.get_bind().dialect.name

    if dialect == "postgresql":
        insert_statement = insert
    elif dialect == "sqlite":
        insert_statement = sqlite_insert
    else:
        raise RuntimeError(
            f"Login rate limiting does not support database dialect {dialect!r}"
        )

    for key_hash in (ip_key, email_key):
        expired = LoginRateLimit.window_started_at <= cutoff

        statement = (
            insert_statement(LoginRateLimit)
            .values(
                key_hash=key_hash,
                attempt_count=1,
                window_started_at=now,
            )
            .on_conflict_do_update(
                index_elements=[LoginRateLimit.key_hash],
                set_={
                    "attempt_count": case(
                        (expired, 1),
                        else_=LoginRateLimit.attempt_count + 1,
                    ),
                    "window_started_at": case(
                        (expired, now),
                        else_=LoginRateLimit.window_started_at,
                    ),
                    "updated_at": now,
                },
            )
        )

        db.execute(statement)

    db.commit()


def reset_email_failures(
    db: Session,
    ip_address: str,
    email: str,
) -> None:
    """Clear only the matching IP + email failure counter."""

    _, email_key = _get_keys(ip_address, email)

    row = db.get(LoginRateLimit, email_key)

    if row is not None:
        db.delete(row)
    db.commit()
