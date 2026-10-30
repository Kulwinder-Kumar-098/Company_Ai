"""Orchestrate authorized retrieval, bounded context, and RAG generation."""

from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.employee import Employee
from app.services.document_retrieval_service import retrieve_document_chunks
from app.services.rag_context_service import build_rag_context
from app.services.rag_generation_service import generate_rag_answer

INSUFFICIENT_CONTEXT_ANSWER = (
    "The available documents do not provide enough information to answer "
    "that question."
)


def answer_rag_query(
    db: Session,
    employee: Employee,
    question: str,
    *,
    conversation_id: str | None = None,
    top_k: int = 5,
) -> dict:
    if not question.strip():
        raise ValueError("Question cannot be empty.")
    if top_k < 1 or top_k > settings.rag_max_top_k:
        raise ValueError("top_k is outside the configured range.")

    conversation = conversation_id or str(uuid4())
    chunks = retrieve_document_chunks(
        db,
        employee,
        question,
        top_k=top_k,
    )
    context, sources = build_rag_context(chunks)
    if not sources:
        return {
            "answer": INSUFFICIENT_CONTEXT_ANSWER,
            "conversation_id": conversation,
            "sources": [],
        }

    answer = generate_rag_answer(question, context)
    return {
        "answer": answer,
        "conversation_id": conversation,
        "sources": sources,
    }
