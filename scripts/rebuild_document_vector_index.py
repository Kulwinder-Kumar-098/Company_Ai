"""Rebuild the configured vector store from compatible SQL chunk embeddings."""

from app.core.database import SessionLocal
from app.services.document_indexing_service import rebuild_document_vector_index


def main() -> None:
    with SessionLocal() as db:
        rebuild_document_vector_index(db)
    print("Document vector index rebuilt from current ready SQL chunks.")


if __name__ == "__main__":
    main()
