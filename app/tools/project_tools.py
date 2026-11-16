"""Project and project-team data tools."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.project import Project
from app.models.task import Task
from app.schemas.tools import (
    ProjectData,
    ProjectListInput,
    ProjectStatusData,
    ProjectTaskInput,
    ProjectTeamInput,
    ProjectToolInput,
    TaskData,
    TeamMemberData,
    ToolResult,
)
from app.services.authorization_service import (
    can_read_all_projects,
    has_permission,
)
from app.tools.common import (
    denied,
    not_found,
    project_scope,
    success,
    task_output_list,
)


def _readable_project(
    db: Session,
    actor: Employee,
    project_id: str,
) -> tuple[Project | None, bool]:
    project = db.get(Project, project_id)
    if project is None:
        return None, False
    if not project_scope(db, actor, project_id):
        return project, False
    return project, True


def get_project_details(
    db: Session,
    actor: Employee,
    args: ProjectToolInput,
) -> ToolResult[ProjectData]:
    project, is_readable = _readable_project(db, actor, args.project_id)
    if project is None:
        return not_found(db, actor, "project", args.project_id)
    if not is_readable:
        return denied(db, actor, "project", args.project_id)
    return success(
        db,
        actor,
        "project",
        args.project_id,
        ProjectData.model_validate(project),
    )


def list_projects(
    db: Session,
    actor: Employee,
    args: ProjectListInput,
) -> ToolResult[list[ProjectData]]:
    can_read_all = can_read_all_projects(db, actor)
    can_read_assigned = has_permission(db, actor, "projects:read:assigned") or has_permission(db, actor, "projects:read")
    if not can_read_all and not can_read_assigned:
        return denied(db, actor, "project", None)

    statement = select(Project)
    if args.status:
        statement = statement.where(Project.status.ilike(f"%{args.status.strip()}%"))

    projects = db.scalars(
        statement.order_by(Project.project_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()

    visible = [
        ProjectData.model_validate(p)
        for p in projects
        if can_read_all or project_scope(db, actor, p.project_id)
    ]
    return success(
        db,
        actor,
        "project",
        None,
        visible,
        result_count=len(visible),
        action="list",
    )


def get_project_status(
    db: Session,
    actor: Employee,
    args: ProjectToolInput,
) -> ToolResult[ProjectStatusData]:
    project, is_readable = _readable_project(db, actor, args.project_id)
    if project is None:
        return not_found(db, actor, "project", args.project_id)
    if not is_readable:
        return denied(db, actor, "project", args.project_id)
    data = ProjectStatusData(
        project_id=project.project_id,
        status=project.status,
        completion_percentage=project.completion_percentage,
        planned_end_date=project.planned_end_date,
        actual_end_date=project.actual_end_date,
        risk_level=project.risk_level,
    )
    return success(db, actor, "project", args.project_id, data)


def get_project_tasks(
    db: Session,
    actor: Employee,
    args: ProjectTaskInput,
) -> ToolResult[list[TaskData]]:
    project, is_readable = _readable_project(db, actor, args.project_id)
    if project is None:
        return not_found(db, actor, "task", args.project_id)
    if not is_readable:
        return denied(db, actor, "task", args.project_id)
    if not can_read_all_projects(db, actor) and not has_permission(
        db,
        actor,
        "tasks:read:assigned",
    ):
        return denied(db, actor, "task", args.project_id)

    statement = select(Task).where(Task.project_id == args.project_id)
    if (
        not can_read_all_projects(db, actor)
        and project.project_manager_id != actor.employee_id
    ):
        statement = statement.where(
            Task.assigned_employee_id == actor.employee_id
        )
    tasks = db.scalars(
        statement.order_by(Task.task_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    return success(
        db,
        actor,
        "task",
        args.project_id,
        task_output_list(tasks),
        result_count=len(tasks),
        action="list",
    )


def get_project_team(
    db: Session,
    actor: Employee,
    args: ProjectTeamInput,
) -> ToolResult[list[TeamMemberData]]:
    project, is_readable = _readable_project(db, actor, args.project_id)
    if project is None:
        return not_found(db, actor, "project_team", args.project_id)
    if not is_readable:
        return denied(db, actor, "project_team", args.project_id)

    assigned_ids = select(Task.assigned_employee_id).where(
        Task.project_id == args.project_id,
        Task.assigned_employee_id.is_not(None),
    )
    members = db.scalars(
        select(Employee)
        .where(
            (Employee.employee_id == project.project_manager_id)
            | Employee.employee_id.in_(assigned_ids)
        )
        .order_by(Employee.employee_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    data = [
        TeamMemberData(
            employee_id=member.employee_id,
            full_name=member.full_name,
            job_title=member.job_title,
        )
        for member in members
    ]
    return success(
        db,
        actor,
        "project_team",
        args.project_id,
        data,
        result_count=len(data),
        action="list",
    )
