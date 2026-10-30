"""Employee API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import require_any_permission
from app.core.database import get_db
from app.models.employee import Employee
from app.observability.audit import record_audit_event
from app.schemas.resources import EmployeeResponse
from app.services.authorization_service import (
    can_read_employee,
    get_user_permissions,
)

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get("", response_model=list[EmployeeResponse])
def list_employees(
    current_user: Employee = Depends(
        require_any_permission("employees:read:all", "employees:read:self")
    ),
    db: Session = Depends(get_db),
) -> list[Employee]:
    permissions = get_user_permissions(db, current_user)

    if "employees:read:all" in permissions:
        employees = db.scalars(
            select(Employee).order_by(Employee.employee_id)
        ).all()
    elif "employees:read:self" in permissions:
        employee = db.get(Employee, current_user.employee_id)
        employees = [employee] if employee is not None else []
    else:
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="list_denied",
            resource_type="employee",
            resource_id=None,
            outcome="denied",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to read employee records",
        )

    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="list",
        resource_type="employee",
        resource_id=None,
        outcome="success",
        details={"result_count": len(employees)},
    )
    return employees


@router.get("/{employee_id}", response_model=EmployeeResponse)
def get_employee(
    employee_id: str,
    current_user: Employee = Depends(
        require_any_permission("employees:read:all", "employees:read:self")
    ),
    db: Session = Depends(get_db),
) -> Employee:
    if not can_read_employee(db, current_user, employee_id):
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="read_denied",
            resource_type="employee",
            resource_id=employee_id,
            outcome="denied",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to read this employee record",
        )

    employee = db.get(Employee, employee_id)
    if employee is None:
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="read",
            resource_type="employee",
            resource_id=employee_id,
            outcome="not_found",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="read",
        resource_type="employee",
        resource_id=employee_id,
        outcome="success",
    )
    return employee
