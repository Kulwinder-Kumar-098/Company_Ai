"""Employee data access."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.employee import Employee


def get_employee(
    db: Session,
    employee_id: str,
    ) -> Employee | None:
    statement = (
        select(Employee).options(joinedload(Employee.role)).where(Employee.employee_id == employee_id)
    )
    return db.scalar(statement)


def list_employees(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    ) -> list[Employee]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    statement = (
        select(Employee).options(joinedload(Employee.role)).order_by(Employee.employee_id).limit(limit).offset(offset)
    )
    return list(db.scalars(statement).unique().all())


def create_employee(
    db: Session,
    *,
    employee_id: str,
    full_name: str,
    email: str,
    department: str,
    role_id: int,
    job_title: str | None = None,
    ) -> Employee:
    employee = Employee(
        employee_id=employee_id,
        full_name=full_name,
        email=email,
        department=department,
        role_id=role_id,
        job_title=job_title,
    )

    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee
