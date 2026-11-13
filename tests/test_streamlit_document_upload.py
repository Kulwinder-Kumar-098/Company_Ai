from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from app.clients import document_api_client
from app.clients.document_api_client import upload_document


def test_document_upload_client_sends_bearer_auth_and_multipart(monkeypatch):
    observed = {}

    def fake_request(method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return SimpleNamespace(
            ok=True,
            status_code=201,
            json=lambda: {"id": 12, "processing_status": "uploaded"},
        )

    monkeypatch.setattr(document_api_client.requests, "request", fake_request)

    result = upload_document(
        "https://api.example.test/",
        "test-token",
        filename="policy.pdf",
        content=b"%PDF-1.4 test",
        mime_type="application/pdf",
        access_scope="private",
    )

    assert result["id"] == 12
    assert observed["url"] == "https://api.example.test/documents/upload"
    assert observed["headers"]["Authorization"] == "Bearer test-token"
    assert observed["files"]["file"] == (
        "policy.pdf",
        b"%PDF-1.4 test",
        "application/pdf",
    )
    assert observed["data"] == {"access_scope": "private"}


def test_authenticated_streamlit_page_renders_document_upload_ui(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        document_api_client,
        "list_documents",
        lambda *args, **kwargs: [],
    )

    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    app.session_state["token"] = "test-token"
    app.session_state["user_info"] = {"email": "admin@example.test"}
    app.run()

    assert not app.exception
    assert app.get("file_uploader")
    assert any(
        button.label == "Upload and process"
        for button in app.button
    )
