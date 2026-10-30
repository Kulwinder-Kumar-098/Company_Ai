"""add chunk embeddings

Revision ID: 9a1f0c2d7e31
Revises: b675aa6b4710
Create Date: 2026-10-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a1f0c2d7e31"
down_revision: Union[str, Sequence[str], None] = "b675aa6b4710"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("embedding", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "embedding")
