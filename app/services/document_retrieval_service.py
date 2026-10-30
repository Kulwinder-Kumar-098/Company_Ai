"""Authorization-first semantic retrieval for uploaded documents."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.employee import Employee
from app.services.document_access_service import accessible_document_conditions
from app.services.document_faiss_service import DocumentFaissIndex
from app.services.embedding_service import embed_texts


class VectorIndexUnavailableError(RuntimeError):
    """Raised when an authorized corpus exists but the vector index is absent."""


def retrieve_document_chunks(
    db: Session,
    employee: Employee,
    query: str,
    top_k: int = 5,
    *,
    relevance_threshold: float | None = None,
) -> list[dict[str, int | float | str | None]]:
    if not query.strip():
        raise ValueError("Question cannot be empty.")
    if top_k <= 0 or top_k > settings.rag_max_top_k:
        raise ValueError("top_k is outside the configured range.")

    authorized_chunk_ids = db.scalars(
        select(DocumentChunk.id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            accessible_document_conditions(employee),
            Document.is_current.is_(True),
            Document.processing_status == "ready",
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_model == settings.embedding_model,
        )
        .order_by(DocumentChunk.id)
    ).all()
    if not authorized_chunk_ids:
        return []

    index = DocumentFaissIndex()
    if not index.index_path.is_file() or not index.metadata_path.is_file():
        raise VectorIndexUnavailableError(
            "The authorized document index is not available."
        )

    query_embedding = embed_texts([query])[0]
    ranked_chunks = index.search(
        query_embedding,
        limit=top_k,
        allowed_chunk_ids=authorized_chunk_ids,
    )
    threshold = (
        settings.rag_relevance_threshold
        if relevance_threshold is None
        else relevance_threshold
    )
    relevant_chunks = [
        (chunk_id, score)
        for chunk_id, score in ranked_chunks
        if score >= threshold
    ]
    if not relevant_chunks:
        return []

    scores = dict(relevant_chunks)
    relevant_ids = [chunk_id for chunk_id, _ in relevant_chunks]
    rows = db.execute(
        select(
            DocumentChunk.id,
            Document.id,
            Document.original_filename,
            Document.version_number,
            DocumentChunk.page_number,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.id.in_(relevant_ids),
            accessible_document_conditions(employee),
            Document.is_current.is_(True),
            Document.processing_status == "ready",
            DocumentChunk.embedding_model == settings.embedding_model,
        )
    ).all()

    chunks_by_id = {
        row[0]: {
            "chunk_id": row[0],
            "document_id": row[1],
            "source": row[2],
            "version_number": row[3],
            "page_number": row[4],
            "chunk_index": row[5],
            "content": row[6],
            "score": scores[row[0]],
        }
        for row in rows
    }

    results: list[dict[str, int | float | str | None]] = []
    document_counts: dict[int, int] = {}
    seen_content: set[tuple[int, str]] = set()
    for chunk_id, _ in relevant_chunks:
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            continue
        document_id = int(chunk["document_id"])
        normalized_content = str(chunk["content"]).strip()
        content_key = (document_id, normalized_content)
        if content_key in seen_content or document_counts.get(document_id, 0) >= 2:
            continue
        seen_content.add(content_key)
        document_counts[document_id] = document_counts.get(document_id, 0) + 1
        results.append(chunk)
        if len(results) >= top_k:
            break
    return results
