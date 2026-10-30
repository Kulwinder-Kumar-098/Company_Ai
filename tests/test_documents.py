
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.document_routes import router
from app.core.config import settings
from app.core.database import get_db
from app.models import Base, Employee, Permission, Role
from app.models.audit_event import AuditEvent
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_processing_job import DocumentProcessingJob
from app.services.document_faiss_service import DocumentFaissIndex


@pytest.fixture
def document_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        "test-secret-key-for-document-tests",
    )
    monkeypatch.setattr(
        settings,
        "document_storage_dir",
        tmp_path,
    )
    monkeypatch.setattr(
        settings,
        "document_faiss_index_path",
        tmp_path / "documents.faiss",
    )
    monkeypatch.setattr(
        settings,
        "max_document_size_mb",
        20,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    with TestSession() as db:
        create_permission = Permission(
            code="documents:create",
            description="Upload documents",
        )
        read_permission = Permission(
            code="documents:read",
            description="Read accessible documents",
        )
        publish_permission = Permission(
            code="documents:publish",
            description="Publish company documents",
        )
        delete_permission = Permission(
            code="documents:delete",
            description="Delete documents",
        )
        version_permission = Permission(
            code="documents:version",
            description="Create document versions",
        )

        admin_role = Role(
            name="admin",
            permissions=[
                create_permission,
                read_permission,
                publish_permission,
                delete_permission,
                version_permission,
            ],
        )
        manager_role = Role(
            name="manager",
            permissions=[
                create_permission,
                read_permission,
                version_permission,
            ],
        )
        employee_role = Role(name="employee")

        admin = Employee(
            employee_id="ADMIN001",
            full_name="Admin",
            email="admin@example.test",
            department="IT",
            role=admin_role,
        )

        manager = Employee(
            employee_id="MANAGER001",
            full_name="Manager",
            email="manager@example.test",
            department="Engineering",
            role=manager_role,
        )

        employee = Employee(
            employee_id="EMP001",
            full_name="Employee",
            email="employee@example.test",
            department="Engineering",
            role=employee_role,
        )

        db.add_all([admin, manager, employee])
        db.commit()

        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db

        with TestClient(
            app,
            raise_server_exceptions=False,
        ) as client:
            yield client, db, tmp_path

        app.dependency_overrides.clear()

    Base.metadata.drop_all(engine)
    engine.dispose()


def token(employee_id):
    return jwt.encode(
        {
            "sub": employee_id,
            "exp": datetime.now(timezone.utc)
            + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def headers(employee_id):
    return {
        "Authorization": f"Bearer {token(employee_id)}"
    }


def test_admin_can_upload_pdf(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": (
                "policy.pdf",
                b"%PDF-1.4\nTest PDF content",
                "application/pdf",
            )
        },
        data={"access_scope": "company"},
    )

    assert response.status_code == 201
    assert response.json()["original_filename"] == "policy.pdf"
    assert response.json()["access_scope"] == "company"
    assert response.json()["processing_status"] == "uploaded"
    assert response.json()["version_number"] == 1
    assert response.json()["is_current"] is True
    assert response.json()["document_series_id"]

    document = db.scalar(select(Document))
    assert document is not None
    assert document.uploaded_by_employee_id == "ADMIN001"
    assert (storage / document.storage_key).exists()
    job = db.scalar(
        select(DocumentProcessingJob).where(
            DocumentProcessingJob.document_id == document.id
        )
    )
    assert job is not None
    assert job.status == "queued"


def test_identical_pdf_bytes_are_rejected_across_different_filenames(
    document_client,
):
    client, db, storage = document_client
    content = b"%PDF-1.4\nidentical file bytes"
    first = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={"file": ("employee_policy.pdf", content, "application/pdf")},
    )
    duplicate = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": (
                "employee_policy_final.pdf",
                content,
                "application/pdf",
            )
        },
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This document has already been uploaded."
    assert db.query(Document).count() == 1
    assert db.query(DocumentProcessingJob).count() == 1
    assert len(list(storage.iterdir())) == 1


def test_manager_can_upload_private_txt(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "notes.txt",
                b"Company meeting notes",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["access_scope"] == "private"

    document = db.scalar(select(Document))
    assert document is not None
    assert document.department is None
    assert (storage / document.storage_key).exists()


def test_uploading_version_preserves_access_and_switches_current_document(
    document_client,
):
    client, db, _ = document_client
    first_response = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "engineering-policy.txt",
                b"First policy version",
                "text/plain",
            )
        },
        data={"access_scope": "department"},
    )
    first_id = first_response.json()["id"]
    first_document = db.get(Document, first_id)
    first_document.processing_status = "ready"
    first_document.processed_at = datetime.now(timezone.utc)
    db.commit()

    version_response = client.post(
        f"/documents/{first_id}/versions",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "engineering-policy-revised.txt",
                b"Revised policy version",
                "text/plain",
            )
        },
    )

    assert version_response.status_code == 201
    version_payload = version_response.json()
    assert version_payload["version_number"] == 2
    first = db.get(Document, first_id)
    second = db.get(Document, version_payload["id"])
    assert first.is_current is True
    assert second.is_current is False
    assert second.document_series_id == first.document_series_id
    assert second.access_scope == first.access_scope == "department"
    assert second.department == first.department == "Engineering"
    assert second.uploaded_by_employee_id == "MANAGER001"
    assert db.scalar(
        select(DocumentProcessingJob).where(
            DocumentProcessingJob.document_id == second.id
        )
    ).status == "queued"
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "document_version_uploaded",
            AuditEvent.resource_id == str(second.id),
        )
    )
    assert event is not None
    assert event.details["version_number"] == 2

    status_response = client.get(
        f"/documents/{second.id}/status",
        headers=headers("MANAGER001"),
    )
    assert status_response.status_code == 200
    assert status_response.json()["active_version"] == 1
    assert status_response.json()["processing_status"] == "uploaded"

    history = client.get(
        f"/documents/{second.id}/versions",
        headers=headers("MANAGER001"),
    )
    assert history.status_code == 200
    assert [item["version_number"] for item in history.json()] == [2, 1]
    assert set(history.json()[0]) == {
        "id",
        "original_filename",
        "version_number",
        "is_current",
        "processing_status",
        "created_at",
    }

    stale_source = client.post(
        f"/documents/{first.id}/versions",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "engineering-policy-v3.txt",
                b"Should not be accepted from stale version",
                "text/plain",
            )
        },
    )
    assert stale_source.status_code == 409

    listing = client.get(
        "/documents",
        headers=headers("MANAGER001"),
    )
    assert [item["id"] for item in listing.json()] == [first.id]


def test_version_history_requires_access_to_private_document(document_client):
    client, db, _ = document_client
    upload_response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": (
                "private.txt",
                b"Admin private document",
                "text/plain",
            )
        },
    )
    document_id = upload_response.json()["id"]

    response = client.get(
        f"/documents/{document_id}/versions",
        headers=headers("MANAGER001"),
    )

    assert response.status_code == 404
    assert db.query(Document).count() == 1


def test_duplicate_version_upload_is_rejected_and_file_removed(document_client):
    client, db, storage = document_client
    first_content = b"First version"
    second_content = b"Second version"
    first_response = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={"file": ("policy.txt", first_content, "text/plain")},
    )
    first_id = first_response.json()["id"]
    first_document = db.get(Document, first_id)
    first_document.processing_status = "ready"
    db.commit()
    second_response = client.post(
        f"/documents/{first_id}/versions",
        headers=headers("MANAGER001"),
        files={"file": ("policy-v2.txt", second_content, "text/plain")},
    )
    second_document = db.get(Document, second_response.json()["id"])
    second_document.processing_status = "ready"
    first_document.is_current = False
    second_document.is_current = True
    db.commit()

    duplicate = client.post(
        f"/documents/{second_response.json()['id']}/versions",
        headers=headers("MANAGER001"),
        files={"file": ("policy-v3.txt", second_content, "text/plain")},
    )

    assert second_response.status_code == 201
    assert duplicate.status_code == 409
    assert db.query(Document).count() == 2
    assert db.query(DocumentProcessingJob).count() == 2
    assert len(list(storage.iterdir())) == 2


def test_current_version_cannot_be_deleted_while_history_exists(document_client):
    client, db, storage = document_client
    first = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={"file": ("policy.txt", b"v1", "text/plain")},
    )
    document_id = first.json()["id"]
    first_document = db.get(Document, document_id)
    first_document.processing_status = "ready"
    db.commit()
    current = client.post(
        f"/documents/{document_id}/versions",
        headers=headers("MANAGER001"),
        files={"file": ("policy-v2.txt", b"v2", "text/plain")},
    )
    current_id = current.json()["id"]
    db.get(Document, document_id).is_current = False
    current_document = db.get(Document, current_id)
    current_document.is_current = True
    current_document.processing_status = "ready"
    db.commit()

    response = client.delete(
        f"/documents/{current_id}",
        headers=headers("MANAGER001"),
    )

    assert response.status_code == 409
    assert db.get(Document, current_id).is_current is True
    assert db.get(Document, document_id).is_current is False
    assert len(list(storage.iterdir())) == 2


def test_document_status_is_access_controlled_and_reports_safe_fields(
    document_client,
):
    client, db, _ = document_client
    upload_response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": ("private-status.txt", b"private", "text/plain")
        },
    )
    document_id = upload_response.json()["id"]
    document = db.get(Document, document_id)
    document.processing_status = "failed"
    document.processing_error_code = "source_file_missing"
    db.commit()

    denied = client.get(
        f"/documents/{document_id}/status",
        headers=headers("MANAGER001"),
    )
    allowed = client.get(
        f"/documents/{document_id}/status",
        headers=headers("ADMIN001"),
    )

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json() == {
        "document_id": document_id,
        "processing_status": "failed",
        "active_version": None,
        "chunk_count": 0,
        "processed_at": None,
        "error": "Document processing failed.",
    }
    assert "storage_key" not in allowed.json()
    assert "processing_error_code" not in allowed.json()


def test_manager_cannot_publish_company_document(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "policy.pdf",
                b"%PDF-1.4\nTest",
                "application/pdf",
            )
        },
        data={"access_scope": "company"},
    )

    assert response.status_code == 403
    assert db.scalar(select(Document)) is None
    assert list(storage.iterdir()) == []


def test_employee_without_permission_cannot_upload(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("EMP001"),
        files={
            "file": (
                "notes.txt",
                b"Some notes",
                "text/plain",
            )
        },
    )

    assert response.status_code == 403
    assert db.scalar(select(Document)) is None
    assert list(storage.iterdir()) == []


def test_unsupported_extension_is_rejected(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": (
                "malware.exe",
                b"some content",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400
    assert db.scalar(select(Document)) is None
    assert list(storage.iterdir()) == []


def test_invalid_pdf_content_is_rejected(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={
            "file": (
                "fake.pdf",
                b"This is not a PDF",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 400
    assert db.scalar(select(Document)) is None
    assert list(storage.iterdir()) == []


def test_department_scope_uses_uploader_department(document_client):
    client, db, storage = document_client

    response = client.post(
        "/documents/upload",
        headers=headers("MANAGER001"),
        files={
            "file": (
                "engineering.txt",
                b"Engineering document",
                "text/plain",
            )
        },
        data={"access_scope": "department"},
    )

    assert response.status_code == 201

    document = db.scalar(select(Document))
    assert document.department == "Engineering"
    assert document.access_scope == "department"


def test_list_documents_returns_only_accessible_scopes(document_client):
    client, db, _ = document_client
    documents = [
        Document(
            original_filename="company.txt",
            storage_key="company.txt",
            file_extension="txt",
            mime_type="text/plain",
            file_size=10,
            sha256="1" * 64,
            uploaded_by_employee_id="ADMIN001",
            access_scope="company",
            processing_status="uploaded",
        ),
        Document(
            original_filename="department.txt",
            storage_key="department.txt",
            file_extension="txt",
            mime_type="text/plain",
            file_size=10,
            sha256="2" * 64,
            uploaded_by_employee_id="ADMIN001",
            department="Engineering",
            access_scope="department",
            processing_status="uploaded",
        ),
        Document(
            original_filename="other-department.txt",
            storage_key="other-department.txt",
            file_extension="txt",
            mime_type="text/plain",
            file_size=10,
            sha256="3" * 64,
            uploaded_by_employee_id="ADMIN001",
            department="Finance",
            access_scope="department",
            processing_status="uploaded",
        ),
        Document(
            original_filename="manager-private.txt",
            storage_key="manager-private.txt",
            file_extension="txt",
            mime_type="text/plain",
            file_size=10,
            sha256="4" * 64,
            uploaded_by_employee_id="MANAGER001",
            access_scope="private",
            processing_status="uploaded",
        ),
    ]
    db.add_all(documents)
    db.commit()

    response = client.get(
        "/documents",
        headers=headers("MANAGER001"),
    )

    assert response.status_code == 200
    assert {
        item["original_filename"] for item in response.json()
    } == {
        "company.txt",
        "department.txt",
        "manager-private.txt",
    }


def test_delete_removes_document_chunks_file_and_index(document_client):
    client, db, storage = document_client
    upload_response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={"file": ("delete.txt", b"Delete me", "text/plain")},
    )
    document_id = upload_response.json()["id"]
    document = db.get(Document, document_id)
    chunk = DocumentChunk(
        document_id=document_id,
        chunk_index=0,
        content="Delete me",
        page_number=None,
        content_hash="5" * 64,
        embedding=[1.0, 0.0],
        embedding_model=settings.embedding_model,
        embedding_dimension=2,
    )
    db.add(chunk)
    db.commit()
    DocumentFaissIndex().rebuild([chunk.id], [[1.0, 0.0]])
    file_path = storage / document.storage_key

    response = client.delete(
        f"/documents/{document_id}",
        headers=headers("ADMIN001"),
    )

    assert response.status_code == 204
    assert db.get(Document, document_id) is None
    assert not db.query(DocumentChunk).filter_by(document_id=document_id).first()
    assert (
        db.query(DocumentProcessingJob)
        .filter_by(document_id=document_id)
        .first()
        is None
    )
    assert not file_path.exists()
    assert not DocumentFaissIndex().index_path.exists()


def test_uploader_can_schedule_document_reprocessing(document_client):
    client, db, _ = document_client
    upload_response = client.post(
        "/documents/upload",
        headers=headers("ADMIN001"),
        files={"file": ("retry.txt", b"Retry me", "text/plain")},
    )
    document_id = upload_response.json()["id"]

    response = client.post(
        f"/documents/{document_id}/reprocess",
        headers=headers("ADMIN001"),
    )

    assert response.status_code == 202
    assert response.json()["processing_status"] == "uploaded"
    assert db.get(Document, document_id).processing_status == "uploaded"
    job = db.scalar(
        select(DocumentProcessingJob).where(
            DocumentProcessingJob.document_id == document_id
        )
    )
    assert job.status == "queued"
    assert job.attempts == 0
    retry_event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "document_processing_retried",
            AuditEvent.resource_id == str(document_id),
        )
    )
    assert retry_event is not None
