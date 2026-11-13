
"""Application configuration."""

from typing import Literal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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
    ollama_host: str = "http://localhost:11434"
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
    planner_provider: Literal["ollama", "groq"] = "ollama"
    planner_model: str | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_planner_model: str = "gemini-3.8-flash"
    planner_fast_path_enabled: bool = True
    smalltalk_enabled: bool = True
    smalltalk_llm_fallback_enabled: bool = True
    smalltalk_llm_timeout_seconds: int = Field(
        default=20,
        ge=1,
        le=120,
    )
    smalltalk_max_query_characters: int = Field(default=200, ge=1)
    smalltalk_max_answer_characters: int = Field(default=600, ge=1)
    planner_cache_enabled: bool = True
    planner_cache_ttl_seconds: int = Field(
        default=21600,
        ge=1,
        le=604800,
    )
    planner_cache_max_entries: int = Field(
        default=512,
        ge=1,
        le=10000,
    )
    planner_rate_limit_cooldown_enabled: bool = True
    planner_rate_limit_cooldown_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
    )
    planner_token_usage_logging_enabled: bool = True
    chat_rag_top_k: int = Field(default=4, ge=1)
    ollama_keep_alive: str = "30m"
    ollama_num_ctx: int = Field(default=4096, ge=1024, le=32768)
    rag_max_answer_tokens: int = Field(default=600, ge=64, le=4096)
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

    @field_validator("ollama_host")
    @classmethod
    def normalize_ollama_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("OLLAMA_HOST must be a non-empty HTTP(S) URL.")
        if "://" not in normalized:
            normalized = f"http://{normalized}"

        parsed = urlsplit(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OLLAMA_HOST must be a valid HTTP(S) URL.")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError(
                "OLLAMA_HOST must be a valid HTTP(S) URL."
            ) from exc

        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path.rstrip("/"),
                "",
                "",
            )
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

    @property
    def effective_planner_model(self) -> str:
        return self.planner_model or self.llm_model

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
