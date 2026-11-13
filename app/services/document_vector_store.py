"""FAISS development index and durable Qdrant vector store adapters."""

from collections.abc import Callable
from functools import lru_cache
import math
from typing import TypeVar

from app.core.config import settings
from app.services.document_faiss_service import DocumentFaissIndex

Result = TypeVar("Result")


class VectorStoreUnavailableError(RuntimeError):
    """Raised when the configured document vector store cannot be reached."""


class FaissDocumentVectorStore:
    def rebuild(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str,
    ) -> None:
        DocumentFaissIndex().rebuild(
            chunk_ids,
            embeddings,
            model_name=model_name,
        )

    def add(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str,
        replace_ids: list[int] | None = None,
    ) -> None:
        DocumentFaissIndex().add(
            chunk_ids,
            embeddings,
            model_name=model_name,
            replace_ids=replace_ids,
        )

    def remove(
        self,
        chunk_ids: list[int],
        *,
        model_name: str,
    ) -> None:
        DocumentFaissIndex().remove(chunk_ids, model_name=model_name)

    def search(
        self,
        embedding: list[float],
        *,
        limit: int,
        allowed_chunk_ids: list[int],
    ) -> list[tuple[int, float]]:
        return DocumentFaissIndex().search(
            embedding,
            limit=limit,
            allowed_chunk_ids=allowed_chunk_ids,
        )

    def is_available(self) -> bool:
        index = DocumentFaissIndex()
        return index.index_path.is_file() and index.metadata_path.is_file()

    def check_connection(self) -> None:
        DocumentFaissIndex().index_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )


class QdrantDocumentVectorStore:
    def __init__(self, client, collection_name: str) -> None:
        self.client = client
        self.collection_name = collection_name

    def rebuild(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str,
    ) -> None:
        vectors, dimension = self._validated_vectors(
            chunk_ids,
            embeddings,
            model_name,
        )

        if self._call(self.client.collection_exists, self.collection_name):
            self._call(self.client.delete_collection, self.collection_name)
        if not vectors:
            return
        self._create_collection(dimension)
        self.add(chunk_ids, vectors, model_name=model_name)

    def add(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str,
        replace_ids: list[int] | None = None,
    ) -> None:
        vectors, dimension = self._validated_vectors(
            chunk_ids,
            embeddings,
            model_name,
        )
        if not vectors:
            return

        if self._call(
            self.client.collection_exists,
            self.collection_name,
        ):
            self._validate_collection(dimension, model_name)
        else:
            self._create_collection(dimension)

        if replace_ids:
            self.remove(replace_ids, model_name=model_name)

        from qdrant_client import models

        self._call(
            self.client.upsert,
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload={
                        "chunk_id": chunk_id,
                        "embedding_model": model_name,
                    },
                )
                for chunk_id, vector in zip(
                    chunk_ids,
                    vectors,
                    strict=True,
                )
            ],
            wait=True,
        )

    def remove(
        self,
        chunk_ids: list[int],
        *,
        model_name: str,
    ) -> None:
        del model_name
        if not chunk_ids or not self._call(
            self.client.collection_exists,
            self.collection_name,
        ):
            return

        from qdrant_client import models

        self._call(
            self.client.delete,
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=chunk_ids),
            wait=True,
        )

    def search(
        self,
        embedding: list[float],
        *,
        limit: int,
        allowed_chunk_ids: list[int],
    ) -> list[tuple[int, float]]:
        if not allowed_chunk_ids or not self._call(
            self.client.collection_exists,
            self.collection_name,
        ):
            return []

        from qdrant_client import models

        response = self._call(
            self.client.query_points,
            collection_name=self.collection_name,
            query=embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_id",
                        match=models.MatchAny(any=allowed_chunk_ids),
                    ),
                    models.FieldCondition(
                        key="embedding_model",
                        match=models.MatchValue(
                            value=settings.embedding_model
                        ),
                    ),
                ]
            ),
            limit=limit,
            with_payload=True,
        )
        return [
            (int(point.id), float(point.score))
            for point in response.points
            if point.id is not None
        ]

    def is_available(self) -> bool:
        return self._call(
            self.client.collection_exists,
            self.collection_name,
        )

    def check_connection(self) -> None:
        self._call(self.client.get_collections)

    def _create_collection(self, dimension: int) -> None:
        from qdrant_client import models

        self._call(
            self.client.create_collection,
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            ),
        )
        self._call(
            self.client.create_payload_index,
            collection_name=self.collection_name,
            field_name="chunk_id",
            field_schema=models.PayloadSchemaType.INTEGER,
            wait=True,
        )
        self._call(
            self.client.create_payload_index,
            collection_name=self.collection_name,
            field_name="embedding_model",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )

    def _validate_collection(
        self,
        dimension: int,
        model_name: str,
    ) -> None:
        collection = self._call(
            self.client.get_collection,
            self.collection_name,
        )
        vector_config = collection.config.params.vectors
        if (
            not isinstance(vector_config, dict)
            and vector_config.size != dimension
        ):
            raise ValueError(
                "Embedding dimensions do not match the Qdrant collection."
            )
        if isinstance(vector_config, dict):
            raise ValueError(
                "The configured Qdrant collection must use unnamed vectors."
            )

        points, _ = self._call(
            self.client.scroll,
            collection_name=self.collection_name,
            limit=1,
            with_payload=True,
        )
        if (
            points
            and (points[0].payload or {}).get("embedding_model") != model_name
        ):
            raise ValueError(
                "The Qdrant collection uses a different embedding model."
            )

    @staticmethod
    def _validated_vectors(
        chunk_ids: list[int],
        embeddings: list[list[float]],
        model_name: str,
    ) -> tuple[list[list[float]], int]:
        if len(chunk_ids) != len(embeddings):
            raise ValueError("Chunk IDs and embeddings must have equal lengths.")
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Chunk IDs must be unique.")
        if any(chunk_id <= 0 for chunk_id in chunk_ids):
            raise ValueError("Chunk IDs must be positive integers.")
        if not model_name:
            raise ValueError("An embedding model name is required.")
        try:
            vectors = [
                [float(value) for value in vector]
                for vector in embeddings
            ]
        except (TypeError, ValueError) as exc:
            raise ValueError("Embeddings must be numeric vectors.") from exc
        if not vectors:
            return [], 0

        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or 0 in dimensions:
            raise ValueError("Embeddings must have one consistent dimension.")
        if any(
            not math.isfinite(value)
            for vector in vectors
            for value in vector
        ):
            raise ValueError("Embeddings must contain only finite values.")
        if any(not any(value != 0.0 for value in vector) for vector in vectors):
            raise ValueError("Embeddings must not be zero vectors.")
        return vectors, dimensions.pop()

    @staticmethod
    def _call(
        function: Callable[..., Result],
        *args: object,
        **kwargs: object,
    ) -> Result:
        from qdrant_client.http.exceptions import ApiException

        try:
            return function(*args, **kwargs)
        except ApiException as exc:
            raise VectorStoreUnavailableError(
                "The configured Qdrant vector store is unavailable."
            ) from exc


@lru_cache(maxsize=4)
def _create_qdrant_client(url: str, api_key: str, timeout: float):
    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise VectorStoreUnavailableError(
            "Qdrant storage requires the qdrant-client dependency."
        ) from exc

    return QdrantClient(
        url=url,
        api_key=api_key,
        timeout=timeout,
    )


def get_document_vector_store():
    if settings.vector_store_backend == "faiss":
        return FaissDocumentVectorStore()
    if settings.vector_store_backend != "qdrant":
        raise ValueError("Unsupported document vector store.")

    url = (settings.qdrant_url or "").strip()
    api_key = (
        settings.qdrant_api_key.get_secret_value()
        if settings.qdrant_api_key
        else ""
    )
    if not url or not api_key:
        raise VectorStoreUnavailableError(
            "Qdrant vector storage is not fully configured."
        )
    return QdrantDocumentVectorStore(
        _create_qdrant_client(
            url,
            api_key,
            settings.qdrant_timeout_seconds,
        ),
        settings.qdrant_collection,
    )
