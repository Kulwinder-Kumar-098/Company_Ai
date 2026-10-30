"""Embedding generation and response validation."""

import math

import httpx
import ollama

from app.core.config import settings


class EmbeddingServiceError(RuntimeError):
    """Raised when Ollama cannot return a usable embedding batch."""


_client = ollama.Client(timeout=settings.embedding_timeout_seconds)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("Embedding inputs must not be empty.")

    try:
        response = _client.embed(model=settings.embedding_model, input=texts)
    except (
        ConnectionError,
        TimeoutError,
        httpx.HTTPError,
        ollama.ResponseError,
    ) as exc:
        raise EmbeddingServiceError(
            "The configured Ollama embedding service is unavailable."
        ) from exc
    raw_embeddings = (
        response.get("embeddings")
        if isinstance(response, dict)
        else response.embeddings
    )
    if len(raw_embeddings) != len(texts):
        raise ValueError(
            "Embedding service returned a different number of vectors "
            "than inputs."
        )

    embeddings: list[list[float]] = []
    dimensions: int | None = None
    for raw_embedding in raw_embeddings:
        embedding = [float(value) for value in raw_embedding]
        if not embedding or any(not math.isfinite(value) for value in embedding):
            raise ValueError(
                "Embedding service returned an empty or non-finite vector."
            )
        if dimensions is None:
            dimensions = len(embedding)
        elif len(embedding) != dimensions:
            raise ValueError(
                "Embedding service returned vectors with inconsistent dimensions."
            )
        if not any(value != 0.0 for value in embedding):
            raise ValueError("Embedding service returned a zero vector.")
        embeddings.append(embedding)

    return embeddings
