
"""Build and search a FAISS index using Ollama embeddings."""

import json
from pathlib import Path

import faiss
import numpy as np
import ollama

DEFAULT_MODEL = "nomic-embed-text:latest"
DEFAULT_INDEX_DIR = Path("data/indexes/policies")


def embed_texts(texts: list[str]) -> np.ndarray:
    response = ollama.embed(
        model=DEFAULT_MODEL,
        input=texts,
    )
    embeddings = np.array(
        response["embeddings"], dtype=np.float32
    )
    faiss.normalize_L2(embeddings)
    return embeddings


def build_faiss_index(
    chunks: list[dict],
    index_dir: Path = DEFAULT_INDEX_DIR,
) -> None:
    if not chunks:
        raise ValueError("No document chunks provided.")

    texts = [chunk["text"] for chunk in chunks]
    embeddings = embed_texts(texts)

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    index_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_dir / "policies.faiss"))

    metadata = {
        "model_name": DEFAULT_MODEL,
        "chunks": chunks,
    }
    (index_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )


def search_faiss(
    query: str,
    index_dir: Path = DEFAULT_INDEX_DIR,
    top_k: int = 5,
) -> list[dict]:
    index_path = index_dir / "policies.faiss"
    metadata_path = index_dir / "metadata.json"

    if not index_path.exists() or not metadata_path.exists():
        raise FileNotFoundError(
            "FAISS index not found. Build the index first."
        )

    index = faiss.read_index(str(index_path))
    metadata = json.loads(
        metadata_path.read_text(encoding="utf-8")
    )

    query_embedding = embed_texts([query])
    scores, indices = index.search(
        query_embedding,
        min(top_k, index.ntotal),
    )

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue

        results.append({
            **metadata["chunks"][int(idx)],
            "score": float(score),
        })

    return results
