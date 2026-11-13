from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
