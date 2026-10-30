"""Project and task API routes."""

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_any_permission
from app.models.employee import Employee
from app.models.project import Project
from app.models.task import Task
from app.observability.audit import record_audit_event
from app.schemas.resources import ProjectResponse, TaskResponse
from app.services.authorization_service import (
    can_read_all_projects,
    can_read_assigned_projects,
    can_read_project,
    has_permission,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


def _assigned_project_filter(employee_id: str):
    assigned_task = (
        select(Task.task_id)
        .where(
            Task.project_id == Project.project_id,
            Task.assigned_employee_id == employee_id,
        )
        .exists()
    )
    return (Project.project_manager_id == employee_id) | assigned_task


def _deny(
    db: Session,
    employee: Employee,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: str,
    status_code: int,
) -> NoReturn:
    record_audit_event(
        db,
        actor_employee_id=employee.employee_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
    )
    raise HTTPException(status_code=status_code, detail=detail)


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    current_user: Employee = Depends(
        require_any_permission(
            "projects:read:all",
            "projects:read:assigned",
        )
    ),
    db: Session = Depends(get_db),
) -> list[Project]:
    if can_read_all_projects(db, current_user):
        projects = db.scalars(
            select(Project).order_by(Project.project_id)
        ).all()
    elif can_read_assigned_projects(db, current_user):
        projects = db.scalars(
            select(Project)
            .where(_assigned_project_filter(current_user.employee_id))
            .order_by(Project.project_id)
        ).all()
    else:
        _deny(
            db,
            current_user,
            action="list_denied",
            resource_type="project",
            resource_id=None,
            detail="You do not have permission to read projects",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="list",
        resource_type="project",
        resource_id=None,
        outcome="success",
        details={"result_count": len(projects)},
    )
    return projects


@router.get("/{project_id}/tasks", response_model=list[TaskResponse])
def list_project_tasks(
    project_id: str,
    current_user: Employee = Depends(
        require_any_permission(
            "projects:read:all",
            "projects:read:assigned",
        )
    ),
    db: Session = Depends(get_db),
) -> list[Task]:
    project = db.get(Project, project_id)
    if project is None:
        _deny(
            db,
            current_user,
            action="list_denied",
            resource_type="task",
            resource_id=project_id,
            detail="Project not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    has_all_projects = can_read_all_projects(db, current_user)
    has_assigned_projects = can_read_assigned_projects(db, current_user)
    if not has_all_projects and not has_assigned_projects:
        _deny(
            db,
            current_user,
            action="list_denied",
            resource_type="task",
            resource_id=project_id,
            detail="You do not have permission to read project tasks",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if not can_read_project(db, current_user, project_id):
        _deny(
            db,
            current_user,
            action="list_denied",
            resource_type="task",
            resource_id=project_id,
            detail="Project not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not has_all_projects and not has_permission(
        db,
        current_user,
        "tasks:read:assigned",
    ):
        _deny(
            db,
            current_user,
            action="list_denied",
            resource_type="task",
            resource_id=project_id,
            detail="You do not have permission to read project tasks",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    statement = select(Task).where(Task.project_id == project_id)
    if not has_all_projects and project.project_manager_id != current_user.employee_id:
        statement = statement.where(
            Task.assigned_employee_id == current_user.employee_id
        )

    tasks = db.scalars(statement.order_by(Task.task_id)).all()
    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="list",
        resource_type="task",
        resource_id=project_id,
        outcome="success",
        details={"result_count": len(tasks)},
    )
    return tasks


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    current_user: Employee = Depends(
        require_any_permission(
            "projects:read:all",
            "projects:read:assigned",
        )
    ),
    db: Session = Depends(get_db),
) -> Project:
    if not can_read_all_projects(db, current_user) and not can_read_assigned_projects(
        db,
        current_user,
    ):
        _deny(
            db,
            current_user,
            action="read_denied",
            resource_type="project",
            resource_id=project_id,
            detail="You do not have permission to read projects",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if not can_read_project(db, current_user, project_id):
        _deny(
            db,
            current_user,
            action="read_denied",
            resource_type="project",
            resource_id=project_id,
            detail="Project not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    project = db.get(Project, project_id)
    if project is None:
        _deny(
            db,
            current_user,
            action="read_denied",
            resource_type="project",
            resource_id=project_id,
            detail="Project not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    record_audit_event(
        db,
        actor_employee_id=current_user.employee_id,
        action="read",
        resource_type="project",
        resource_id=project_id,
        outcome="success",
    )
    return project
