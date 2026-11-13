"""Document validation and local or S3-compatible file storage."""

import hashlib
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from fastapi import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

CANONICAL_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    ".txt": "text/plain",
}

CHUNK_SIZE = 1024 * 1024


class DocumentValidationError(Exception):
    pass


class DocumentStorageUnavailableError(RuntimeError):
    """Raised when the configured durable document store is unavailable."""


class DocumentStorage(Protocol):
    def upload_file(
        self,
        source_path: Path,
        storage_key: str,
        content_type: str,
    ) -> None: ...

    def download_file(self, storage_key: str, destination: Path) -> None: ...

    def open_file(self, storage_key: str) -> BinaryIO: ...

    def delete_file(self, storage_key: str) -> None: ...

    def move_file(self, source_key: str, destination_key: str) -> bool: ...

    def file_exists(self, storage_key: str) -> bool: ...

    def check_connection(self) -> None: ...


class LocalDocumentStorage:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir.resolve()

    def upload_file(
        self,
        source_path: Path,
        storage_key: str,
        content_type: str,
    ) -> None:
        del content_type
        destination = self._path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source_path, destination)
        os.chmod(destination, 0o600)

    def download_file(self, storage_key: str, destination: Path) -> None:
        source = self._path(storage_key)
        if not source.is_file():
            raise FileNotFoundError(f"Document file not found: {storage_key}")
        shutil.copyfile(source, destination)

    def open_file(self, storage_key: str) -> BinaryIO:
        source = self._path(storage_key)
        if not source.is_file():
            raise FileNotFoundError(f"Document file not found: {storage_key}")
        return source.open("rb")

    def delete_file(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)

    def move_file(self, source_key: str, destination_key: str) -> bool:
        source = self._path(source_key)
        if not source.exists():
            return False
        destination = self._path(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(source, destination)
        return True

    def file_exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def check_connection(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.storage_dir.is_dir():
            raise DocumentStorageUnavailableError(
                "Local document storage directory is unavailable."
            )
        with tempfile.NamedTemporaryFile(
            dir=self.storage_dir,
            prefix=".health-check-",
        ):
            pass

    def _path(self, storage_key: str) -> Path:
        key_path = Path(storage_key)
        if (
            not storage_key
            or key_path.is_absolute()
            or any(part in {"", ".", ".."} for part in key_path.parts)
        ):
            raise ValueError("Invalid document storage key.")
        path = (self.storage_dir / key_path).resolve()
        if not path.is_relative_to(self.storage_dir):
            raise ValueError("Document storage key is outside storage directory.")
        return path


class S3DocumentStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket_name: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise DocumentStorageUnavailableError(
                "S3 storage requires the boto3 dependency."
            ) from exc

        self.bucket_name = bucket_name
        self.client = _create_s3_client(
            boto3,
            Config,
            endpoint_url,
            access_key_id,
            secret_access_key,
            region,
        )

    def upload_file(
        self,
        source_path: Path,
        storage_key: str,
        content_type: str,
    ) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(storage_key)
        try:
            self.client.upload_file(
                str(source_path),
                self.bucket_name,
                storage_key,
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError) as exc:
            raise DocumentStorageUnavailableError(
                "Could not persist the uploaded document."
            ) from exc

    def download_file(self, storage_key: str, destination: Path) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(storage_key)
        try:
            self.client.download_file(
                self.bucket_name,
                storage_key,
                str(destination),
            )
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            ) == 404:
                raise FileNotFoundError(
                    f"Document file not found: {storage_key}"
                ) from exc
            raise DocumentStorageUnavailableError(
                "Could not retrieve the stored document."
            ) from exc
        except BotoCoreError as exc:
            raise DocumentStorageUnavailableError(
                "Could not retrieve the stored document."
            ) from exc

    def open_file(self, storage_key: str) -> BinaryIO:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(storage_key)
        try:
            return self.client.get_object(
                Bucket=self.bucket_name,
                Key=storage_key,
            )["Body"]
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            ) == 404:
                raise FileNotFoundError(
                    f"Document file not found: {storage_key}"
                ) from exc
            raise DocumentStorageUnavailableError(
                "Could not retrieve the stored document."
            ) from exc
        except BotoCoreError as exc:
            raise DocumentStorageUnavailableError(
                "Could not retrieve the stored document."
            ) from exc

    def delete_file(self, storage_key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(storage_key)
        try:
            self.client.delete_object(
                Bucket=self.bucket_name,
                Key=storage_key,
            )
        except (BotoCoreError, ClientError) as exc:
            raise DocumentStorageUnavailableError(
                "Could not delete the stored document."
            ) from exc

    def move_file(self, source_key: str, destination_key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(source_key)
        self._validate_key(destination_key)
        if not self.file_exists(source_key):
            return False
        try:
            self.client.copy_object(
                Bucket=self.bucket_name,
                CopySource={
                    "Bucket": self.bucket_name,
                    "Key": source_key,
                },
                Key=destination_key,
            )
            self.delete_file(source_key)
        except (BotoCoreError, ClientError) as exc:
            raise DocumentStorageUnavailableError(
                "Could not prepare the stored document for deletion."
            ) from exc
        return True

    def file_exists(self, storage_key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        self._validate_key(storage_key)
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=storage_key,
            )
            return True
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            ) == 404:
                return False
            raise DocumentStorageUnavailableError(
                "Could not check the stored document."
            ) from exc
        except BotoCoreError as exc:
            raise DocumentStorageUnavailableError(
                "Could not check the stored document."
            ) from exc

    def check_connection(self) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except (BotoCoreError, ClientError) as exc:
            raise DocumentStorageUnavailableError(
                "Configured S3-compatible storage is unavailable."
            ) from exc

    @staticmethod
    def _validate_key(storage_key: str) -> None:
        if (
            not storage_key
            or storage_key.startswith("/")
            or any(part in {"", ".", ".."} for part in storage_key.split("/"))
        ):
            raise ValueError("Invalid document storage key.")


@lru_cache(maxsize=4)
def _create_s3_client(
    boto3_module,
    config_class,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
):
    return boto3_module.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
        config=config_class(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def get_document_storage() -> DocumentStorage:
    if settings.document_storage_backend == "local":
        return LocalDocumentStorage(settings.document_storage_dir)
    if settings.document_storage_backend != "s3":
        raise ValueError("Unsupported document storage backend.")

    endpoint_url = (settings.s3_endpoint_url or "").strip()
    bucket_name = (settings.s3_bucket_name or "").strip()
    access_key_id = (
        settings.s3_access_key_id.get_secret_value()
        if settings.s3_access_key_id
        else ""
    )
    secret_access_key = (
        settings.s3_secret_access_key.get_secret_value()
        if settings.s3_secret_access_key
        else ""
    )
    if not all(
        (endpoint_url, bucket_name, access_key_id, secret_access_key)
    ):
        raise DocumentStorageUnavailableError(
            "S3-compatible document storage is not fully configured."
        )
    return S3DocumentStorage(
        endpoint_url=endpoint_url,
        bucket_name=bucket_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=settings.s3_region,
    )


def validate_document_file(path: Path, extension: str) -> None:
    """Validate actual file content rather than trusting its extension."""

    with path.open("rb") as file:
        header = file.read(1024)

    if extension == ".pdf":
        if b"%PDF-" not in header:
            raise DocumentValidationError("Invalid PDF file.")

    elif extension == ".docx":
        if not zipfile.is_zipfile(path):
            raise DocumentValidationError("Invalid DOCX file.")

        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise DocumentValidationError(
                        "Invalid DOCX document structure."
                    )

                if archive.testzip() is not None:
                    raise DocumentValidationError(
                        "DOCX archive is corrupted."
                    )
        except zipfile.BadZipFile as exc:
            raise DocumentValidationError(
                "Invalid DOCX archive."
            ) from exc

    elif extension == ".txt":
        try:
            with path.open("r", encoding="utf-8") as file:
                while file.read(CHUNK_SIZE):
                    pass
        except UnicodeDecodeError as exc:
            raise DocumentValidationError(
                "TXT files must contain valid UTF-8 text."
            ) from exc


def save_uploaded_document(upload: UploadFile) -> dict:
    """Stream, validate and persist an uploaded document."""

    if not upload.filename:
        raise DocumentValidationError("Filename is required.")

    safe_original_name = (
        upload.filename.replace("\\", "/").split("/")[-1]
    )

    if not safe_original_name or safe_original_name in {".", ".."}:
        raise DocumentValidationError("Invalid filename.")

    extension = Path(safe_original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentValidationError(
            "Only PDF, DOCX and TXT files are supported."
        )

    expected_mime = CANONICAL_MIME_TYPES[extension]
    declared_mime = upload.content_type or ""
    if declared_mime not in {"", "application/octet-stream", expected_mime}:
        raise DocumentValidationError(
            "File content type does not match its extension."
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="company-ai-upload-",
        suffix=extension,
    )
    temporary_path = Path(temporary_name)
    os.chmod(temporary_path, 0o600)
    storage_key = f"{uuid.uuid4().hex}{extension}"
    sha256 = hashlib.sha256()
    file_size = 0
    storage = None
    upload_started = False

    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = upload.file.read(CHUNK_SIZE)
                if not chunk:
                    break

                file_size += len(chunk)
                max_bytes = settings.max_document_size_mb * 1024 * 1024
                if file_size > max_bytes:
                    raise DocumentValidationError(
                        "File exceeds the maximum allowed size."
                    )

                sha256.update(chunk)
                destination.write(chunk)

        if file_size == 0:
            raise DocumentValidationError("Empty files are not allowed.")
        validate_document_file(temporary_path, extension)
        storage = get_document_storage()
        upload_started = True
        storage.upload_file(
            temporary_path,
            storage_key,
            expected_mime,
        )

        return {
            "original_filename": safe_original_name,
            "storage_key": storage_key,
            "file_extension": extension.lstrip("."),
            "mime_type": expected_mime,
            "file_size": file_size,
            "sha256": sha256.hexdigest(),
        }
    except Exception:
        if upload_started and storage is not None:
            try:
                storage.delete_file(storage_key)
            except (DocumentStorageUnavailableError, ValueError):
                logger.exception("Failed to clean up incomplete document upload.")
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
