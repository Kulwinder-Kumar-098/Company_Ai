"""add durable document processing jobs

Revision ID: 71c4a8f09d26
Revises: 9a1f0c2d7e31
Create Date: 2026-10-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "71c4a8f09d26"
down_revision: Union[str, Sequence[str], None] = "9a1f0c2d7e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_processing_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_document_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_document_processing_jobs_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            name="uq_document_processing_job_document",
        ),
    )
    op.create_index(
        "ix_document_processing_jobs_status_available",
        "document_processing_jobs",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_processing_jobs_status_available",
        table_name="document_processing_jobs",
    )
    op.drop_table("document_processing_jobs")
