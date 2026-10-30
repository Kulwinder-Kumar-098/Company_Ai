"""add processing and embedding metadata

Revision ID: d8f41a6c2b90
Revises: 3d8e6a14bc52
Create Date: 2026-10-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f41a6c2b90"
down_revision: Union[str, Sequence[str], None] = "3d8e6a14bc52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("processing_error_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE document_chunks SET embedding = NULL "
            "WHERE embedding IS NOT NULL"
        )
    )
    op.create_check_constraint(
        "ck_document_chunks_embedding_metadata",
        "document_chunks",
        "(embedding IS NULL AND embedding_model IS NULL "
        "AND embedding_dimension IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_model IS NOT NULL "
        "AND embedding_dimension > 0)",
    )
    op.execute(
        sa.text(
            "UPDATE documents SET processing_status = 'uploaded', "
            "processed_at = NULL, processing_error_code = NULL"
        )
    )

    op.execute(
        sa.text(
            "INSERT INTO document_processing_jobs "
            "(document_id, status, attempts, available_at, created_at, updated_at) "
            "SELECT id, 'queued', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP FROM documents WHERE TRUE "
            "ON CONFLICT (document_id) DO UPDATE SET "
            "status = 'queued', attempts = 0, available_at = CURRENT_TIMESTAMP, "
            "locked_at = NULL, lease_token = NULL, last_error = NULL, "
            "updated_at = CURRENT_TIMESTAMP"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_document_chunks_embedding_metadata",
        "document_chunks",
        type_="check",
    )
    op.drop_column("document_chunks", "embedding_dimension")
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "processing_error_code")
