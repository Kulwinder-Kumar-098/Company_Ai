import csv
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Employee, Role

CSV_PATH = "data/raw/employees.csv"

ROLE_MAP = {
    "ceo": "admin",
    "cto": "manager",
    "project_manager": "manager",
    "hr": "hr",
    "employee": "employee",
    "system_admin": "admin",
}


def import_employees():
    with open(CSV_PATH, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    with SessionLocal() as db:
        roles = {
            role.name: role
            for role in db.scalars(select(Role)).all()
        }

        created = 0
        skipped = 0
        imported_employee_ids: set[str] = set()
        imported_emails: set[str] = set()

        for row in rows:
            employee_id = row["employee_id"].strip()
            email = row["email"].strip().lower()
            role_code = row["role_code"].strip().lower()
            role_name = ROLE_MAP.get(role_code)

            if not role_name or role_name not in roles:
                print(f"Skipped {employee_id}: unknown role {role_code}")
                skipped += 1
                continue

            existing = db.get(Employee, employee_id)
            if existing:
                print(f"Skipped {employee_id}: already exists")
                skipped += 1
                continue

            if employee_id in imported_employee_ids:
                print(f"Skipped {employee_id}: duplicate employee ID in import")
                skipped += 1
                continue

            if email in imported_emails:
                print(f"Skipped {employee_id}: duplicate email in import")
                skipped += 1
                continue

            duplicate_email = db.scalar(
                select(Employee).where(Employee.email == email)
            )
            if duplicate_email:
                print(f"Skipped {employee_id}: email already exists")
                skipped += 1
                continue

            employee = Employee(
                employee_id=employee_id,
                full_name=row["full_name"].strip(),
                email=email,
                department=row["department"].strip(),
                job_title=row["job_title"].strip(),
                role_id=roles[role_name].id,
                is_active=row["employment_status"].strip().lower() == "active",
            )

            db.add(employee)
            imported_employee_ids.add(employee_id)
            imported_emails.add(email)
            created += 1

        db.commit()
        print(f"Import complete: {created} created, {skipped} skipped.")


if __name__ == "__main__":
    import_employees()