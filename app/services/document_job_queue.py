"""Database-backed, retryable document processing queue."""

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_event import AuditEvent
from app.models.document import Document
from app.models.document_processing_job import DocumentProcessingJob
from app.services.embedding_service import EmbeddingServiceError

logger = logging.getLogger(__name__)


class JobAlreadyRunningError(Exception):
    """Raised when attempting to enqueue a currently leased job."""


@dataclass(frozen=True)
class ClaimedDocumentJob:
    job_id: int
    document_id: int
    attempts: int
    lease_token: str


def enqueue_document_processing(
    db: Session,
    document_id: int,
) -> DocumentProcessingJob:
    """Create or reset a document job in the caller's transaction."""
    job = db.scalar(
        select(DocumentProcessingJob)
        .where(DocumentProcessingJob.document_id == document_id)
        .with_for_update()
    )
    if job is None:
        job = DocumentProcessingJob(
            document_id=document_id,
            status="queued",
            attempts=0,
            available_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.flush()
        return job

    if job.status == "running":
        raise JobAlreadyRunningError(
            f"Document {document_id} already has a running job."
        )

    job.status = "queued"
    job.attempts = 0
    job.available_at = datetime.now(timezone.utc)
    job.locked_at = None
    job.lease_token = None
    job.last_error = None
    db.flush()
    return job


def claim_next_document_job(
    db: Session,
    *,
    now: datetime | None = None,
) -> ClaimedDocumentJob | None:
    """Claim the next due job; PostgreSQL workers skip locked jobs."""
    current_time = now or datetime.now(timezone.utc)
    expired_before = current_time - timedelta(
        seconds=settings.document_job_lease_seconds
    )

    while True:
        candidate = db.scalar(
            select(DocumentProcessingJob)
            .where(
                or_(
                    (
                        (DocumentProcessingJob.status == "queued")
                        & (DocumentProcessingJob.available_at <= current_time)
                    ),
                    (
                        (DocumentProcessingJob.status == "running")
                        & (DocumentProcessingJob.locked_at.is_not(None))
                        & (DocumentProcessingJob.locked_at <= expired_before)
                    ),
                )
            )
            .order_by(DocumentProcessingJob.available_at, DocumentProcessingJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if candidate is None:
            db.commit()
            return None

        if (
            candidate.status == "running"
            and candidate.attempts >= settings.document_job_max_attempts
        ):
            candidate.status = "failed"
            candidate.locked_at = None
            candidate.lease_token = None
            candidate.last_error = (
                candidate.last_error
                or "Worker lease expired after the maximum number of attempts."
            )
            document = db.get(Document, candidate.document_id)
            if document is not None:
                document.processing_status = "failed"
                document.processing_error_code = "worker_lease_expired"
                db.add(
                    AuditEvent(
                        actor_employee_id=document.uploaded_by_employee_id,
                        action="document_processing_failed",
                        resource_type="document",
                        resource_id=str(document.id),
                        outcome="failure",
                        details={
                            "document_series_id": document.document_series_id,
                            "version_number": document.version_number,
                            "error_code": "worker_lease_expired",
                        },
                    )
                )
            db.commit()
            continue

        candidate.status = "running"
        candidate.attempts += 1
        candidate.locked_at = current_time
        candidate.lease_token = uuid.uuid4().hex
        candidate.last_error = None
        claimed = ClaimedDocumentJob(
            job_id=candidate.id,
            document_id=candidate.document_id,
            attempts=candidate.attempts,
            lease_token=candidate.lease_token,
        )
        db.commit()
        return claimed


def extend_document_job_lease(
    db: Session,
    job_id: int,
    lease_token: str,
) -> bool:
    result = db.execute(
        update(DocumentProcessingJob)
        .where(
            DocumentProcessingJob.id == job_id,
            DocumentProcessingJob.status == "running",
            DocumentProcessingJob.lease_token == lease_token,
        )
        .values(locked_at=datetime.now(timezone.utc))
    )
    db.commit()
    return result.rowcount == 1


def lock_document_job_lease(
    db: Session,
    job_id: int,
    lease_token: str,
) -> bool:
    job = db.scalar(
        select(DocumentProcessingJob)
        .where(
            DocumentProcessingJob.id == job_id,
            DocumentProcessingJob.status == "running",
            DocumentProcessingJob.lease_token == lease_token,
        )
        .with_for_update()
    )
    return job is not None


def mark_document_job_completed(
    db: Session,
    job_id: int,
    lease_token: str,
) -> None:
    job = db.get(DocumentProcessingJob, job_id)
    if job is None or job.lease_token != lease_token:
        return
    job.status = "completed"
    job.locked_at = None
    job.lease_token = None
    job.last_error = None
    db.commit()


def mark_document_job_failed(
    db: Session,
    job_id: int,
    attempts: int,
    lease_token: str,
    error: Exception,
    *,
    now: datetime | None = None,
) -> None:
    job = db.get(DocumentProcessingJob, job_id)
    if job is None or job.lease_token != lease_token:
        return

    current_time = now or datetime.now(timezone.utc)
    job.locked_at = None
    job.lease_token = None
    job.last_error = _safe_error_code(error)
    document = db.get(Document, job.document_id)

    if attempts >= settings.document_job_max_attempts:
        job.status = "failed"
        if document is not None:
            document.processing_status = "failed"
            document.processing_error_code = _safe_error_code(error)
    else:
        job.status = "queued"
        delay_seconds = min(
            settings.document_job_retry_base_seconds * (2 ** (attempts - 1)),
            3600,
        )
        job.available_at = current_time + timedelta(seconds=delay_seconds)
        if document is not None:
            document.processing_status = "uploaded"
            document.processing_error_code = _safe_error_code(error)
    db.commit()


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, EmbeddingServiceError):
        return "embedding_service_unavailable"
    if isinstance(error, FileNotFoundError):
        return "source_file_missing"
    if isinstance(error, ValueError):
        return "document_processing_invalid"
    return "document_processing_failed"


def process_one_document_job() -> bool:
    """Run one job; return whether a job was claimed."""
    from app.core.database import SessionLocal
    from app.services.document_processing_service import process_document

    with SessionLocal() as db:
        claimed = claim_next_document_job(db)
    if claimed is None:
        return False

    heartbeat_stop = threading.Event()
    heartbeat_interval = max(
        1.0,
        min(60.0, settings.document_job_lease_seconds / 3),
    )

    def maintain_lease() -> None:
        while not heartbeat_stop.wait(heartbeat_interval):
            try:
                with SessionLocal() as db:
                    renewed = extend_document_job_lease(
                        db,
                        claimed.job_id,
                        claimed.lease_token,
                    )
            except Exception:
                logger.exception(
                    "Failed to renew lease for document job %s.",
                    claimed.job_id,
                )
                continue
            if not renewed:
                logger.warning(
                    "Document job %s no longer has this worker's lease.",
                    claimed.job_id,
                )
                return

    heartbeat = threading.Thread(
        target=maintain_lease,
        name=f"document-job-lease-{claimed.job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        process_document(
            claimed.document_id,
            job_id=claimed.job_id,
            lease_token=claimed.lease_token,
        )
    except Exception as exc:
        logger.exception(
            "Document job %s failed on attempt %s.",
            claimed.job_id,
            claimed.attempts,
        )
        with SessionLocal() as db:
            mark_document_job_failed(
                db,
                claimed.job_id,
                claimed.attempts,
                claimed.lease_token,
                exc,
            )
    else:
        with SessionLocal() as db:
            mark_document_job_completed(
                db,
                claimed.job_id,
                claimed.lease_token,
            )
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=heartbeat_interval + 1)
    return True
