import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Employee, Project, PerformanceMetric

CSV_PATH = Path("data/raw/performance_metrics.csv")


def import_performance():
    with CSV_PATH.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    with SessionLocal() as db:
        employee_ids = set(db.scalars(select(Employee.employee_id)).all())
        project_ids = set(db.scalars(select(Project.project_id)).all())

        created = skipped = 0
        imported_record_ids: set[str] = set()

        for row in rows:
            record_id = row["record_id"].strip()

            if (
                record_id in imported_record_ids
                or db.get(PerformanceMetric, record_id)
            ):
                skipped += 1
                continue

            employee_id = row["employee_id"].strip()
            project_id = row["project_id"].strip() or None

            if employee_id not in employee_ids:
                print(f"Skipped {record_id}: employee not found")
                skipped += 1
                continue

            if project_id and project_id not in project_ids:
                print(
                    f"Warning {record_id}: project {project_id} not found; "
                    "importing without a project"
                )
                project_id = None

            metric = PerformanceMetric(
                record_id=record_id,
                employee_id=employee_id,
                quarter=row["quarter"].strip(),
                review_date=date.fromisoformat(row["review_date"].strip()),
                department=row["department"].strip(),
                project_id=project_id,
                goals_completed=int(row["goals_completed"]),
                goals_total=int(row["goals_total"]),
                task_completion_rate=float(row["task_completion_rate"]),
                quality_score=float(row["quality_score"]),
                collaboration_score=float(row["collaboration_score"]),
                attendance_rate=float(row["attendance_rate"]),
                manager_review_status=row["manager_review_status"].strip(),
                performance_category=row["performance_category"].strip(),
            )

            db.add(metric)
            imported_record_ids.add(record_id)
            created += 1

        db.commit()
        print(f"Import complete: {created} created, {skipped} skipped.")


if __name__ == "__main__":
    import_performance()