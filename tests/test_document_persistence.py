from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from qdrant_client import QdrantClient

from app.core.config import Settings, settings
from app.services.document_storage_service import (
    LocalDocumentStorage,
    S3DocumentStorage,
    save_uploaded_document,
)
from app.services.document_vector_store import QdrantDocumentVectorStore


def test_local_document_storage_upload_download_move_and_delete(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("persistent document", encoding="utf-8")
    storage = LocalDocumentStorage(tmp_path / "objects")

    storage.upload_file(source, "document.txt", "text/plain")
    destination = tmp_path / "downloaded.txt"
    storage.download_file("document.txt", destination)

    assert destination.read_text(encoding="utf-8") == "persistent document"
    assert storage.file_exists("document.txt")
    assert storage.move_file("document.txt", ".deleting/1/document.txt")
    assert not storage.file_exists("document.txt")
    storage.delete_file(".deleting/1/document.txt")
    assert not storage.file_exists(".deleting/1/document.txt")


def test_local_document_storage_rejects_path_traversal(tmp_path):
    storage = LocalDocumentStorage(tmp_path)

    with pytest.raises(ValueError, match="Invalid document storage key"):
        storage.file_exists("../outside.txt")


def test_s3_document_storage_uses_private_object_operations(tmp_path):
    class FakeS3Client:
        def __init__(self):
            self.objects = {}
            self.content_types = {}

        def upload_file(self, source, bucket, key, *, ExtraArgs):
            self.objects[(bucket, key)] = Path(source).read_bytes()
            self.content_types[key] = ExtraArgs["ContentType"]

        def download_file(self, bucket, key, destination):
            Path(destination).write_bytes(self.objects[(bucket, key)])

        def get_object(self, *, Bucket, Key):
            from io import BytesIO

            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

        def delete_object(self, *, Bucket, Key):
            self.objects.pop((Bucket, Key), None)

        def copy_object(self, *, Bucket, CopySource, Key):
            self.objects[(Bucket, Key)] = self.objects[
                (CopySource["Bucket"], CopySource["Key"])
            ]

        def head_object(self, *, Bucket, Key):
            if (Bucket, Key) not in self.objects:
                raise AssertionError("Unexpected missing object")

        def head_bucket(self, *, Bucket):
            assert Bucket == "company-documents"

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    client = FakeS3Client()
    storage = object.__new__(S3DocumentStorage)
    storage.bucket_name = "company-documents"
    storage.client = client

    storage.upload_file(source, "document.pdf", "application/pdf")
    destination = tmp_path / "downloaded.pdf"
    storage.download_file("document.pdf", destination)
    with storage.open_file("document.pdf") as stored:
        assert stored.read() == b"%PDF-1.4 test"
    assert destination.read_bytes() == b"%PDF-1.4 test"
    assert client.content_types["document.pdf"] == "application/pdf"
    assert storage.move_file("document.pdf", ".deleting/1/document.pdf")
    storage.delete_file(".deleting/1/document.pdf")
    storage.check_connection()
    assert not client.objects


def test_uploaded_file_is_saved_through_the_local_storage_adapter(
    tmp_path,
    monkeypatch,
):
    from fastapi import UploadFile
    from io import BytesIO

    storage_dir = tmp_path / "documents"
    monkeypatch.setattr(settings, "document_storage_backend", "local")
    monkeypatch.setattr(settings, "document_storage_dir", storage_dir)
    upload = UploadFile(
        filename="policy.txt",
        file=BytesIO(b"Policy content"),
        headers={"content-type": "text/plain"},
    )

    saved = save_uploaded_document(upload)

    assert saved["original_filename"] == "policy.txt"
    assert (storage_dir / saved["storage_key"]).read_bytes() == b"Policy content"


def test_qdrant_store_filters_by_authorized_chunk_ids_and_removes():
    client = QdrantClient(location=":memory:")
    store = QdrantDocumentVectorStore(
        client,
        "document-test",
    )
    model_name = settings.embedding_model
    try:
        store.add(
            [1, 2],
            [[1.0, 0.0], [0.0, 1.0]],
            model_name=model_name,
        )

        assert store.search(
            [1.0, 0.0],
            limit=5,
            allowed_chunk_ids=[2],
        ) == [(2, pytest.approx(0.0))]

        store.remove([2], model_name=model_name)
        assert store.search(
            [1.0, 0.0],
            limit=5,
            allowed_chunk_ids=[1, 2],
        ) == [(1, pytest.approx(1.0))]
    finally:
        client.close()


def test_qdrant_rejects_mixed_embedding_models():
    client = QdrantClient(location=":memory:")
    store = QdrantDocumentVectorStore(client, "document-model-test")
    try:
        store.add([1], [[1.0, 0.0]], model_name="model-a")

        with pytest.raises(ValueError, match="different embedding model"):
            store.add([2], [[0.0, 1.0]], model_name="model-b")
    finally:
        client.close()


def test_production_settings_require_durable_storage_and_qdrant():
    with pytest.raises(
        ValidationError,
        match="DOCUMENT_STORAGE_BACKEND=s3",
    ):
        Settings(
            database_url="postgresql+psycopg://user:pass@db.example/app",
            app_env="production",
            jwt_secret_key="a" * 32,
        )


def test_production_settings_accept_complete_storage_configuration():
    configured = Settings(
        database_url="postgresql+psycopg://user:pass@db.example/app",
        app_env="production",
        jwt_secret_key="a" * 32,
        document_storage_backend="s3",
        s3_endpoint_url="https://account.r2.cloudflarestorage.com",
        s3_bucket_name="company-documents",
        s3_access_key_id=SecretStr("access-key"),
        s3_secret_access_key=SecretStr("secret-key"),
        vector_store_backend="qdrant",
        qdrant_url="https://cluster.example",
        qdrant_api_key=SecretStr("qdrant-key"),
    )

    assert configured.document_storage_backend == "s3"
    assert configured.vector_store_backend == "qdrant"
