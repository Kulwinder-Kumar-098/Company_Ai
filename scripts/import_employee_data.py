"""Safely import employees and credential hashes from a staged PostgreSQL backup."""

import argparse
import os
import sys

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert


def _validate_database_urls(staging_url: URL, target_url: URL) -> None:
    if (
        staging_url.get_backend_name() != "postgresql"
        or target_url.get_backend_name() != "postgresql"
    ):
        raise ValueError("Both staging and target databases must be PostgreSQL.")
    if (
        staging_url.host == target_url.host
        and staging_url.port == target_url.port
        and staging_url.database == target_url.database
    ):
        raise ValueError("Staging and target must be different databases.")


def _normalize_database_url(value: str) -> URL:
    url = make_url(value.strip())
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if (
        url.host
        and url.host.casefold() not in {"localhost", "127.0.0.1", "::1"}
        and "sslmode" not in url.query
    ):
        url = url.update_query_dict({"sslmode": "require"})
    return url


def _reflect_table(connection, name: str) -> Table:
    metadata = MetaData()
    try:
        return Table(name, metadata, autoload_with=connection, schema="public")
    except SQLAlchemyError as exc:
        raise ValueError(
            f"The staged backup is missing required table '{name}'."
        ) from exc


def _require_columns(table: Table, required: set[str]) -> None:
    missing = required - set(table.columns.keys())
    if missing:
        raise ValueError(
            f"Table '{table.name}' is missing required columns."
        )


def _role_id_mapping(source_connection, target_connection) -> dict[int, int]:
    source_roles = _reflect_table(source_connection, "roles")
    target_roles = _reflect_table(target_connection, "roles")
    _require_columns(source_roles, {"id", "name"})
    _require_columns(target_roles, {"id", "name"})
    target_by_name = dict(
        target_connection.execute(
            select(target_roles.c.name, target_roles.c.id)
        )
    )
    return {
        source_id: target_by_name[name]
        for source_id, name in source_connection.execute(
            select(source_roles.c.id, source_roles.c.name)
        )
        if name in target_by_name
    }


def import_employee_data(
    staging_url: URL,
    target_url: URL,
    *,
    apply_changes: bool,
) -> dict[str, int | str]:
    _validate_database_urls(staging_url, target_url)
    source_engine = create_engine(staging_url, pool_pre_ping=True)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with source_engine.connect() as source_connection:
            with target_engine.begin() as target_connection:
                source_employees = _reflect_table(
                    source_connection,
                    "employees",
                )
                target_employees = _reflect_table(
                    target_connection,
                    "employees",
                )
                source_credentials = _reflect_table(
                    source_connection,
                    "employee_credentials",
                )
                target_credentials = _reflect_table(
                    target_connection,
                    "employee_credentials",
                )
                _require_columns(
                    source_employees,
                    {
                        "employee_id",
                        "full_name",
                        "email",
                        "department",
                        "job_title",
                        "role_id",
                        "is_active",
                    },
                )
                _require_columns(
                    target_employees,
                    {
                        "employee_id",
                        "full_name",
                        "email",
                        "department",
                        "job_title",
                        "role_id",
                        "is_active",
                    },
                )
                _require_columns(
                    source_credentials,
                    {"employee_id", "password_hash"},
                )
                _require_columns(
                    target_credentials,
                    {"employee_id", "password_hash"},
                )

                role_ids = _role_id_mapping(
                    source_connection,
                    target_connection,
                )
                existing_employees = dict(
                    target_connection.execute(
                        select(
                            target_employees.c.employee_id,
                            target_employees.c.email,
                        )
                    )
                )
                email_to_employee_id = {
                    email.casefold(): employee_id
                    for employee_id, email in existing_employees.items()
                }
                source_rows = source_connection.execute(
                    select(source_employees)
                ).mappings()

                employee_inserted = 0
                employee_skipped = 0
                safe_employee_ids: set[str] = set()
                for source_row in source_rows:
                    employee_id = source_row["employee_id"]
                    email = source_row["email"]
                    existing_email = email_to_employee_id.get(email.casefold())
                    existing_email_for_id = existing_employees.get(employee_id)

                    if existing_email_for_id is not None:
                        if existing_email_for_id.casefold() == email.casefold():
                            safe_employee_ids.add(employee_id)
                        employee_skipped += 1
                        continue
                    if existing_email is not None:
                        employee_skipped += 1
                        continue
                    target_role_id = role_ids.get(source_row["role_id"])
                    if target_role_id is None:
                        employee_skipped += 1
                        continue

                    values = {
                        column: source_row[column]
                        for column in target_employees.columns.keys()
                        if column in source_employees.columns
                    }
                    values["role_id"] = target_role_id
                    if not apply_changes:
                        employee_inserted += 1
                        safe_employee_ids.add(employee_id)
                        existing_employees[employee_id] = email
                        email_to_employee_id[email.casefold()] = employee_id
                        continue

                    inserted_id = target_connection.execute(
                        insert(target_employees)
                        .values(**values)
                        .on_conflict_do_nothing()
                        .returning(target_employees.c.employee_id)
                    ).scalar_one_or_none()
                    if inserted_id is not None:
                        employee_inserted += 1
                        safe_employee_ids.add(employee_id)
                        existing_employees[employee_id] = email
                        email_to_employee_id[email.casefold()] = employee_id
                    else:
                        actual_email = target_connection.scalar(
                            select(target_employees.c.email).where(
                                target_employees.c.employee_id == employee_id
                            )
                        )
                        if (
                            actual_email is not None
                            and actual_email.casefold() == email.casefold()
                        ):
                            safe_employee_ids.add(employee_id)
                        employee_skipped += 1

                source_credential_rows = source_connection.execute(
                    select(source_credentials)
                ).mappings()
                existing_credentials = set(
                    target_connection.scalars(
                        select(target_credentials.c.employee_id)
                    )
                )
                credential_inserted = 0
                credential_skipped = 0
                for source_row in source_credential_rows:
                    employee_id = source_row["employee_id"]
                    if (
                        employee_id not in safe_employee_ids
                        or employee_id in existing_credentials
                    ):
                        credential_skipped += 1
                        continue

                    values = {
                        column: source_row[column]
                        for column in target_credentials.columns.keys()
                        if column in source_credentials.columns
                    }
                    if not apply_changes:
                        credential_inserted += 1
                        existing_credentials.add(employee_id)
                        continue

                    inserted_id = target_connection.execute(
                        insert(target_credentials)
                        .values(**values)
                        .on_conflict_do_nothing()
                        .returning(target_credentials.c.employee_id)
                    ).scalar_one_or_none()
                    if inserted_id is not None:
                        credential_inserted += 1
                    else:
                        credential_skipped += 1

        return {
            "mode": "applied" if apply_changes else "dry-run",
            "employees_inserted": employee_inserted,
            "employees_skipped": employee_skipped,
            "credentials_inserted": credential_inserted,
            "credentials_skipped": credential_skipped,
        }
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import employees and missing credential rows from an isolated "
            "PostgreSQL staging database. Existing target rows are never updated."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit inserts. Without this flag, only report insert/skip counts.",
    )
    arguments = parser.parse_args()

    staging_url_value = os.environ.get("STAGING_DATABASE_URL")
    target_url_value = os.environ.get("TARGET_DATABASE_URL")
    if not staging_url_value or not target_url_value:
        parser.error(
            "Set STAGING_DATABASE_URL and TARGET_DATABASE_URL in the "
            "process environment. The importer will not use DATABASE_URL."
        )

    try:
        result = import_employee_data(
            _normalize_database_url(staging_url_value),
            _normalize_database_url(target_url_value),
            apply_changes=arguments.apply,
        )
    except (SQLAlchemyError, ValueError):
        print(
            "Import failed; no partial transaction was committed.",
            file=sys.stderr,
        )
        return 1

    print(
        " ".join(
            f"{name}={value}"
            for name, value in result.items()
        )
    )
    if not arguments.apply:
        print("Dry run only. Re-run with --apply after reviewing the counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
