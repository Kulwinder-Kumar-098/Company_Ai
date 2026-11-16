"""Tests for Enterprise AI Flask Frontend."""

from flask_app import app


def test_flask_index_renders_html():
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "ENTERPRISE" in html
    assert "AI Assistant Workspace" in html
    assert "Document Knowledge Base" in html
    assert "Platform &amp; Overview" in html or "Platform & Overview" in html
    assert "Employee Registration" in html


def test_flask_health_check():
    client = app.test_client()
    response = client.get("/health")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "enterprise-ai-frontend"
