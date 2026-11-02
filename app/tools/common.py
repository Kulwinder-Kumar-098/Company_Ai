"""Shared authorization, audit, and query helpers for data tools."""

from collections.abc import Sequence
from datetime import date
from typing import TypeVar

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.performance_metric import PerformanceMetric
from app.models.project import Project
from app.models.task import Task
from app.observability.audit import record_audit_event
from app.schemas.tools import PerformanceSummaryData, TaskData, ToolResult
from app.services.authorization_service import (
    can_read_all_projects,
    can_read_assigned_projects,
    can_read_employee,
    can_read_project,
    has_permission,
)

T = TypeVar("T")


def audit(
    db: Session,
    actor: Employee,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    result_count: int | None = None,
) -> None:
    details = {"result_count": result_count} if result_count is not None else None
    record_audit_event(
        db,
        actor_employee_id=actor.employee_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        details=details,
    )


def denied(
    db: Session,
    actor: Employee,
    resource_type: str,
    resource_id: str | None,
) -> ToolResult[T]:
    audit(
        db,
        actor,
        action="read_denied",
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
    )
    return ToolResult(status="denied", error="Access denied")


def not_found(
    db: Session,
    actor: Employee,
    resource_type: str,
    resource_id: str | None,
) -> ToolResult[T]:
    audit(
        db,
        actor,
        action="read",
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="not_found",
    )
    return ToolResult(status="not_found", error="Resource not found")


def success(
    db: Session,
    actor: Employee,
    resource_type: str,
    resource_id: str | None,
    data: T,
    *,
    result_count: int | None = None,
    action: str = "read",
) -> ToolResult[T]:
    audit(
        db,
        actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
        result_count=result_count,
    )
    return ToolResult(status="ok", data=data)


def employee_scope(
    db: Session,
    actor: Employee,
    employee_id: str,
) -> bool:
    return can_read_employee(db, actor, employee_id)


def project_scope(
    db: Session,
    actor: Employee,
    project_id: str,
) -> bool:
    return (
        can_read_all_projects(db, actor)
        or (
            can_read_assigned_projects(db, actor)
            and can_read_project(db, actor, project_id)
        )
    )


def visible_task_statement(
    db: Session,
    actor: Employee,
) -> Select[tuple[Task]] | None:
    if can_read_all_projects(db, actor):
        return select(Task)
    if not (
        can_read_assigned_projects(db, actor)
        and has_permission(db, actor, "tasks:read:assigned")
    ):
        return None

    return (
        select(Task)
        .join(Project, Project.project_id == Task.project_id)
        .where(
            or_(
                Project.project_manager_id == actor.employee_id,
                Task.assigned_employee_id == actor.employee_id,
            )
        )
    )


def task_is_visible(db: Session, actor: Employee, task: Task) -> bool:
    if not project_scope(db, actor, task.project_id):
        return False
    if can_read_all_projects(db, actor):
        return True
    return (
        has_permission(db, actor, "tasks:read:assigned")
        and (
            task.assigned_employee_id == actor.employee_id
            or db.scalar(
                select(Project.project_manager_id).where(
                    Project.project_id == task.project_id
                )
            ) == actor.employee_id
        )
    )


def performance_filter(
    db: Session,
    actor: Employee,
    *,
    employee_id: str | None = None,
    department: str | None = None,
):
    permissions = {
        code
        for code in (
            "performance:read:self",
            "performance:read:department",
            "performance:read:all",
        )
        if has_permission(db, actor, code)
    }

    if "performance:read:all" in permissions:
        conditions = []
        if employee_id is not None:
            conditions.append(PerformanceMetric.employee_id == employee_id)
        if department is not None:
            conditions.append(PerformanceMetric.department == department)
        return and_(*conditions) if conditions else True

    if "performance:read:department" in permissions:
        if department is not None and department != actor.department:
            return None
        conditions = [
            PerformanceMetric.department == actor.department,
            Employee.department == actor.department,
        ]
        if employee_id is not None:
            conditions.append(PerformanceMetric.employee_id == employee_id)
        return and_(*conditions)

    if "performance:read:self" in permissions:
        if employee_id is not None and employee_id != actor.employee_id:
            return None
        if department is not None and department != actor.department:
            return None
        conditions = [PerformanceMetric.employee_id == actor.employee_id]
        if department is not None:
            conditions.append(PerformanceMetric.department == department)
        return and_(*conditions)

    return None


def summarize_metrics(rows: Sequence[tuple]) -> PerformanceSummaryData:
    if not rows or rows[0][0] == 0:
        return PerformanceSummaryData(
            metric_count=0,
            goals_completed=0,
            goals_total=0,
            average_task_completion_rate=None,
            average_quality_score=None,
            average_collaboration_score=None,
            average_attendance_rate=None,
        )
    (
        metric_count,
        employee_count,
        goals_completed,
        goals_total,
        task_completion_rate,
        quality_score,
        collaboration_score,
        attendance_rate,
    ) = rows[0]
    return PerformanceSummaryData(
        metric_count=metric_count,
        employee_count=employee_count or 0,
        goals_completed=goals_completed or 0,
        goals_total=goals_total or 0,
        average_task_completion_rate=float(task_completion_rate),
        average_quality_score=float(quality_score),
        average_collaboration_score=float(collaboration_score),
        average_attendance_rate=float(attendance_rate),
    )


def performance_summary_statement(
    where_clause,
    *,
    quarter: str | None = None,
):
    conditions = [where_clause]
    if quarter is not None:
        conditions.append(PerformanceMetric.quarter == quarter)
    return select(
        func.count(PerformanceMetric.record_id),
        func.count(func.distinct(PerformanceMetric.employee_id)),
        func.coalesce(func.sum(PerformanceMetric.goals_completed), 0),
        func.coalesce(func.sum(PerformanceMetric.goals_total), 0),
        func.avg(PerformanceMetric.task_completion_rate),
        func.avg(PerformanceMetric.quality_score),
        func.avg(PerformanceMetric.collaboration_score),
        func.avg(PerformanceMetric.attendance_rate),
    ).select_from(PerformanceMetric).join(
        Employee, Employee.employee_id == PerformanceMetric.employee_id
    ).where(*conditions)


def task_output_list(tasks: Sequence[Task]) -> list[TaskData]:
    return [TaskData.model_validate(task) for task in tasks]


def overdue_conditions():
    return and_(
        Task.due_date.is_not(None),
        Task.due_date < date.today(),
        Task.completed_date.is_(None),
        func.lower(Task.status) != "completed",
    )
