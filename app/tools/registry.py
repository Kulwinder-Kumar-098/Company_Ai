"""Framework-independent registry for authorized structured-data tools."""

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.employee import Employee
from app.schemas.tools import (
    EmployeeData,
    EmployeeSearchInput,
    EmployeeTasksInput,
    EmployeeToolInput,
    OverdueTasksInput,
    PerformanceDepartmentInput,
    PerformanceMetricData,
    PerformanceMetricsInput,
    PerformanceSummaryData,
    ProjectData,
    ProjectStatusData,
    ProjectTaskInput,
    ProjectTeamInput,
    ProjectToolInput,
    TaskData,
    TaskSearchInput,
    TaskToolInput,
    TeamMemberData,
    ToolResult,
)
from app.tools.hr_tools import (
    get_department_performance_summary,
    get_employee_details,
    get_employee_performance_metrics,
    get_employee_performance_summary,
    get_employee_tasks,
    get_performance_metrics,
    search_employees,
)
from app.tools.project_tools import (
    get_project_details,
    get_project_status,
    get_project_tasks,
    get_project_team,
)
from app.tools.technical_tools import (
    get_overdue_tasks,
    get_task_details,
    search_tasks,
)


ToolHandler = Callable[..., BaseModel]


class ToolNotFoundError(LookupError):
    """Raised when a requested tool is not registered."""


class ToolArgumentsError(ValueError):
    """Raised when arguments do not match a registered tool's schema."""


class ToolOutputError(RuntimeError):
    """Raised when a handler returns data outside its declared output schema."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: ToolHandler


TOOLS: dict[str, ToolDefinition] = {
    definition.name: definition
    for definition in (
        ToolDefinition(
            "get_employee_details",
            "Read an employee record allowed by the actor's employee permissions. "
            "Omit employee_id to read the authenticated actor's own record.",
            EmployeeToolInput,
            ToolResult[EmployeeData],
            get_employee_details,
        ),
        ToolDefinition(
            "search_employees",
            "Search only employee records visible to the authenticated actor.",
            EmployeeSearchInput,
            ToolResult[list[EmployeeData]],
            search_employees,
        ),
        ToolDefinition(
            "get_employee_tasks",
            "List tasks assigned to an employee, subject to employee and task scope.",
            EmployeeTasksInput,
            ToolResult[list[TaskData]],
            get_employee_tasks,
        ),
        ToolDefinition(
            "get_employee_performance_metrics",
            "Read personal performance metrics for an employee. "
            "Use this tool when a user asks for their own performance metrics or reviews, "
            "or another specific employee's metrics. Omit employee_id to select the authenticated user.",
            PerformanceMetricsInput,
            ToolResult[list[PerformanceMetricData]],
            get_employee_performance_metrics,
        ),
        ToolDefinition(
            "get_performance_metrics",
            "Query or list performance metrics across broader criteria (such as filtered by department or multiple employees). "
            "For an individual's or one's own performance metrics, prefer get_employee_performance_metrics instead.",
            PerformanceMetricsInput,
            ToolResult[list[PerformanceMetricData]],
            get_performance_metrics,
        ),
        ToolDefinition(
            "get_employee_performance_summary",
            "Aggregate one employee's performance metrics only when the actor "
            "has explicit permission for that employee's scope.",
            EmployeeToolInput,
            ToolResult[PerformanceSummaryData],
            get_employee_performance_summary,
        ),
        ToolDefinition(
            "get_department_performance_summary",
            "Aggregate metrics for the actor's permitted department, or any "
            "department with performance:read:all.",
            PerformanceDepartmentInput,
            ToolResult[PerformanceSummaryData],
            get_department_performance_summary,
        ),
        ToolDefinition(
            "get_project_details",
            "Read project details after verifying project visibility.",
            ProjectToolInput,
            ToolResult[ProjectData],
            get_project_details,
        ),
        ToolDefinition(
            "get_project_status",
            "Read status and completion fields for a visible project.",
            ProjectToolInput,
            ToolResult[ProjectStatusData],
            get_project_status,
        ),
        ToolDefinition(
            "get_project_tasks",
            "List tasks in a visible project; managers see all tasks only in "
            "projects they manage, while other assigned users see their own.",
            ProjectTaskInput,
            ToolResult[list[TaskData]],
            get_project_tasks,
        ),
        ToolDefinition(
            "get_project_team",
            "List only the manager and task assignees attached to a visible project.",
            ProjectTeamInput,
            ToolResult[list[TeamMemberData]],
            get_project_team,
        ),
        ToolDefinition(
            "get_task_details",
            "Read one task only if its project and task assignment are visible.",
            TaskToolInput,
            ToolResult[TaskData],
            get_task_details,
        ),
        ToolDefinition(
            "search_tasks",
            "Search tasks after applying the actor's project and task visibility "
            "filters in the database.",
            TaskSearchInput,
            ToolResult[list[TaskData]],
            search_tasks,
        ),
        ToolDefinition(
            "get_overdue_tasks",
            "List visible, incomplete tasks whose due date is before today.",
            OverdueTasksInput,
            ToolResult[list[TaskData]],
            get_overdue_tasks,
        ),
    )
}


def execute_tool(
    name: str,
    arguments: dict[str, object] | BaseModel,
    *,
    db: Session,
    actor: Employee,
) -> BaseModel:
    """Validate arguments and execute using trusted backend context."""
    try:
        definition = TOOLS[name]
    except KeyError as exc:
        raise ToolNotFoundError(name) from exc
    raw_arguments = (
        arguments.model_dump()
        if isinstance(arguments, BaseModel)
        else arguments
    )
    try:
        validated = definition.input_schema.model_validate(raw_arguments)
    except ValidationError as exc:
        raise ToolArgumentsError from exc
    output = definition.handler(db, actor, validated)
    try:
        return definition.output_schema.model_validate(output)
    except ValidationError as exc:
        raise ToolOutputError from exc
