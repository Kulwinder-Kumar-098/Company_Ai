import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Employee, Project, Task

CSV_PATH = Path("data/raw/tasks.csv")


def parse_date(value):
    value = value.strip()
    return date.fromisoformat(value) if value else None


def import_tasks():
    with CSV_PATH.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    with SessionLocal() as db:
        project_ids = set(db.scalars(select(Project.project_id)).all())
        employee_ids = set(db.scalars(select(Employee.employee_id)).all())

        created = skipped = 0

        for row in rows:
            task_id = row["task_id"].strip()

            if db.get(Task, task_id):
                print(f"Skipped {task_id}: already exists")
                skipped += 1
                continue

            project_id = row["project_id"].strip()
            employee_id = row["assigned_employee_id"].strip() or None

            if project_id not in project_ids:
                print(f"Skipped {task_id}: project {project_id} not found")
                skipped += 1
                continue

            if employee_id and employee_id not in employee_ids:
                print(f"Skipped {task_id}: employee {employee_id} not found")
                skipped += 1
                continue

            task = Task(
                task_id=task_id,
                project_id=project_id,
                task_name=row["task_name"].strip(),
                assigned_employee_id=employee_id,
                created_date=parse_date(row["created_date"]),
                due_date=parse_date(row["due_date"]),
                completed_date=parse_date(row["completed_date"]),
                status=row["status"].strip(),
                priority=row["priority"].strip(),
                estimated_hours=int(row["estimated_hours"]),
                actual_hours=(
                    int(row["actual_hours"])
                    if row["actual_hours"].strip()
                    else None
                ),
                blocker_description=row["blocker_description"].strip() or None,
            )

            db.add(task)
            created += 1

        db.commit()
        print(f"Import complete: {created} created, {skipped} skipped.")


if __name__ == "__main__":
    import_tasks()