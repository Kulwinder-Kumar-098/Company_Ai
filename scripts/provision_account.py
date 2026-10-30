

"""Provision credentials for an existing employee."""
from getpass import getpass

from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.employee_credential import EmployeeCredential

password_hasher = PasswordHash.recommended()


def main() -> None:
    employee_id = input("Employee ID (e.g. EMP001): ").strip()

    if not employee_id:
        print("Employee ID is required.")
        return

    with SessionLocal() as db:
        employee = db.scalar(
            select(Employee).where(Employee.employee_id == employee_id)
        )

        if employee is None:
            print("Employee not found.")
            return

        if not employee.is_active:
            print("This employee account is inactive.")
            return

        existing = db.get(EmployeeCredential, employee_id)
        if existing is not None:
            print("Credentials already exist. No changes made.")
            return

        print(f"Employee: {employee.full_name}")
        print(f"Email: {employee.email}")
        print("Enter a strong password (minimum 12 characters).")

        password = getpass("Password: ")
        confirmation = getpass("Confirm password: ")

        if len(password) < 12:
            print("Password must contain at least 12 characters.")
            return

        if password != confirmation:
            print("Passwords do not match.")
            return

        credential = EmployeeCredential(
            employee_id=employee.employee_id,
            password_hash=password_hasher.hash(password),
        )

        try:
            db.add(credential)
            db.commit()
            print("Account provisioned successfully.")
            print(f"Login email: {employee.email}")
        except Exception:
            db.rollback()
            print("Provisioning failed. No changes were committed.")
            raise


if __name__ == "__main__":
    main()
