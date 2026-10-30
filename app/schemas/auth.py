from pydantic import BaseModel, Field, field_validator

class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()

        if len(normalized) < 5:
            raise ValueError("Invalid email address")

        return normalized

class EmployeeRegistrationRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    full_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=255)
    department: str = Field(min_length=1, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    password: str = Field(min_length=12, max_length=128)
    role_id: int = Field(gt=0)

    @field_validator("employee_id", "full_name", "department")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()

        if "@" not in value:
            raise ValueError("Invalid email address")

        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
