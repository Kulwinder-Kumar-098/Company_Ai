"""Background processing for uploaded documents."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.ingestion.chunking import chunk_pages
from app.ingestion.parsers import extract_document_pages
from app.models.audit_event import AuditEvent
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_processing_job import DocumentProcessingJob
from app.services.document_chunk_service import save_document_chunks
from app.services.document_duplicate_service import find_duplicate_review_match
from app.services.document_job_queue import lock_document_job_lease
from app.services.document_indexing_service import (
    generate_missing_chunk_embeddings,
    index_document_chunks,
)
from app.services.document_faiss_service import DocumentFaissIndex
from app.services.embedding_service import EmbeddingServiceError

logger = logging.getLogger(__name__)


class DocumentJobLeaseLostError(RuntimeError):
    """Raised when a stale worker no longer owns the document job."""


def process_document(
    document_id: int,
    *,
    job_id: int | None = None,
    lease_token: str | None = None,
) -> None:
    """Process a document using a dedicated database session."""
    with SessionLocal() as db:
        _process_document(db, document_id, job_id, lease_token)


def _process_document(
    db: Session,
    document_id: int,
    job_id: int | None,
    lease_token: str | None,
) -> None:
    document = db.get(Document, document_id)
    if document is None:
        logger.warning(
            "Skipping document processing because document %s does not exist.",
            document_id,
        )
        return

    try:
        job = (
            db.get(DocumentProcessingJob, job_id)
            if job_id is not None
            else None
        )
        attempt = job.attempts if job is not None else 1
        document.processing_status = "processing"
        document.processing_error_code = None
        db.add(
            AuditEvent(
                actor_employee_id=document.uploaded_by_employee_id,
                action="document_processing_started",
                resource_type="document",
                resource_id=str(document.id),
                outcome="started",
                details={
                    "document_series_id": document.document_series_id,
                    "version_number": document.version_number,
                    "attempt": attempt,
                },
            )
        )
        if attempt > 1:
            db.add(
                AuditEvent(
                    actor_employee_id=document.uploaded_by_employee_id,
                    action="document_processing_retried",
                    resource_type="document",
                    resource_id=str(document.id),
                    outcome="success",
                    details={
                        "document_series_id": document.document_series_id,
                        "version_number": document.version_number,
                        "attempt": attempt,
                    },
                )
            )
        db.commit()

        storage_root = Path(settings.document_storage_dir).resolve()
        file_path = (storage_root / document.storage_key).resolve()
        if file_path.parent != storage_root:
            raise ValueError("Document storage key is outside the storage directory.")

        pages = extract_document_pages(
            file_path,
            document.file_extension,
        )
        chunks = chunk_pages(
            pages,
            chunk_size=settings.document_chunk_size,
            overlap=settings.document_chunk_overlap,
        )
        if not chunks:
            raise ValueError("The uploaded document contains no extractable text.")

        replaced_chunk_ids = db.scalars(
            select(DocumentChunk.id).where(
                DocumentChunk.document_id == document.id
            )
        ).all()
        extracted_text = "\n".join(page["text"] for page in pages)
        save_document_chunks(
            db=db,
            document_id=document.id,
            chunks=chunks,
        )
        generate_missing_chunk_embeddings(db)
        find_duplicate_review_match(db, document, extracted_text)
        _lock_owned_job_lease(db, job_id, lease_token)
        db.commit()

        index_document_chunks(
            db,
            document.id,
            replace_chunk_ids=replaced_chunk_ids,
        )
        _activate_document_version(db, document.id, job_id, lease_token)
    except Exception as exc:
        db.rollback()
        document = db.get(Document, document_id)
        owns_job = _owns_job_lease(db, job_id, lease_token)
        if document is not None and owns_job:
            document.processing_status = "failed"
            document.processing_error_code = _safe_error_code(exc)
            db.add(
                AuditEvent(
                    action="document_processing_failed",
                    resource_type="document",
                    resource_id=str(document.id),
                    outcome="failure",
                    details={
                        "document_series_id": document.document_series_id,
                        "version_number": document.version_number,
                        "error_code": document.processing_error_code,
                    },
                )
            )
            db.commit()
        elif not owns_job:
            logger.warning(
                "Stopping stale processing attempt for document %s.",
                document_id,
            )
        logger.exception("Document processing failed for document %s.", document_id)
        raise


def _activate_document_version(
    db: Session,
    document_id: int,
    job_id: int | None,
    lease_token: str | None,
) -> None:
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
    )
    if document is None:
        raise RuntimeError("Document disappeared during processing.")

    superseded_chunk_ids: list[int] = []
    latest_version_number = db.scalar(
        select(func.max(Document.version_number)).where(
            Document.document_series_id == document.document_series_id
        )
    )
    will_activate = (
        not document.is_current
        and document.version_number == latest_version_number
    )
    if will_activate:
        current_document = db.scalar(
            select(Document)
            .where(
                Document.document_series_id == document.document_series_id,
                Document.is_current.is_(True),
            )
            .with_for_update()
        )
        if current_document is not None:
            if current_document.processing_status != "ready":
                raise RuntimeError(
                    "The active document version changed during processing."
                )
            superseded_chunk_ids = db.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_id == current_document.id
                )
            ).all()
            current_document.is_current = False
            db.flush()
        document.is_current = True
    if will_activate or (
        document.is_current and document.processed_at is None
    ):
        db.add(
            AuditEvent(
                actor_employee_id=document.uploaded_by_employee_id,
                action="document_version_activated",
                resource_type="document",
                resource_id=str(document.id),
                outcome="success",
                details={
                    "document_series_id": document.document_series_id,
                    "version_number": document.version_number,
                },
            )
        )

    document.processing_status = "ready"
    document.processing_error_code = None
    document.processed_at = datetime.now(timezone.utc)
    _lock_owned_job_lease(db, job_id, lease_token)
    db.add(
        AuditEvent(
            actor_employee_id=document.uploaded_by_employee_id,
            action="document_processing_completed",
            resource_type="document",
            resource_id=str(document.id),
            outcome="success",
            details={
                "document_series_id": document.document_series_id,
                "version_number": document.version_number,
            },
        )
    )
    db.commit()
    if superseded_chunk_ids:
        try:
            DocumentFaissIndex().remove(
                superseded_chunk_ids,
                model_name=settings.embedding_model,
            )
        except Exception:
            logger.exception(
                "Activated document version %s, but failed to remove "
                "superseded vectors from FAISS.",
                document.id,
            )


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, EmbeddingServiceError):
        return "embedding_service_unavailable"
    if isinstance(error, FileNotFoundError):
        return "source_file_missing"
    if isinstance(error, ValueError):
        return "document_processing_invalid"
    return "document_processing_failed"


def _lock_owned_job_lease(
    db: Session,
    job_id: int | None,
    lease_token: str | None,
) -> None:
    if job_id is not None and lease_token is not None:
        if not lock_document_job_lease(db, job_id, lease_token):
            raise DocumentJobLeaseLostError(
                f"Processing lease for document job {job_id} was lost."
            )


def _owns_job_lease(
    db: Session,
    job_id: int | None,
    lease_token: str | None,
) -> bool:
    return (
        job_id is None
        or lease_token is None
        or lock_document_job_lease(db, job_id, lease_token)
    )
