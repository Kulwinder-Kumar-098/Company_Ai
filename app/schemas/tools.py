"""Typed contracts for structured-data tools."""

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel, Generic[T]):
    status: Literal["ok", "not_found", "denied"]
    data: T | None = None
    error: str | None = None


class ToolExecuteRequest(ToolInput):
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object]


class ToolExecuteResponse(BaseModel):
    tool_name: str
    result: dict[str, object]


class EmployeeToolInput(ToolInput):
    employee_id: str | None = Field(default=None, min_length=1, max_length=50)


class EmployeeTasksInput(ToolInput):
    employee_id: str | None = Field(default=None, min_length=1, max_length=50)
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class EmployeeSearchInput(ToolInput):
    query: str | None = Field(default=None, max_length=150)
    department: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class TaskSearchInput(ToolInput):
    query: str | None = Field(default=None, max_length=250)
    project_id: str | None = Field(default=None, max_length=30)
    status: str | None = Field(default=None, max_length=30)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class TaskToolInput(ToolInput):
    task_id: str = Field(min_length=1, max_length=30)


class OverdueTasksInput(ToolInput):
    project_id: str | None = Field(default=None, max_length=30)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ProjectToolInput(ToolInput):
    project_id: str = Field(min_length=1, max_length=30)


class ProjectTaskInput(ProjectToolInput):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ProjectTeamInput(ProjectToolInput):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PerformanceMetricsInput(ToolInput):
    employee_id: str | None = Field(default=None, min_length=1, max_length=50)
    department: str | None = Field(default=None, max_length=100)
    quarter: str | None = Field(default=None, max_length=10)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PerformanceDepartmentInput(ToolInput):
    department: str = Field(min_length=1, max_length=100)
    quarter: str | None = Field(default=None, max_length=10)


class EmployeeData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    full_name: str
    email: str
    department: str
    job_title: str | None
    is_active: bool


class ProjectData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    project_name: str
    department: str
    project_manager_id: str | None
    start_date: date | None
    planned_end_date: date | None
    actual_end_date: date | None
    status: str
    priority: str
    budgeted_hours: int
    completed_hours: int
    completion_percentage: float
    risk_level: str


class ProjectStatusData(BaseModel):
    project_id: str
    status: str
    completion_percentage: float
    planned_end_date: date | None
    actual_end_date: date | None
    risk_level: str


class TeamMemberData(BaseModel):
    employee_id: str
    full_name: str
    job_title: str | None


class TaskData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    project_id: str
    task_name: str
    assigned_employee_id: str | None
    created_date: date | None
    due_date: date | None
    completed_date: date | None
    status: str
    priority: str
    estimated_hours: int
    actual_hours: int | None


class PerformanceMetricData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: str
    employee_id: str
    quarter: str
    review_date: date
    department: str
    project_id: str | None
    goals_completed: int
    goals_total: int
    task_completion_rate: float
    quality_score: float
    collaboration_score: float
    attendance_rate: float
    manager_review_status: str
    performance_category: str


class PerformanceSummaryData(BaseModel):
    metric_count: int
    employee_count: int = 0
    goals_completed: int
    goals_total: int
    average_task_completion_rate: float | None
    average_quality_score: float | None
    average_collaboration_score: float | None
    average_attendance_rate: float | None
