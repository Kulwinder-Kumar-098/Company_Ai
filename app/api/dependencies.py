from fastapi import Header, HTTPException
from app.core.security import UserContext

# DEMO ONLY: client-supplied headers are not safe for production identity.
DEMO_USERS = {
    "E001": {"role": "employee", "department": "engineering"},
    "E002": {"role": "hr", "department": "people"},
    "E003": {"role": "project_manager", "department": "engineering"},
    "E004": {"role": "cto", "department": "engineering"},
}

def get_current_user(
    employee_id: str | None = Header(default=None, alias="X-Employee-ID"),
    role: str | None = Header(default=None, alias="X-Role"),
) -> UserContext:
    if not employee_id:
        raise HTTPException(status_code=401, detail="Missing X-Employee-ID header")
    record = DEMO_USERS.get(employee_id)
    if not record:
        raise HTTPException(status_code=401, detail="Unknown demo employee")
    if role != record["role"]:
        raise HTTPException(status_code=403, detail="Role does not match employee record")
    return UserContext(employee_id=employee_id, role=record["role"], department=record["department"])
