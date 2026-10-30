
import hashlib
import os
import uuid
import zipfile

from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
}

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


def save_uploaded_document(
    upload: UploadFile,
) -> dict:
    """Stream, validate and save an uploaded document."""

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

    storage_dir = Path(settings.document_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    document_id = uuid.uuid4().hex
    storage_key = f"{document_id}{extension}"

    temporary_path = storage_dir / f".{document_id}.uploading"
    final_path = storage_dir / storage_key

    sha256 = hashlib.sha256()
    file_size = 0

    try:
        with temporary_path.open("xb") as destination:
            os.chmod(temporary_path, 0o600)

            while True:
                chunk = upload.file.read(CHUNK_SIZE)

                if not chunk:
                    break

                file_size += len(chunk)

                max_bytes = (
                    settings.max_document_size_mb * 1024 * 1024
                )

                if file_size > max_bytes:
                    raise DocumentValidationError(
                        "File exceeds the maximum allowed size."
                    )

                sha256.update(chunk)
                destination.write(chunk)

        if file_size == 0:
            raise DocumentValidationError("Empty files are not allowed.")

        validate_document_file(temporary_path, extension)

        os.replace(temporary_path, final_path)

        return {
            "original_filename": safe_original_name,
            "storage_key": storage_key,
            "file_extension": extension.lstrip("."),
            "mime_type": expected_mime,
            "file_size": file_size,
            "sha256": sha256.hexdigest(),
            "storage_path": final_path,
        }

    except Exception:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
