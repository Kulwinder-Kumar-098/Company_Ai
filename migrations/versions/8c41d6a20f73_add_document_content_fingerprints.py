"""add document content fingerprints

Revision ID: 8c41d6a20f73
Revises: 71c4a8f09d26
Create Date: 2026-10-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c41d6a20f73"
down_revision: Union[str, Sequence[str], None] = "71c4a8f09d26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("text_fingerprint", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "duplicate_review_status",
            sa.String(length=20),
            server_default="none",
            nullable=False,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("duplicate_similarity", sa.Float(), nullable=True),
    )
    op.create_check_constraint(
        "ck_documents_duplicate_review_status",
        "documents",
        "duplicate_review_status IN ('none', 'exact_content', 'near_duplicate')",
    )
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.create_unique_constraint(
        "uq_documents_sha256",
        "documents",
        ["sha256"],
    )
    op.create_index(
        "ix_documents_content_hash",
        "documents",
        ["content_hash"],
    )
    op.create_index(
        "ix_documents_text_fingerprint",
        "documents",
        ["text_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_text_fingerprint", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_constraint("uq_documents_sha256", "documents", type_="unique")
    op.create_index(
        "ix_documents_sha256",
        "documents",
        ["sha256"],
        unique=False,
    )
    op.drop_constraint(
        "ck_documents_duplicate_review_status",
        "documents",
        type_="check",
    )
    op.drop_column("documents", "duplicate_similarity")
    op.drop_column("documents", "duplicate_review_status")
    op.drop_column("documents", "text_fingerprint")
    op.drop_column("documents", "content_hash")
