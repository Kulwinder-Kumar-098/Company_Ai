import hashlib

from sqlalchemy.orm import Session

from app.ingestion.chunking import DocumentChunk as ChunkData
from app.models.document_chunk import DocumentChunk


def save_document_chunks(
    db: Session,
    document_id: int,
    chunks: list[ChunkData],
) -> None:
    db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).delete()

    records: list[DocumentChunk] = []
    for chunk in chunks:
        content = chunk["text"]
        content_hash = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()

        records.append(
            DocumentChunk(
                document_id=document_id,
                chunk_index=chunk["chunk_index"],
                content=content,
                page_number=chunk["page_number"],
                content_hash=content_hash,
            )
        )

    db.add_all(records)
    db.flush()