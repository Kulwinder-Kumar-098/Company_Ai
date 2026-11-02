"""Chat API routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.orchestrator import run_agent_query
from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.employee import Employee
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=ChatResponse)
def ask_question(
    request: ChatRequest,
    current_user: Employee = Depends(
        require_permission("chat:use")
    ),
    db: Session = Depends(get_db),
) -> ChatResponse:
    try:
        result = run_agent_query(
            db,
            current_user,
            request.message,
        )
    except Exception:
        logger.error("Company AI graph execution failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Company AI is temporarily unavailable.",
        ) from None
    return ChatResponse(
        answer=result["answer"],
        role=current_user.role.name,
        sources=result["sources"],
        trace_id=result.get("trace_id"),
    )
