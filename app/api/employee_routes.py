"""Employee API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import require_any_permission, require_permission
from app.core.database import get_db
from app.models.document import Document
from app.models.employee import Employee
from app.models.employee_credential import EmployeeCredential
from app.models.performance_metric import PerformanceMetric
from app.models.project import Project
from app.models.task import Task
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


@router.delete("/{employee_id}")
def delete_employee(
    employee_id: str,
    current_user: Employee = Depends(
        require_permission("roles:manage")
    ),
    db: Session = Depends(get_db),
):
    """Admin-only: Remove an employee and unlink their assignments cleanly."""
    if current_user.employee_id == employee_id:
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="delete_denied",
            resource_type="employee",
            resource_id=employee_id,
            outcome="denied",
            details={"reason": "cannot_delete_self"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own active administrator account.",
        )

    target = db.get(Employee, employee_id)
    if target is None:
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="delete",
            resource_type="employee",
            resource_id=employee_id,
            outcome="not_found",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee '{employee_id}' not found.",
        )

    full_name = target.full_name
    department = target.department

    # 1. Remove credentials
    creds = db.scalars(
        select(EmployeeCredential).where(EmployeeCredential.employee_id == employee_id)
    ).all()
    for c in creds:
        db.delete(c)

    # 2. Remove performance records
    metrics = db.scalars(
        select(PerformanceMetric).where(PerformanceMetric.employee_id == employee_id)
    ).all()
    for m in metrics:
        db.delete(m)

    # 3. Unassign tasks
    tasks = db.scalars(
        select(Task).where(Task.assigned_employee_id == employee_id)
    ).all()
    for t in tasks:
        t.assigned_employee_id = None

    # 4. Unset project manager
    projects = db.scalars(
        select(Project).where(Project.project_manager_id == employee_id)
    ).all()
    for p in projects:
        p.project_manager_id = None

    # 5. Unset document uploader
    docs = db.scalars(
        select(Document).where(Document.uploaded_by_employee_id == employee_id)
    ).all()
    for d in docs:
        d.uploaded_by_employee_id = None

    # 6. Delete the employee record
    db.delete(target)

    # 7. Record audit log
    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="delete",
        resource_type="employee",
        resource_id=employee_id,
        outcome="success",
        details={"full_name": full_name, "department": department},
    )

    db.commit()

    return {
        "status": "success",
        "message": f"Employee {full_name} ({employee_id}) has been removed from the system.",
        "employee_id": employee_id,
    }
