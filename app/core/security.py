from dataclasses import dataclass

@dataclass(frozen=True)
class UserContext:
    employee_id: str
    role: str
    department: str

ROLE_PERMISSIONS = {
    "employee": {"policy:read", "profile:read:self"},
    "hr": {"policy:read", "employee:read:department", "performance:read"},
    "project_manager": {"policy:read", "project:read:assigned", "task:read:assigned"},
    "cto": {"policy:read", "engineering_metrics:read", "project:read:all"},
}

def has_permission(user: UserContext, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, set())
