"""Persistent FAISS index for database-backed document chunks."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import faiss
import numpy as np

from app.core.config import settings

_index_thread_lock = threading.RLock()


class DocumentFaissIndex:
    def __init__(self, index_path: Path | None = None) -> None:
        self.index_path = index_path or settings.document_faiss_index_path
        self.metadata_path = self.index_path.with_suffix(
            self.index_path.suffix + ".metadata.json"
        )

    def rebuild(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str | None = None,
    ) -> None:
        with self._exclusive_lock():
            self._rebuild_locked(
                chunk_ids,
                embeddings,
                model_name or settings.embedding_model,
            )

    def rebuild_from_loader(
        self,
        load_vectors: Callable[[], tuple[list[int], list[list[float]]]],
        *,
        model_name: str | None = None,
    ) -> None:
        with self._exclusive_lock():
            chunk_ids, embeddings = load_vectors()
            self._rebuild_locked(
                chunk_ids,
                embeddings,
                model_name or settings.embedding_model,
            )

    def add(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        *,
        model_name: str,
        replace_ids: list[int] | None = None,
    ) -> None:
        if not chunk_ids:
            return
        with self._exclusive_lock():
            if not self.index_path.is_file():
                self._rebuild_locked(chunk_ids, embeddings, model_name)
                return
            index = faiss.read_index(str(self.index_path))
            metadata = self._read_metadata_locked()
            if metadata["model_name"] != model_name:
                raise ValueError(
                    "The FAISS index uses a different embedding model."
                )
            vectors = self._validated_matrix(embeddings)
            if vectors.shape[1] != metadata["dimension"] or index.d != vectors.shape[1]:
                raise ValueError(
                    "Embedding dimensions do not match the persisted FAISS index."
                )
            if len(chunk_ids) != len(vectors):
                raise ValueError("Chunk IDs and embeddings must have equal lengths.")
            ids = np.asarray(chunk_ids, dtype=np.int64)
            obsolete_ids = set(replace_ids or ())
            obsolete_ids.update(chunk_ids)
            index.remove_ids(np.asarray(sorted(obsolete_ids), dtype=np.int64))
            index.add_with_ids(vectors, ids)
            self._persist_locked(index, model_name, int(vectors.shape[1]))

    def remove(
        self,
        chunk_ids: list[int],
        *,
        model_name: str,
    ) -> None:
        if not chunk_ids or not self.index_path.is_file():
            return
        with self._exclusive_lock():
            if not self.index_path.is_file():
                return
            index = faiss.read_index(str(self.index_path))
            metadata = self._read_metadata_locked()
            if metadata["model_name"] != model_name:
                raise ValueError(
                    "The FAISS index uses a different embedding model."
                )
            index.remove_ids(
                np.asarray(sorted(set(chunk_ids)), dtype=np.int64)
            )
            self._persist_locked(
                index,
                model_name,
                int(metadata["dimension"]),
            )

    def _rebuild_locked(
        self,
        chunk_ids: list[int],
        embeddings: list[list[float]],
        model_name: str,
    ) -> None:
        if len(chunk_ids) != len(embeddings):
            raise ValueError("Chunk IDs and embeddings must have equal lengths.")
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Chunk IDs must be unique.")
        if not model_name:
            raise ValueError("An embedding model name is required.")

        if not embeddings:
            self.index_path.unlink(missing_ok=True)
            self.metadata_path.unlink(missing_ok=True)
            return

        vectors = self._validated_matrix(embeddings)
        ids = np.asarray(chunk_ids, dtype=np.int64)
        index = faiss.IndexIDMap2(
            faiss.IndexFlatIP(vectors.shape[1])
        )
        index.add_with_ids(vectors, ids)
        self._persist_locked(index, model_name, int(vectors.shape[1]))

    def _persist_locked(
        self,
        index: faiss.Index,
        model_name: str,
        dimension: int,
    ) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.index_path.parent,
                prefix=f".{self.index_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            os.chmod(temp_path, 0o600)
            faiss.write_index(index, str(temp_path))
            os.replace(temp_path, self.index_path)
            self._write_metadata_locked(model_name, dimension)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def search(
        self,
        embedding: list[float],
        limit: int | None = None,
        *,
        allowed_chunk_ids: list[int] | None = None,
    ) -> list[tuple[int, float]]:
        with self._exclusive_lock():
            if not self.index_path.is_file():
                return []
            index = faiss.read_index(str(self.index_path))
            metadata = self._read_metadata_locked()
            if metadata["model_name"] != settings.embedding_model:
                raise ValueError(
                    "The FAISS index uses a different embedding model."
                )
            if index.ntotal == 0:
                return []

            query = self._validated_matrix([embedding])
            if (
                query.shape[1] != index.d
                or query.shape[1] != metadata["dimension"]
            ):
                raise ValueError(
                    "Query embedding dimensions do not match the document index."
                )

            if allowed_chunk_ids is None:
                result_count = (
                    index.ntotal if limit is None else min(limit, index.ntotal)
                )
                search_index = index
            else:
                indexed_ids = set(
                    int(chunk_id)
                    for chunk_id in faiss.vector_to_array(index.id_map)
                )
                unique_ids = [
                    chunk_id
                    for chunk_id in dict.fromkeys(allowed_chunk_ids)
                    if chunk_id in indexed_ids
                ]
                if not unique_ids:
                    return []
                candidate_vectors = np.vstack(
                    [index.reconstruct(int(chunk_id)) for chunk_id in unique_ids]
                ).astype(np.float32, copy=False)
                search_index = faiss.IndexFlatIP(index.d)
                search_index.add(candidate_vectors)
                candidate_ids = np.asarray(unique_ids, dtype=np.int64)
                result_count = (
                    len(unique_ids)
                    if limit is None
                    else min(limit, len(unique_ids))
                )
            if result_count <= 0:
                return []

            scores, ids = search_index.search(query, result_count)
            if allowed_chunk_ids is not None:
                ids = np.where(ids >= 0, candidate_ids[ids], -1)
            return [
                (int(chunk_id), float(score))
                for chunk_id, score in zip(ids[0], scores[0])
                if chunk_id >= 0
            ]

    def _read_metadata_locked(self) -> dict[str, str | int]:
        try:
            metadata = json.loads(
                self.metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "The FAISS index metadata is missing or invalid; rebuild the index."
            ) from exc
        model_name = metadata.get("model_name")
        dimension = metadata.get("dimension")
        if (
            not isinstance(model_name, str)
            or not model_name
            or not isinstance(dimension, int)
            or dimension <= 0
        ):
            raise RuntimeError(
                "The FAISS index metadata is missing or invalid; rebuild the index."
            )
        return {"model_name": model_name, "dimension": dimension}

    def _write_metadata_locked(self, model_name: str, dimension: int) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.metadata_path.parent,
                prefix=f".{self.metadata_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                os.chmod(temp_path, 0o600)
                json.dump(
                    {"model_name": model_name, "dimension": dimension},
                    temp_file,
                )
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.metadata_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _validated_matrix(embeddings: list[list[float]]) -> np.ndarray:
        if not embeddings:
            raise ValueError("At least one embedding is required.")
        try:
            vectors = np.asarray(embeddings, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ValueError("Embeddings must be numeric vectors.") from exc

        if vectors.ndim != 2 or vectors.shape[1] == 0:
            raise ValueError("Embeddings must have one consistent dimension.")
        if not np.isfinite(vectors).all():
            raise ValueError("Embeddings must contain only finite values.")
        if np.any(np.linalg.norm(vectors, axis=1) == 0):
            raise ValueError("Embeddings must not be zero vectors.")

        faiss.normalize_L2(vectors)
        return vectors

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.index_path.with_suffix(
            self.index_path.suffix + ".lock"
        )
        with _index_thread_lock:
            with lock_path.open("a+b") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
