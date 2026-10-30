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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
