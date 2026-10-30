"""Permission definitions and helpers."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.employee import Employee
from app.observability.audit import record_audit_event
from app.services.authorization_service import get_user_permissions


def require_any_permission(*permission_codes: str) -> Callable[..., Employee]:
    def permission_checker(
        current_user: Employee = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Employee:
        user_permissions = get_user_permissions(db, current_user)
        if not user_permissions.intersection(permission_codes):
            record_audit_event(
                db,
                actor_employee_id=current_user.employee_id,
                action="read_denied",
                resource_type="permission",
                resource_id=",".join(permission_codes),
                outcome="denied",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )

        return current_user

    return permission_checker


def require_permission(permission_code: str) -> Callable[..., Employee]:
    return require_any_permission(permission_code)
