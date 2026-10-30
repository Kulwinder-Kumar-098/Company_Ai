
"""Application configuration."""

from typing import Literal
from pathlib import Path
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
    document_storage_dir: Path = Path("data/documents")
    document_faiss_index_path: Path = Path(
        "data/indexes/documents.faiss"
    )
    embedding_model: str = "nomic-embed-text:latest"
    embedding_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    embedding_batch_size: int = Field(default=64, gt=0, le=512)
    rag_max_top_k: int = Field(default=10, gt=0, le=50)
    rag_relevance_threshold: float = Field(default=0.25, ge=-1.0, le=1.0)
    rag_context_max_characters: int = Field(default=12000, gt=0, le=100000)
    rag_generation_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
    )
    rag_generation_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
    )
    document_chunk_size: int = Field(default=1000, gt=0, le=10000)
    document_chunk_overlap: int = Field(default=150, ge=0, le=9999)
    llm_model: str = "llama3.2:3b"
    document_worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    document_job_lease_seconds: int = Field(default=3600, gt=0)
    document_job_max_attempts: int = Field(default=5, gt=0, le=20)
    document_job_retry_base_seconds: int = Field(default=30, gt=0)
    document_near_duplicate_threshold: float = Field(
        default=0.98,
        ge=0.5,
        le=1.0,
    )

    max_document_size_mb: int = Field(
        default=20,
        gt=0,
        le=100,
    )

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.document_chunk_overlap >= self.document_chunk_size:
            raise ValueError(
                "DOCUMENT_CHUNK_OVERLAP must be smaller than "
                "DOCUMENT_CHUNK_SIZE"
            )
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
