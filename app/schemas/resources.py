"""Response schemas for employee and project resources."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    full_name: str
    email: str
    department: str
    job_title: str | None
    is_active: bool


class ProjectResponse(BaseModel):
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


class TaskResponse(BaseModel):
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
    blocker_description: str | None
