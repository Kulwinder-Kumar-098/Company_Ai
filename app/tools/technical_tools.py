"""Task lookup and search tools."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.task import Task
from app.schemas.tools import (
    OverdueTasksInput,
    TaskData,
    TaskSearchInput,
    TaskToolInput,
    ToolResult,
)
from app.tools.common import (
    denied,
    not_found,
    overdue_conditions,
    project_scope,
    success,
    task_is_visible,
    task_output_list,
    visible_task_statement,
)


def get_task_details(
    db: Session,
    actor: Employee,
    args: TaskToolInput,
) -> ToolResult[TaskData]:
    task_id = args.task_id
    task = db.get(Task, task_id)
    if task is None:
        return not_found(db, actor, "task", task_id)
    if not task_is_visible(db, actor, task):
        return denied(db, actor, "task", task_id)
    return success(
        db,
        actor,
        "task",
        task_id,
        TaskData.model_validate(task),
    )


def search_tasks(
    db: Session,
    actor: Employee,
    args: TaskSearchInput,
) -> ToolResult[list[TaskData]]:
    statement = visible_task_statement(db, actor)
    if statement is None:
        return denied(db, actor, "task", None)
    if args.project_id:
        if not project_scope(db, actor, args.project_id):
            return denied(db, actor, "task", args.project_id)
        statement = statement.where(Task.project_id == args.project_id)
    if args.query:
        statement = statement.where(
            Task.task_name.ilike(f"%{args.query.strip()}%")
        )
    if args.status:
        statement = statement.where(Task.status == args.status)
    tasks = db.scalars(
        statement.order_by(Task.task_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    return success(
        db,
        actor,
        "task",
        None,
        task_output_list(tasks),
        result_count=len(tasks),
        action="list",
    )


def get_overdue_tasks(
    db: Session,
    actor: Employee,
    args: OverdueTasksInput,
) -> ToolResult[list[TaskData]]:
    statement = visible_task_statement(db, actor)
    if statement is None:
        return denied(db, actor, "task", args.project_id)
    if args.project_id:
        if not project_scope(db, actor, args.project_id):
            return denied(db, actor, "task", args.project_id)
        statement = statement.where(Task.project_id == args.project_id)
    statement = statement.where(overdue_conditions())
    tasks = db.scalars(
        statement.order_by(Task.due_date, Task.task_id)
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
