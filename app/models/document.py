"""Document model."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.document_chunk import DocumentChunk
    from app.models.document_processing_job import DocumentProcessingJob


class Document(Base):
    __tablename__ = "documents"

    __table_args__ = (
        CheckConstraint(
            "access_scope IN ('company', 'department', 'private')",
            name="ck_documents_access_scope",
        ),
        CheckConstraint(
            "processing_status IN "
            "('uploaded', 'processing', 'ready', 'failed')",
            name="ck_documents_processing_status",
        ),
        CheckConstraint(
            "file_size > 0",
            name="ck_documents_file_size_positive",
        ),
        CheckConstraint(
            "duplicate_review_status IN "
            "('none', 'exact_content', 'near_duplicate')",
            name="ck_documents_duplicate_review_status",
        ),
        CheckConstraint(
            "version_number >= 1",
            name="ck_documents_version_number_positive",
        ),
        UniqueConstraint("sha256", name="uq_documents_sha256"),
        UniqueConstraint(
            "document_series_id",
            "version_number",
            name="uq_documents_series_version",
        ),
        Index("ix_documents_department", "department"),
        Index("ix_documents_uploaded_by", "uploaded_by_employee_id"),
        Index("ix_documents_access_scope", "access_scope"),
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_text_fingerprint", "text_fingerprint"),
        Index(
            "uq_documents_current_series",
            "document_series_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        String(512),
        unique=True,
        nullable=False,
    )

    file_extension: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    document_series_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default=lambda: str(uuid4()),
    )

    version_number: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        server_default="1",
    )

    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    text_fingerprint: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )

    duplicate_review_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="none",
        server_default="none",
    )

    duplicate_similarity: Mapped[float | None] = mapped_column(
        nullable=True,
    )

    uploaded_by_employee_id: Mapped[str] = mapped_column(
        ForeignKey(
            "employees.employee_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    department: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    access_scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="private",
    )

    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="uploaded",
    )

    processing_error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    processing_job: Mapped["DocumentProcessingJob | None"] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )
