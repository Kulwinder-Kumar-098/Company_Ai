
"""Application configuration."""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Company AI"
    database_url: str

    app_env: Literal[
        "development",
        "test",
        "staging",
        "production",
    ] = "development"

    jwt_secret_key: str | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"

    access_token_expire_minutes: int = Field(
        default=30,
        gt=0,
        le=1440,
    )

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.app_env == "production":
            secret = (self.jwt_secret_key or "").strip()

            if len(secret) < 32:
                raise ValueError(
                    "Production requires JWT_SECRET_KEY "
                    "with at least 32 characters"
                )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
