from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.employee import Employee
from app.observability.audit import record_audit_event
from app.services.knowledge_service import answer_question

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QuestionResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=QuestionResponse)
def ask_question(
    request: QuestionRequest,
    current_user: Employee = Depends(require_permission("policy:read")),
    db: Session = Depends(get_db),
):
    try:
        result = answer_question(request.question)
        record_audit_event(
            db,
            actor_employee_id=current_user.employee_id,
            action="read",
            resource_type="policy",
            resource_id=None,
            outcome="success",
        )
        return QuestionResponse(
            answer=result["answer"],
            sources=result["sources"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to process your question.",
        ) from exc