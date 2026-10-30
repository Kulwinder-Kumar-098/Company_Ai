"""Authorization service."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.project import Project
from app.models.role import Permission, Role
from app.models.task import Task


def get_user_permissions(
    db: Session,
    employee: Employee,
) -> set[str]:
    statement = (
        select(Permission.code)
        .select_from(Employee)
        .join(Employee.role)
        .join(Role.permissions)
        .where(Employee.employee_id == employee.employee_id)
    )

    return set(db.scalars(statement).all())


def has_permission(
    db: Session,
    employee: Employee,
    permission_code: str,
) -> bool:
    permissions = get_user_permissions(db, employee)
    return permission_code in permissions


def can_read_employee(
    db: Session,
    actor: Employee,
    target_employee_id: str,
) -> bool:
    permissions = get_user_permissions(db, actor)
    return (
        "employees:read:all" in permissions
        or (
            target_employee_id == actor.employee_id
            and "employees:read:self" in permissions
        )
    )


def can_read_all_projects(db: Session, employee: Employee) -> bool:
    return has_permission(db, employee, "projects:read:all")


def can_read_assigned_projects(db: Session, employee: Employee) -> bool:
    return has_permission(db, employee, "projects:read:assigned")


def can_read_project(
    db: Session,
    employee: Employee,
    project_id: str,
) -> bool:
    if can_read_all_projects(db, employee):
        return db.get(Project, project_id) is not None

    if not can_read_assigned_projects(db, employee):
        return False

    assigned_task = (
        select(Task.task_id)
        .where(
            Task.project_id == Project.project_id,
            Task.assigned_employee_id == employee.employee_id,
        )
        .exists()
    )
    statement = select(Project.project_id).where(
        Project.project_id == project_id,
        (Project.project_manager_id == employee.employee_id) | assigned_task,
    )
    return db.scalar(statement) is not None
