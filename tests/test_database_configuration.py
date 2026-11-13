from sqlalchemy import create_engine

from app.core.config import Settings


def _settings(database_url: str, **kwargs) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        **kwargs,
    )


def test_database_url_loads_from_environment(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://user:password@railway.example:5432/company",
    )

    configured = Settings(_env_file=None)

    assert configured.database_url == (
        "postgresql+psycopg://user:password@railway.example:5432/company"
        "?sslmode=require"
    )


def test_database_url_loads_from_local_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://user:password@localhost/company\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_file)

    assert configured.database_url == (
        "postgresql+psycopg://user:password@localhost/company"
    )


def test_postgres_url_normalization_preserves_ssl_query_parameters():
    configured = _settings(
        "postgres://user:p%40ss@railway.example:5432/company"
        "?sslmode=require&application_name=company_ai"
    )

    assert configured.database_url == (
        "postgresql+psycopg://user:p%40ss@railway.example:5432/company"
        "?sslmode=require&application_name=company_ai"
    )


def test_postgresql_url_uses_psycopg_and_adds_ssl_for_external_host():
    configured = _settings(
        "postgresql://user:password@railway.example:5432/company"
        "?application_name=company_ai"
    )

    assert configured.database_url == (
        "postgresql+psycopg://user:password@railway.example:5432/company"
        "?application_name=company_ai&sslmode=require"
    )
    engine = create_engine(configured.database_url)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert engine.url.query == {
            "application_name": "company_ai",
            "sslmode": "require",
        }
    finally:
        engine.dispose()


def test_existing_ssl_mode_and_local_database_url_are_preserved():
    remote = _settings(
        "postgresql+psycopg://user:password@railway.example/company"
        "?sslmode=verify-full"
    )
    local = _settings(
        "postgresql://user:password@127.0.0.1:5432/company"
    )

    assert remote.database_url.endswith("?sslmode=verify-full")
    assert local.database_url == (
        "postgresql+psycopg://user:password@127.0.0.1:5432/company"
    )
