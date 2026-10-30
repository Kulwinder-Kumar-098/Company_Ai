from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.knowledge_service import answer_question

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QuestionResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=QuestionResponse)
def ask_question(request: QuestionRequest):
    try:
        return answer_question(request.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to process your question.",
        )