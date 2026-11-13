import pytest
from sqlalchemy.engine import make_url

from scripts.import_employee_data import (
    _normalize_database_url,
    _validate_database_urls,
)


def test_employee_import_rejects_same_source_and_target_database():
    database = make_url(
        "postgresql+psycopg://user:secret@db.example/company_ai"
    )

    with pytest.raises(ValueError, match="different databases"):
        _validate_database_urls(database, database)


def test_employee_import_accepts_separate_postgres_staging_database():
    staging = make_url(
        "postgresql+psycopg://user:secret@db.example/company_ai_staging"
    )
    target = make_url(
        "postgresql+psycopg://user:secret@db.example/company_ai"
    )

    _validate_database_urls(staging, target)


def test_employee_import_rejects_non_postgres_databases():
    staging = make_url("sqlite:///staging.db")
    target = make_url(
        "postgresql+psycopg://user:secret@db.example/company_ai"
    )

    with pytest.raises(ValueError, match="must be PostgreSQL"):
        _validate_database_urls(staging, target)


def test_employee_import_normalizes_remote_postgres_ssl():
    url = _normalize_database_url(
        "postgresql://user:secret@db.example/company_ai"
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
