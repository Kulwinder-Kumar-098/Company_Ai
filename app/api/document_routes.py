
import logging
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_any_permission, require_permission
from app.core.config import settings
from app.models.audit_event import AuditEvent
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.employee import Employee
from app.schemas.document import (
    DocumentResponse,
    DocumentProcessingStatusResponse,
    DocumentVersionHistoryItem,
    DocumentVersionUploadResponse,
)
from app.services.authorization_service import has_permission
from app.services.document_access_service import can_access_document
from app.services.document_indexing_service import rebuild_document_faiss_index
from app.services.document_job_queue import (
    JobAlreadyRunningError,
    enqueue_document_processing,
)
from app.services.document_storage_service import (
    DocumentValidationError,
    save_uploaded_document,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    file: UploadFile = File(...),
    access_scope: Literal[
        "private",
        "department",
        "company",
    ] = Form("private"),
    current_user: Employee = Depends(
        require_permission("documents:create")
    ),
    db: Session = Depends(get_db),
):
    # 1. Validate publishing permissions.
    if (
        access_scope == "company"
        and not has_permission(
            db,
            current_user,
            "documents:publish",
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot publish company-wide documents.",
        )

    # 2. Save and validate the uploaded file.
    try:
        saved_file = save_uploaded_document(file)

    except DocumentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    storage_path: Path = saved_file["storage_path"]
    try:
        duplicate_id = db.scalar(
            select(Document.id)
            .where(Document.sha256 == saved_file["sha256"])
            .limit(1)
        )
    except SQLAlchemyError as exc:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check document duplicates.",
        ) from exc
    if duplicate_id is not None:
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document has already been uploaded.",
        )

    # 3. Determine document ownership and scope.
    department = (
        current_user.department
        if access_scope == "department"
        else None
    )

    # 4. Create the database record.
    document = Document(
        original_filename=saved_file["original_filename"],
        storage_key=saved_file["storage_key"],
        file_extension=saved_file["file_extension"],
        mime_type=saved_file["mime_type"],
        file_size=saved_file["file_size"],
        sha256=saved_file["sha256"],
        uploaded_by_employee_id=current_user.employee_id,
        department=department,
        access_scope=access_scope,
        processing_status="uploaded",
    )

    audit_event = AuditEvent(
        actor_employee_id=current_user.employee_id,
        action="document_uploaded",
        resource_type="document",
        resource_id=saved_file["storage_key"],
        outcome="success",
        details={
            "filename": saved_file["original_filename"],
            "access_scope": access_scope,
        },
    )

    try:
        db.add(document)
        db.flush()
        enqueue_document_processing(db, document.id)
        db.add(audit_event)

        db.commit()
        db.refresh(document)

    except IntegrityError as exc:
        db.rollback()

        # Do not leave an orphaned file if the database fails.
        storage_path.unlink(missing_ok=True)

        if db.scalar(
            select(Document.id)
            .where(Document.sha256 == saved_file["sha256"])
            .limit(1)
        ) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This document has already been uploaded.",
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document metadata.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document metadata.",
        ) from exc

    return document


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document_version(
    document_id: int,
    file: UploadFile = File(...),
    current_user: Employee = Depends(
        require_permission("documents:version")
    ),
    db: Session = Depends(get_db),
) -> DocumentVersionUploadResponse:
    current_document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
    )
    if current_document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not can_access_document(current_document, current_user):
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_document.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A new version can only be created from the current version.",
        )
    if current_document.processing_status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A new version can only be created from a ready document.",
        )
    pending_version_id = db.scalar(
        select(Document.id)
        .where(
            Document.document_series_id == current_document.document_series_id,
            Document.is_current.is_(False),
            Document.processing_status.in_(("uploaded", "processing")),
        )
        .limit(1)
    )
    if pending_version_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document version is already being processed.",
        )
    previous_version_number = current_document.version_number
    latest_version_number = db.scalar(
        select(func.max(Document.version_number)).where(
            Document.document_series_id == current_document.document_series_id
        )
    )
    version_number = (latest_version_number or previous_version_number) + 1

    try:
        saved_file = save_uploaded_document(file)
    except DocumentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    storage_path: Path = saved_file["storage_path"]
    try:
        duplicate_id = db.scalar(
            select(Document.id)
            .where(Document.sha256 == saved_file["sha256"])
            .limit(1)
        )
    except SQLAlchemyError as exc:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check document version duplicates.",
        ) from exc
    if duplicate_id is not None:
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document has already been uploaded.",
        )

    new_document = Document(
        original_filename=saved_file["original_filename"],
        storage_key=saved_file["storage_key"],
        file_extension=saved_file["file_extension"],
        mime_type=saved_file["mime_type"],
        file_size=saved_file["file_size"],
        sha256=saved_file["sha256"],
        document_series_id=current_document.document_series_id,
        version_number=version_number,
        is_current=False,
        uploaded_by_employee_id=current_user.employee_id,
        department=current_document.department,
        access_scope=current_document.access_scope,
        processing_status="uploaded",
    )
    try:
        db.add(new_document)
        db.flush()
        enqueue_document_processing(db, new_document.id)
        db.add(
            AuditEvent(
                actor_employee_id=current_user.employee_id,
                action="document_version_uploaded",
                resource_type="document",
                resource_id=str(new_document.id),
                outcome="success",
                details={
                    "document_series_id": current_document.document_series_id,
                    "version_number": version_number,
                    "previous_version_number": previous_version_number,
                },
            )
        )
        db.commit()
        db.refresh(new_document)
    except IntegrityError as exc:
        db.rollback()
        storage_path.unlink(missing_ok=True)

        duplicate_exists = db.scalar(
            select(Document.id)
            .where(Document.sha256 == saved_file["sha256"])
            .limit(1)
        )
        if duplicate_exists is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This document has already been uploaded.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent update prevented this document version.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document version.",
        ) from exc

    return DocumentVersionUploadResponse(
        id=new_document.id,
        version_number=new_document.version_number,
        processing_status=new_document.processing_status,
    )


@router.get(
    "/{document_id}/versions",
    response_model=list[DocumentVersionHistoryItem],
)
def list_document_versions(
    document_id: int,
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
) -> list[DocumentVersionHistoryItem]:
    document = db.get(Document, document_id)
    if document is None or not can_access_document(document, current_user):
        raise HTTPException(status_code=404, detail="Document not found")

    access_conditions = [
        Document.access_scope == "company",
        and_(
            Document.access_scope == "private",
            Document.uploaded_by_employee_id == current_user.employee_id,
        ),
    ]
    if current_user.department is not None:
        access_conditions.append(
            and_(
                Document.access_scope == "department",
                Document.department == current_user.department,
            )
        )
    versions = db.scalars(
        select(Document)
        .where(
            Document.document_series_id == document.document_series_id,
            or_(*access_conditions),
        )
        .order_by(Document.version_number.desc())
    ).all()
    return [
        DocumentVersionHistoryItem.model_validate(version)
        for version in versions
    ]


@router.get(
    "/{document_id}/status",
    response_model=DocumentProcessingStatusResponse,
)
def get_document_processing_status(
    document_id: int,
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
) -> DocumentProcessingStatusResponse:
    document = db.get(Document, document_id)
    if document is None or not can_access_document(document, current_user):
        raise HTTPException(status_code=404, detail="Document not found")

    active_version = db.scalar(
        select(Document.version_number).where(
            Document.document_series_id == document.document_series_id,
            Document.is_current.is_(True),
            Document.processing_status == "ready",
        )
    )
    chunk_count = db.scalar(
        select(func.count(DocumentChunk.id)).where(
            DocumentChunk.document_id == document.id
        )
    )
    return DocumentProcessingStatusResponse(
        document_id=document.id,
        processing_status=document.processing_status,
        active_version=active_version,
        chunk_count=chunk_count or 0,
        processed_at=document.processed_at,
        error=(
            "Document processing failed."
            if document.processing_status == "failed"
            else None
        ),
    )


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
):
    access_conditions = [
        Document.access_scope == "company",
        and_(
            Document.access_scope == "private",
            Document.uploaded_by_employee_id == current_user.employee_id,
        ),
    ]
    if current_user.department is not None:
        access_conditions.append(
            and_(
                Document.access_scope == "department",
                Document.department == current_user.department,
            )
        )
    return db.scalars(
        select(Document)
        .where(
            Document.is_current.is_(True),
            or_(*access_conditions),
        )
        .order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    if not can_access_document(document, current_user):
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    storage_root = Path(
        settings.document_storage_dir
    ).resolve()

    file_path = (
        storage_root / document.storage_key
    ).resolve()

    # Ensure the resolved file stays inside the storage directory.
    if file_path.parent != storage_root:
        raise HTTPException(
            status_code=404,
            detail="Document file not found",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Document file not found",
        )

    return FileResponse(
        path=file_path,
        media_type=document.mime_type,
        filename=document.original_filename,
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    document_id: int,
    current_user: Employee = Depends(
        require_any_permission("documents:create", "documents:delete")
    ),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if (
        document.uploaded_by_employee_id != current_user.employee_id
        and not has_permission(db, current_user, "documents:delete")
    ):
        raise HTTPException(status_code=404, detail="Document not found")
    if document.processing_status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document processing is already in progress.",
        )

    document.processing_status = "uploaded"
    try:
        enqueue_document_processing(db, document.id)
        db.add(
            AuditEvent(
                actor_employee_id=current_user.employee_id,
                action="document_processing_retried",
                resource_type="document",
                resource_id=str(document.id),
                outcome="success",
                details={
                    "document_series_id": document.document_series_id,
                    "version_number": document.version_number,
                    "reason": "user_requested",
                },
            )
        )
        db.commit()
        db.refresh(document)
    except JobAlreadyRunningError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document processing is already in progress.",
        ) from exc
    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(
    document_id: int,
    current_user: Employee = Depends(
        require_any_permission("documents:create", "documents:delete")
    ),
    db: Session = Depends(get_db),
) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if (
        document.uploaded_by_employee_id != current_user.employee_id
        and not has_permission(db, current_user, "documents:delete")
    ):
        raise HTTPException(status_code=404, detail="Document not found")
    if document.is_current:
        has_previous_versions = db.scalar(
            select(Document.id)
            .where(
                Document.document_series_id == document.document_series_id,
                Document.id != document.id,
            )
            .limit(1)
        )
        if has_previous_versions is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The current version cannot be deleted while its version "
                    "history exists."
                ),
            )

    storage_root = Path(settings.document_storage_dir).resolve()
    file_path = (storage_root / document.storage_key).resolve()
    if file_path.parent != storage_root:
        raise HTTPException(status_code=404, detail="Document not found")

    tombstone_path = file_path.with_name(
        f".{file_path.name}.{uuid.uuid4().hex}.deleting"
    )
    try:
        if file_path.exists():
            os.replace(file_path, tombstone_path)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prepare document file for deletion.",
        ) from exc

    db.delete(document)
    db.add(
        AuditEvent(
            actor_employee_id=current_user.employee_id,
            action="document_deleted",
            resource_type="document",
            resource_id=str(document_id),
            outcome="success",
            details={"filename": document.original_filename},
        )
    )
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        if tombstone_path.exists():
            try:
                os.replace(tombstone_path, file_path)
            except OSError:
                logger.exception(
                    "Failed to restore document file %s after database deletion failed.",
                    document_id,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document metadata.",
        ) from exc

    if tombstone_path.exists():
        try:
            tombstone_path.unlink()
        except OSError:
            logger.exception(
                "Document %s was removed from the database, but its file "
                "could not be deleted.",
                document_id,
            )

    try:
        rebuild_document_faiss_index(db)
    except Exception:
        logger.exception(
            "Document %s was deleted, but the FAISS index rebuild failed.",
            document_id,
        )