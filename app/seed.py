from sqlalchemy import select
from app.core.database import SessionLocal
from app.models import Role, Permission

ROLES = [
    ("admin", "Full system administration"),
    ("hr", "Human resources access"),
    ("manager", "Team and project management"),
    ("employee", "Standard employee access"),
]

PERMISSIONS = [
    ("policy:read", "Read company policies"),
    ("employees:read", "View employee records"),
    ("employees:read:all", "View employee records across departments"),
    ("employees:read:self", "View own employee record"),
    ("employees:create", "Create employee records"),
    ("employees:update", "Update employee records"),
    ("roles:manage", "Manage roles and permissions"),
    ("projects:read", "View permitted projects"),
    ("projects:read:all", "View all projects"),
    ("projects:read:assigned", "View managed or assigned projects"),
    ("tasks:read:assigned", "View tasks in managed or assigned projects"),
    ("projects:manage", "Manage projects"),
    ("reports:read", "View permitted reports"),
]


def seed_data():
    with SessionLocal() as db:
        roles = {}
        permissions = {}

        for name, description in ROLES:
            role = db.scalar(select(Role).where(Role.name == name))
            if role is None:
                role = Role(name=name, description=description)
                db.add(role)
            roles[name] = role

        for code, description in PERMISSIONS:
            permission = db.scalar(
                select(Permission).where(Permission.code == code)
            )
            if permission is None:
                permission = Permission(
                    code=code, description=description
                )
                db.add(permission)
            permissions[code] = permission

        db.flush()

        role_permissions = {
            "admin": list(permissions),
            "hr": [
                "policy:read",
                "employees:read",
                "employees:read:all",
                "employees:create",
                "employees:update",
                "reports:read",
            ],
            "manager": [
                "policy:read",
                "employees:read",
                "projects:read:assigned",
                "tasks:read:assigned",
                "projects:read",
                "projects:manage",
                "reports:read",
            ],
            "employee": [
                "policy:read",
                "employees:read:self",
                "projects:read:assigned",
                "tasks:read:assigned",
                "projects:read",
            ],
        }

        for role_name, codes in role_permissions.items():
            for code in codes:
                permission = permissions[code]
                if permission not in roles[role_name].permissions:
                    roles[role_name].permissions.append(permission)

        db.commit()
        print("Roles and permissions seeded successfully.")


if __name__ == "__main__":
    seed_data()