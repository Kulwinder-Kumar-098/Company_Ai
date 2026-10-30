"""Chat API routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.employee import Employee
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.document_rag_service import answer_document_question

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/ask", response_model=ChatResponse)
def ask_question(
    request: ChatRequest,
    current_user: Employee = Depends(
        require_permission("documents:read")
    ),
    db: Session = Depends(get_db),
) -> ChatResponse:
    result = answer_document_question(
        db,
        current_user,
        request.message,
    )
    return ChatResponse(
        answer=result["answer"],
        role=current_user.role.name,
        sources=result["sources"],
    )
