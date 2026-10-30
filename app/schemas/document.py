from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    id: int
    original_filename: str
    file_extension: str
    mime_type: str
    file_size: int
    uploaded_by_employee_id: str
    department: str | None
    access_scope: str
    document_series_id: str
    version_number: int
    is_current: bool
    processing_status: str
    duplicate_review_status: str
    duplicate_similarity: float | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentVersionUploadResponse(BaseModel):
    id: int
    version_number: int
    processing_status: str


class DocumentVersionHistoryItem(BaseModel):
    id: int
    original_filename: str
    version_number: int
    is_current: bool
    processing_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentProcessingStatusResponse(BaseModel):
    document_id: int
    processing_status: str
    active_version: int | None
    chunk_count: int
    processed_at: datetime | None
    error: str | None