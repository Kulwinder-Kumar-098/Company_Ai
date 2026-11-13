from pathlib import Path
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.main import app
from app.services.document_vector_store import VectorStoreUnavailableError


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_render_port_and_fastapi_entrypoint():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        'CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 '
        '--port ${PORT:-10000}"]'
    ) in dockerfile


def test_dockerignore_excludes_secrets_and_local_data():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env" in dockerignore.splitlines()
    assert ".env.*" in dockerignore.splitlines()
    assert "data/" in dockerignore.splitlines()


def test_health_endpoint_does_not_require_database_or_llm(monkeypatch):
    def unexpected_call(*_args, **_kwargs):
        pytest.fail("/health attempted an external service call")

    monkeypatch.setattr("app.main.engine.connect", unexpected_call)
    monkeypatch.setattr(
        "app.agents.orchestrator.run_agent_query",
        unexpected_call,
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint_checks_configured_dependencies(monkeypatch):
    class Connection:
        def execute(self, _statement):
            return None

    monkeypatch.setattr(
        "app.main.engine.connect",
        lambda: nullcontext(Connection()),
    )
    monkeypatch.setattr(
        "app.main.get_document_storage",
        lambda: SimpleNamespace(check_connection=lambda: None),
    )
    monkeypatch.setattr(
        "app.main.get_document_vector_store",
        lambda: SimpleNamespace(check_connection=lambda: None),
    )
    monkeypatch.setattr(
        "app.main.httpx.get",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "models": [
                    {"name": settings.embedding_model},
                    {"name": settings.llm_model},
                ]
            },
        ),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {
            "database": "ok",
            "document_storage": "ok",
            "vector_store": "ok",
            "embedding_service": "ok",
        },
    }


def test_readiness_endpoint_hides_dependency_error_details(monkeypatch):
    monkeypatch.setattr(
        "app.main.engine.connect",
        lambda: (_ for _ in ()).throw(SQLAlchemyError("database secret")),
    )
    monkeypatch.setattr(
        "app.main.get_document_storage",
        lambda: SimpleNamespace(check_connection=lambda: None),
    )
    monkeypatch.setattr(
        "app.main.get_document_vector_store",
        lambda: SimpleNamespace(
            check_connection=lambda: (_ for _ in ()).throw(
                VectorStoreUnavailableError("private Qdrant URL")
            )
        ),
    )
    monkeypatch.setattr(
        "app.main.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("private Ollama URL")
        ),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert "private" not in response.text
    assert response.json()["detail"]["dependencies"] == {
        "database": "unavailable",
        "document_storage": "ok",
        "vector_store": "unavailable",
        "embedding_service": "unavailable",
    }


def test_document_worker_starts_and_stops_with_application(monkeypatch):
    worker_started = Event()
    worker_stop_events: list[Event] = []

    def fake_worker(stop_event: Event) -> None:
        worker_stop_events.append(stop_event)
        worker_started.set()
        stop_event.wait()

    monkeypatch.setattr("app.main.run_worker", fake_worker)

    with TestClient(app):
        assert worker_started.wait(timeout=1)

    assert worker_stop_events[0].is_set()
