"""Employee and performance data tools."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.models.performance_metric import PerformanceMetric
from app.models.role import Role
from app.models.task import Task
from app.schemas.tools import (
    DepartmentHeadcount,
    EmployeeCountData,
    EmployeeCountInput,
    EmployeeData,
    EmployeeSearchInput,
    EmployeeTasksInput,
    EmployeeToolInput,
    PerformanceDepartmentInput,
    PerformanceMetricData,
    PerformanceMetricsInput,
    PerformanceSummaryData,
    TaskData,
    ToolResult,
)
from app.services.authorization_service import has_permission
from app.tools.common import (
    denied,
    employee_scope,
    not_found,
    performance_filter,
    performance_summary_statement,
    success,
    summarize_metrics,
    task_output_list,
    visible_task_statement,
)


def get_employee_details(
    db: Session,
    actor: Employee,
    args: EmployeeToolInput,
) -> ToolResult[EmployeeData]:
    employee_id = args.employee_id or actor.employee_id
    if not employee_scope(db, actor, employee_id):
        return denied(db, actor, "employee", employee_id)

    employee = db.get(Employee, employee_id)
    if employee is None:
        return not_found(db, actor, "employee", employee_id)

    return success(
        db,
        actor,
        "employee",
        employee_id,
        EmployeeData.model_validate(employee),
    )


def search_employees(
    db: Session,
    actor: Employee,
    args: EmployeeSearchInput,
) -> ToolResult[list[EmployeeData]]:
    can_read_all = has_permission(db, actor, "employees:read:all")
    can_read_self = has_permission(db, actor, "employees:read:self")
    if not can_read_all and not can_read_self:
        return denied(db, actor, "employee", None)

    statement = select(Employee)
    if not can_read_all:
        statement = statement.where(Employee.employee_id == actor.employee_id)
    if args.query:
        pattern = f"%{args.query.strip()}%"
        statement = statement.where(
            (Employee.full_name.ilike(pattern))
            | (Employee.email.ilike(pattern))
            | (Employee.employee_id.ilike(pattern))
            | (Employee.job_title.ilike(pattern))
        )
    if args.department:
        dept_raw = args.department.strip()
        dept_lower = dept_raw.lower()
        if dept_lower in {"hr", "human resources", "human resource"}:
            statement = statement.where(Employee.department.ilike("%Human Resources%"))
        elif dept_lower in {"ai", "ml", "ai/ml", "aiml", "ai-ml"}:
            statement = statement.where(Employee.department.ilike("%AI/ML%"))
        else:
            statement = statement.where(Employee.department.ilike(f"%{dept_raw}%"))

    employees = db.scalars(
        statement.order_by(Employee.employee_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    visible = [
        EmployeeData.model_validate(employee)
        for employee in employees
        if employee_scope(db, actor, employee.employee_id)
    ]
    return success(
        db,
        actor,
        "employee",
        None,
        visible,
        result_count=len(visible),
        action="list",
    )


def get_employee_count(
    db: Session,
    actor: Employee,
    args: EmployeeCountInput,
) -> ToolResult[EmployeeCountData]:
    can_read_all = has_permission(db, actor, "employees:read:all")
    can_read_self = has_permission(db, actor, "employees:read:self")
    can_chat = has_permission(db, actor, "chat:use")
    if not can_read_all and not can_read_self and not can_chat:
        return denied(db, actor, "employee_count", None)

    statement = select(Employee).where(Employee.is_active.is_(True))
    dept_filter = None
    if args.department:
        dept_raw = args.department.strip()
        dept_lower = dept_raw.lower()
        if dept_lower in {"hr", "human resources", "human resource"}:
            statement = statement.where(Employee.department.ilike("%Human Resources%"))
            dept_filter = "Human Resources"
        elif dept_lower in {"ai", "ml", "ai/ml", "ai-ml", "aiml"}:
            statement = statement.where(Employee.department.ilike("%AI/ML%"))
            dept_filter = "AI/ML"
        elif dept_lower in {"it", "it operations", "it-operations"}:
            statement = statement.where(Employee.department.ilike("%IT Operations%"))
            dept_filter = "IT Operations"
        elif dept_lower in {"pm", "project management"}:
            statement = statement.where(Employee.department.ilike("%Project Management%"))
            dept_filter = "Project Management"
        else:
            statement = statement.where(Employee.department.ilike(f"%{dept_raw}%"))
            dept_filter = dept_raw

    if args.role:
        role_raw = args.role.strip().lower()
        statement = statement.join(Employee.role).where(Role.name.ilike(f"%{role_raw}%"))

    if args.job_title:
        title_raw = args.job_title.strip()
        title_lower = title_raw.lower()
        if "ai" in title_lower or "ml" in title_lower:
            statement = statement.where(
                Employee.job_title.ilike(f"%{title_raw}%")
                | Employee.job_title.ilike("%ML Engineer%")
                | Employee.job_title.ilike("%AI%")
                | Employee.department.ilike("%AI/ML%")
            )
        elif title_lower in {"hr", "human resources"}:
            statement = statement.where(
                Employee.job_title.ilike(f"%{title_raw}%")
                | Employee.department.ilike("%Human Resources%")
            )
        else:
            statement = statement.where(Employee.job_title.ilike(f"%{title_raw}%"))

    employees = db.scalars(statement).all()
    total_count = len(employees)

    dept_counts: dict[str, int] = {}
    titles: set[str] = set()
    for emp in employees:
        dept_counts[emp.department] = dept_counts.get(emp.department, 0) + 1
        if emp.job_title:
            titles.add(emp.job_title)

    breakdown = [
        DepartmentHeadcount(department=dept, count=count)
        for dept, count in sorted(dept_counts.items(), key=lambda x: x[0])
    ]

    return success(
        db,
        actor,
        "employee_count",
        dept_filter or "all",
        EmployeeCountData(
            total_count=total_count,
            filter_department=dept_filter,
            filter_role=args.role,
            filter_job_title=args.job_title,
            breakdown_by_department=breakdown,
            matched_job_titles=sorted(list(titles))[:10],
        ),
        result_count=total_count,
        action="count",
    )


def get_employee_tasks(
    db: Session,
    actor: Employee,
    args: EmployeeTasksInput,
) -> ToolResult[list[TaskData]]:
    employee_id = args.employee_id or actor.employee_id
    if not employee_scope(db, actor, employee_id):
        return denied(db, actor, "task", employee_id)

    statement = visible_task_statement(db, actor)
    if statement is None:
        return denied(db, actor, "task", employee_id)

    tasks = db.scalars(
        statement.where(Task.assigned_employee_id == employee_id)
        .order_by(Task.task_id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    return success(
        db,
        actor,
        "task",
        employee_id,
        task_output_list(tasks),
        result_count=len(tasks),
        action="list",
    )


def get_employee_performance_metrics(
    db: Session,
    actor: Employee,
    args: PerformanceMetricsInput,
) -> ToolResult[list[PerformanceMetricData]]:
    employee_id = args.employee_id or actor.employee_id
    scope = performance_filter(
        db,
        actor,
        employee_id=employee_id,
        department=args.department,
    )
    if scope is None:
        return denied(db, actor, "performance_metric", employee_id)

    statement = (
        select(PerformanceMetric)
        .join(Employee, Employee.employee_id == PerformanceMetric.employee_id)
        .where(scope)
    )
    if args.quarter:
        statement = statement.where(PerformanceMetric.quarter == args.quarter)
    metrics = db.scalars(
        statement.order_by(
            PerformanceMetric.review_date.desc(),
            PerformanceMetric.record_id,
        )
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    return success(
        db,
        actor,
        "performance_metric",
        employee_id,
        [PerformanceMetricData.model_validate(metric) for metric in metrics],
        result_count=len(metrics),
        action="list",
    )


def get_performance_metrics(
    db: Session,
    actor: Employee,
    args: PerformanceMetricsInput,
) -> ToolResult[list[PerformanceMetricData]]:
    scope = performance_filter(
        db,
        actor,
        employee_id=args.employee_id,
        department=args.department,
    )
    if scope is None:
        return denied(db, actor, "performance_metric", args.employee_id)

    statement = (
        select(PerformanceMetric)
        .join(Employee, Employee.employee_id == PerformanceMetric.employee_id)
        .where(scope)
    )
    if args.quarter:
        statement = statement.where(PerformanceMetric.quarter == args.quarter)
    metrics = db.scalars(
        statement.order_by(
            PerformanceMetric.review_date.desc(),
            PerformanceMetric.record_id,
        )
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    return success(
        db,
        actor,
        "performance_metric",
        args.employee_id,
        [PerformanceMetricData.model_validate(metric) for metric in metrics],
        result_count=len(metrics),
        action="list",
    )


def get_employee_performance_summary(
    db: Session,
    actor: Employee,
    args: EmployeeToolInput,
) -> ToolResult[PerformanceSummaryData]:
    employee_id = args.employee_id or actor.employee_id
    scope = performance_filter(db, actor, employee_id=employee_id)
    if scope is None:
        return denied(db, actor, "performance_metric", employee_id)
    if db.get(Employee, employee_id) is None:
        return not_found(db, actor, "performance_metric", employee_id)

    summary = summarize_metrics(
        db.execute(performance_summary_statement(scope)).all()
    )
    return success(
        db,
        actor,
        "performance_metric",
        employee_id,
        summary,
    )


def get_department_performance_summary(
    db: Session,
    actor: Employee,
    args: PerformanceDepartmentInput,
) -> ToolResult[PerformanceSummaryData]:
    scope = performance_filter(
        db,
        actor,
        department=args.department,
    )
    if scope is None or (
        not has_permission(db, actor, "performance:read:all")
        and not has_permission(db, actor, "performance:read:department")
    ):
        return denied(db, actor, "performance_metric", args.department)

    statement = performance_summary_statement(scope, quarter=args.quarter)
    summary = summarize_metrics(db.execute(statement).all())
    return success(
        db,
        actor,
        "performance_metric",
        args.department,
        summary,
    )
