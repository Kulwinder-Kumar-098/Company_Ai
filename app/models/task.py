from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(String(250), nullable=False)
    assigned_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.employee_id", ondelete="SET NULL"),
        nullable=True,
    )

    created_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(String(30), nullable=False)
    estimated_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocker_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    project = relationship("Project")
    assigned_employee = relationship("Employee")