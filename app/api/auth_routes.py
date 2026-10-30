
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
)
from app.services.rate_limit_service import (
    get_retry_after_seconds,
    record_failed_login,
    reset_email_failures,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.permissions import require_permission
from app.models.employee import Employee
from app.models.employee_credential import EmployeeCredential
from app.models.role import Role
from app.models.audit_event import AuditEvent
from app.schemas.auth import EmployeeRegistrationRequest
from app.schemas.resources import EmployeeResponse
from app.services.auth_service import hash_password
from app.services.authorization_service import has_permission


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_address = (
        request.client.host
        if request.client
        else "unknown"
    )

    email = credentials.email

    # 1. Check rate limits before performing password verification.
    retry_after = get_retry_after_seconds(
        db,
        ip_address,
        email,
    )

    if retry_after > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    # 2. Authenticate the employee.
    employee = authenticate_user(
        db=db,
        email=email,
        password=credentials.password,
    )

    # 3. Record failed attempts.
    if employee is None:
        record_failed_login(
            db,
            ip_address,
            email,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Successful login clears this IP + email counter.
    reset_email_failures(
        db,
        ip_address,
        email,
    )

    # 5. Generate the access token.
    token = create_access_token(employee.employee_id)

    return TokenResponse(access_token=token)


@router.post(
    "/register",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_employee(
    employee_data: EmployeeRegistrationRequest,
    current_user: Employee = Depends(
        require_permission("employees:create")
    ),
    db: Session = Depends(get_db),
):
    # 1. Check whether employee ID already exists.
    existing_employee = db.get(
        Employee,
        employee_data.employee_id,
    )

    if existing_employee:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee ID already exists",
        )

    # 2. Check whether email already exists.
    existing_email = db.scalar(
        select(Employee).where(
            func.lower(Employee.email)
            == employee_data.email.lower()
        )
    )

    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # 3. Validate the requested role.
    role = db.get(Role, employee_data.role_id)

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role ID",
        )

    can_assign_privileged_role = has_permission(
        db,
        current_user,
        "employees:assign:privileged",
    )
    if not can_assign_privileged_role and role.name != "employee":
        db.add(
            AuditEvent(
                actor_employee_id=current_user.employee_id,
                action="employee_registration_denied",
                resource_type="employee",
                resource_id=employee_data.employee_id,
                outcome="denied",
                details={"role_id": employee_data.role_id},
            )
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can assign privileged roles",
        )

    # 4. Create employee and credentials atomically.
    employee = Employee(
        employee_id=employee_data.employee_id,
        full_name=employee_data.full_name,
        email=employee_data.email,
        department=employee_data.department,
        job_title=employee_data.job_title,
        role_id=employee_data.role_id,
        is_active=True,
    )

    credential = EmployeeCredential(
        employee_id=employee_data.employee_id,
        password_hash=hash_password(employee_data.password),
    )

    try:
        db.add(employee)
        db.add(credential)

        # 5. Record registration in the same transaction.
        db.add(
            AuditEvent(
                actor_employee_id=current_user.employee_id,
                action="employee_registered",
                resource_type="employee",
                resource_id=employee_data.employee_id,
                outcome="success",
                details={"role_id": employee_data.role_id},
            )
        )

        db.commit()
        db.refresh(employee)

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee ID or email already exists",
        )

    return employee