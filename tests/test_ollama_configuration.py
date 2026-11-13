import numpy as np
import pytest
from pydantic import ValidationError

from app.core import ollama_client
from app.core.config import Settings, settings
from app.ingestion import indexing


def test_ollama_host_defaults_to_local_development():
    configured = Settings(_env_file=None, database_url="sqlite://")

    assert configured.ollama_host == "http://localhost:11434"
    assert configured.llm_model == "llama3.2:3b"
    assert configured.embedding_model == "nomic-embed-text:latest"


def test_ollama_host_reads_and_normalizes_remote_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "  HTTPS://ollama.example.com/  ")

    configured = Settings(_env_file=None, database_url="sqlite://")

    assert configured.ollama_host == "https://ollama.example.com"


@pytest.mark.parametrize(
    "host",
    ["", "ftp://ollama.example.com", "http://", "http://host:99999"],
)
def test_ollama_host_rejects_invalid_urls(host):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            ollama_host=host,
        )


@pytest.mark.parametrize(
    ("host", "expected_host"),
    [
        ("http://localhost:11434/", "http://localhost:11434"),
        ("https://ollama.example.com/", "https://ollama.example.com"),
    ],
)
def test_ollama_client_factory_uses_configured_host(
    monkeypatch,
    host,
    expected_host,
):
    captured = {}
    configured_host = Settings(
        _env_file=None,
        database_url="sqlite://",
        ollama_host=host,
    ).ollama_host

    class FakeClient:
        def __init__(self, *, host, timeout):
            captured.update(host=host, timeout=timeout)

    monkeypatch.setattr(settings, "ollama_host", configured_host)
    monkeypatch.setattr(ollama_client.ollama, "Client", FakeClient)

    client = ollama_client.create_ollama_client(timeout=17)

    assert isinstance(client, FakeClient)
    assert captured == {"host": expected_host, "timeout": 17}


def test_legacy_faiss_embeddings_use_shared_embedding_service(monkeypatch):
    calls = []

    def fake_embed_texts(texts):
        calls.append(texts)
        return [[3.0, 4.0]]

    monkeypatch.setattr(
        "app.services.embedding_service.embed_texts",
        fake_embed_texts,
    )

    embeddings = indexing.embed_texts(["document text"])

    assert calls == [["document text"]]
    np.testing.assert_allclose(embeddings, [[0.6, 0.8]])
