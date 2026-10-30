from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    record_id: Mapped[str] = mapped_column(String(100), primary_key=True)

    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.employee_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    quarter: Mapped[str] = mapped_column(String(10), nullable=False)
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
    )

    goals_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    goals_total: Mapped[int] = mapped_column(Integer, nullable=False)
    task_completion_rate: Mapped[float] = mapped_column(Float, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    collaboration_score: Mapped[float] = mapped_column(Float, nullable=False)
    attendance_rate: Mapped[float] = mapped_column(Float, nullable=False)

    manager_review_status: Mapped[str] = mapped_column(String(30), nullable=False)
    performance_category: Mapped[str] = mapped_column(String(100), nullable=False)

    employee = relationship("Employee")
    project = relationship("Project")