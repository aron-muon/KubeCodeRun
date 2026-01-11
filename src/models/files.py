"""File management data models for the Code Interpreter API."""

# Standard library imports
from datetime import datetime
from typing import List, Optional

# Third-party imports
from pydantic import BaseModel, Field, field_serializer


class FileUploadRequest(BaseModel):
    """Request model for file upload."""

    filename: str = Field(..., description="Name of the file")
    content_type: str | None = Field(default=None, description="MIME type of the file")


class FileUploadResponse(BaseModel):
    """Response model for file upload."""

    file_id: str = Field(..., description="Unique file identifier")
    filename: str
    size: int
    content_type: str
    upload_url: str = Field(..., description="Pre-signed URL for file upload")
    expires_at: datetime = Field(..., description="URL expiration time")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        return value.isoformat()


class FileInfo(BaseModel):
    """File information model."""

    file_id: str
    filename: str
    size: int
    content_type: str
    created_at: datetime
    path: str = Field(..., description="File path in the session")

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class FileListResponse(BaseModel):
    """Response model for listing files."""

    files: list[FileInfo]
    total_count: int
    total_size: int = Field(..., description="Total size of all files in bytes")


class FileDownloadResponse(BaseModel):
    """Response model for file download."""

    file_id: str
    filename: str
    download_url: str = Field(..., description="Pre-signed URL for file download")
    expires_at: datetime = Field(..., description="URL expiration time")

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        return value.isoformat()


class FileDeleteResponse(BaseModel):
    """Response model for file deletion."""

    file_id: str
    filename: str
    deleted: bool
    message: str | None = None
