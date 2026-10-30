"""Audit logging."""

from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_event import AuditEvent


def record_audit_event(
    db: Session,
    *,
    actor_employee_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_employee_id=actor_employee_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
        )
    )
    db.commit()
