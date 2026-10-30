"""Secure RAG query endpoints."""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.employee import Employee
from app.observability.audit import record_audit_event
from app.schemas.rag import RAGQueryRequest, RAGQueryResponse
from app.services.document_retrieval_service import VectorIndexUnavailableError
from app.services.embedding_service import EmbeddingServiceError
from app.services.rag_generation_service import (
    RAGEmptyGenerationError,
    RAGGenerationTimeoutError,
    RAGGenerationUnavailableError,
)
from app.services.rag_query_service import answer_rag_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["RAG"])


@router.post(
    "/query",
    response_model=RAGQueryResponse,
)
def query_documents(
    request: RAGQueryRequest,
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
) -> RAGQueryResponse:
    conversation_id = request.conversation_id or str(uuid4())
    try:
        _record_query_event(
            db,
            employee_id=current_user.employee_id,
            action="rag_query_initiated",
            conversation_id=conversation_id,
            outcome="started",
            details={"top_k": request.top_k},
        )
        result = answer_rag_query(
            db,
            current_user,
            request.question,
            conversation_id=conversation_id,
            top_k=request.top_k,
        )
        _record_query_event(
            db,
            employee_id=current_user.employee_id,
            action="rag_query_completed",
            conversation_id=conversation_id,
            outcome="success",
            details={"source_count": len(result["sources"])},
        )
    except VectorIndexUnavailableError as exc:
        _record_query_failure(db, current_user, conversation_id, "index_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document search is temporarily unavailable.",
        ) from exc
    except EmbeddingServiceError as exc:
        _record_query_failure(
            db,
            current_user,
            conversation_id,
            "embedding_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document search is temporarily unavailable.",
        ) from exc
    except RAGGenerationTimeoutError as exc:
        _record_query_failure(db, current_user, conversation_id, "generation_timeout")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Answer generation timed out.",
        ) from exc
    except RAGGenerationUnavailableError as exc:
        _record_query_failure(
            db,
            current_user,
            conversation_id,
            "generation_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Answer generation is temporarily unavailable.",
        ) from exc
    except RAGEmptyGenerationError as exc:
        _record_query_failure(
            db,
            current_user,
            conversation_id,
            "empty_generation",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation returned no answer.",
        ) from exc
    except ValueError as exc:
        _record_query_failure(db, current_user, conversation_id, "invalid_vector")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document search returned an invalid result.",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Database failure during RAG query.")
        _record_query_failure(db, current_user, conversation_id, "database_error")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document search is temporarily unavailable.",
        ) from exc

    return RAGQueryResponse.model_validate(result)


def _record_query_event(
    db: Session,
    *,
    employee_id: str,
    action: str,
    conversation_id: str,
    outcome: str,
    details: dict[str, int | str],
) -> None:
    record_audit_event(
        db,
        actor_employee_id=employee_id,
        action=action,
        resource_type="rag_query",
        resource_id=conversation_id,
        outcome=outcome,
        details=details,
    )


def _record_query_failure(
    db: Session,
    employee: Employee,
    conversation_id: str,
    error_code: str,
) -> None:
    try:
        _record_query_event(
            db,
            employee_id=employee.employee_id,
            action="rag_query_failed",
            conversation_id=conversation_id,
            outcome="failure",
            details={"error_code": error_code},
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record RAG query failure audit event.")
