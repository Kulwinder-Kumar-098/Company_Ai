"""add document version tracking

Revision ID: 3d8e6a14bc52
Revises: 8c41d6a20f73
Create Date: 2026-10-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3d8e6a14bc52"
down_revision: Union[str, Sequence[str], None] = "8c41d6a20f73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("document_series_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("version_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("is_current", sa.Boolean(), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE documents "
            "SET document_series_id = 'legacy-' || CAST(id AS VARCHAR(20)), "
            "version_number = 1, is_current = TRUE"
        )
    )

    op.alter_column(
        "documents",
        "document_series_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.alter_column(
        "documents",
        "version_number",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1",
    )
    op.alter_column(
        "documents",
        "is_current",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    )
    op.create_check_constraint(
        "ck_documents_version_number_positive",
        "documents",
        "version_number >= 1",
    )
    op.create_unique_constraint(
        "uq_documents_series_version",
        "documents",
        ["document_series_id", "version_number"],
    )
    op.create_index(
        "uq_documents_current_series",
        "documents",
        ["document_series_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_current_series", table_name="documents")
    op.drop_constraint(
        "uq_documents_series_version",
        "documents",
        type_="unique",
    )
    op.drop_constraint(
        "ck_documents_version_number_positive",
        "documents",
        type_="check",
    )
    op.drop_column("documents", "is_current")
    op.drop_column("documents", "version_number")
    op.drop_column("documents", "document_series_id")
