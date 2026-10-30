import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Employee, Project

CSV_PATH = Path("data/raw/projects.csv")


def parse_date(value):
    value = value.strip()
    return date.fromisoformat(value) if value else None


def import_projects():
    with CSV_PATH.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    with SessionLocal() as db:
        employee_ids = set(
            db.scalars(select(Employee.employee_id)).all()
        )

        created = 0
        skipped = 0

        for row in rows:
            project_id = row["project_id"].strip()

            if db.get(Project, project_id):
                print(f"Skipped {project_id}: already exists")
                skipped += 1
                continue

            manager_id = row["project_manager_id"].strip() or None

            if manager_id and manager_id not in employee_ids:
                print(
                    f"Skipped {project_id}: manager {manager_id} "
                    "does not exist"
                )
                skipped += 1
                continue

            project = Project(
                project_id=project_id,
                project_name=row["project_name"].strip(),
                department=row["department"].strip(),
                project_manager_id=manager_id,
                start_date=parse_date(row["start_date"]),
                planned_end_date=parse_date(row["planned_end_date"]),
                actual_end_date=parse_date(row["actual_end_date"]),
                status=row["status"].strip(),
                priority=row["priority"].strip(),
                budgeted_hours=int(row["budgeted_hours"]),
                completed_hours=int(row["completed_hours"]),
                completion_percentage=float(
                    row["completion_percentage"]
                ),
                risk_level=row["risk_level"].strip(),
            )

            db.add(project)
            created += 1

        db.commit()
        print(f"Import complete: {created} created, {skipped} skipped.")


if __name__ == "__main__":
    import_projects()