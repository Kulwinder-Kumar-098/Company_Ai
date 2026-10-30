from app.core.security import UserContext, has_permission

def test_employee_cannot_read_performance():
    user = UserContext(employee_id="E001", role="employee", department="engineering")
    assert not has_permission(user, "performance:read")

def test_hr_can_read_performance():
    user = UserContext(employee_id="E002", role="hr", department="people")
    assert has_permission(user, "performance:read")
