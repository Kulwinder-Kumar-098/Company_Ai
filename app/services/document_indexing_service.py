"""Persist compatible embeddings and incrementally maintain FAISS."""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.document_faiss_service import DocumentFaissIndex
from app.services.embedding_service import embed_texts


def generate_missing_chunk_embeddings(db: Session) -> None:
    chunks = db.scalars(
        select(DocumentChunk)
        .order_by(DocumentChunk.id)
    ).all()
    missing_chunks = []
    for chunk in chunks:
        if chunk.embedding is None:
            missing_chunks.append(chunk)
            continue
        if (
            chunk.embedding_model is None
            or chunk.embedding_dimension is None
        ):
            chunk.embedding = None
            chunk.embedding_model = None
            chunk.embedding_dimension = None
            missing_chunks.append(chunk)
            continue
        if chunk.embedding_model != settings.embedding_model:
            raise ValueError(
                "Stored document embeddings use an incompatible model; "
                "re-embed documents before indexing."
            )
        if len(chunk.embedding) != chunk.embedding_dimension:
            raise ValueError(
                f"Stored embedding metadata is invalid for chunk {chunk.id}."
            )

    if not missing_chunks:
        return

    embeddings: list[list[float]] = []
    for offset in range(0, len(missing_chunks), settings.embedding_batch_size):
        batch = missing_chunks[
            offset : offset + settings.embedding_batch_size
        ]
        batch_embeddings = embed_texts([chunk.content for chunk in batch])
        if embeddings and len(batch_embeddings[0]) != len(embeddings[0]):
            raise ValueError(
                "Embedding service returned inconsistent dimensions across batches."
            )
        embeddings.extend(batch_embeddings)

    if len(embeddings) != len(missing_chunks):
        raise ValueError("Embedding service returned an incomplete batch.")
    for chunk, embedding in zip(missing_chunks, embeddings, strict=True):
        chunk.embedding = embedding
        chunk.embedding_model = settings.embedding_model
        chunk.embedding_dimension = len(embedding)
    db.flush()


def _load_compatible_vectors(
    db: Session,
    include_document_id: int | None = None,
) -> tuple[list[int], list[list[float]]]:
    indexable_documents = [
        and_(
            Document.is_current.is_(True),
            Document.processing_status == "ready",
        )
    ]
    if include_document_id is not None:
        indexable_documents.append(Document.id == include_document_id)
    chunks = db.scalars(
        select(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.embedding.is_not(None),
            or_(*indexable_documents),
        )
        .order_by(DocumentChunk.id)
    ).all()
    vectors: list[list[float]] = []
    chunk_ids: list[int] = []
    dimensions: int | None = None
    for chunk in chunks:
        embedding = chunk.embedding
        if (
            chunk.embedding_model != settings.embedding_model
            or chunk.embedding_dimension is None
            or embedding is None
            or len(embedding) != chunk.embedding_dimension
        ):
            raise ValueError(
                "Stored document embeddings use incompatible model metadata."
            )
        if dimensions is None:
            dimensions = chunk.embedding_dimension
        elif dimensions != chunk.embedding_dimension:
            raise ValueError(
                "Stored document embeddings have inconsistent dimensions."
            )
        chunk_ids.append(chunk.id)
        vectors.append(embedding)
    return chunk_ids, vectors


def rebuild_document_faiss_index(
    db: Session,
    *,
    include_document_id: int | None = None,
) -> None:
    def load_vectors() -> tuple[list[int], list[list[float]]]:
        return _load_compatible_vectors(db, include_document_id)

    DocumentFaissIndex().rebuild_from_loader(
        load_vectors,
        model_name=settings.embedding_model,
    )


def index_document_chunks(
    db: Session,
    document_id: int,
    *,
    replace_chunk_ids: list[int] | None = None,
) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} does not exist.")
    if not document.is_current:
        latest_version_number = db.scalar(
            select(Document.version_number)
            .where(
                Document.document_series_id == document.document_series_id
            )
            .order_by(Document.version_number.desc())
            .limit(1)
        )
        if document.version_number != latest_version_number:
            return

    chunks = db.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.embedding.is_not(None),
        )
        .order_by(DocumentChunk.id)
    ).all()
    chunk_ids = [chunk.id for chunk in chunks]
    vectors: list[list[float]] = []
    for chunk in chunks:
        embedding = chunk.embedding
        if (
            chunk.embedding_model != settings.embedding_model
            or chunk.embedding_dimension is None
            or embedding is None
            or len(embedding) != chunk.embedding_dimension
        ):
            raise ValueError(
                "Document chunk embedding metadata is incompatible with "
                "the configured model."
            )
        vectors.append(embedding)

    index = DocumentFaissIndex()
    if not index.index_path.exists() or not index.metadata_path.exists():
        rebuild_document_faiss_index(
            db,
            include_document_id=document_id,
        )
        return
    index.add(
        chunk_ids,
        vectors,
        model_name=settings.embedding_model,
        replace_ids=replace_chunk_ids,
    )
