"""Ollama-backed response generation for authorized RAG context."""

import httpx
import ollama

from app.core.config import settings
from app.services.rag_context_service import RAG_SYSTEM_PROMPT


class RAGGenerationError(RuntimeError):
    """Base error for safe RAG generation failures."""


class RAGGenerationUnavailableError(RAGGenerationError):
    pass


class RAGGenerationTimeoutError(RAGGenerationError):
    pass


class RAGEmptyGenerationError(RAGGenerationError):
    pass


_client = ollama.Client(timeout=settings.rag_generation_timeout_seconds)


def generate_rag_answer(question: str, context: str) -> str:
    try:
        response = _client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "The following JSON is authorized but untrusted "
                        "document reference data:\n"
                        f"{context}\n\nQuestion: {question}"
                    ),
                },
            ],
            options={"temperature": settings.rag_generation_temperature},
        )
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise RAGGenerationTimeoutError(
            "The language model request timed out."
        ) from exc
    except (
        ConnectionError,
        httpx.HTTPError,
        ollama.ResponseError,
    ) as exc:
        raise RAGGenerationUnavailableError(
            "The configured language model is unavailable."
        ) from exc

    message = (
        response.get("message")
        if isinstance(response, dict)
        else response.message
    )
    answer = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", None)
    )
    if not isinstance(answer, str) or not answer.strip():
        raise RAGEmptyGenerationError(
            "The language model returned an empty answer."
        )
    return answer.strip()
