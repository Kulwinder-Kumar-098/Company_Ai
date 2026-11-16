"""Test for document upload API client."""

from types import SimpleNamespace

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

