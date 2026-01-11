"""Models for state management API endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_serializer


class StateInfo(BaseModel):
    """Metadata about stored session state.

    Returned by GET /state/{session_id}/info endpoint.
    """

    exists: bool = Field(..., description="Whether state exists for this session")
    session_id: str | None = Field(None, description="Session identifier")
    size_bytes: int | None = Field(None, description="Compressed state size in bytes")
    hash: str | None = Field(None, description="SHA256 hash for ETag/change detection")
    created_at: datetime | None = Field(None, description="When state was created/updated")
    expires_at: datetime | None = Field(None, description="When state will expire")
    source: str | None = Field(None, description="Storage source: 'redis' or 'archive'")

    @field_serializer("created_at", "expires_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class StateUploadResponse(BaseModel):
    """Response for state upload endpoint.

    Returned by POST /state/{session_id} endpoint.
    """

    message: str = Field(default="state_uploaded", description="Status message")
    size: int = Field(..., description="Uploaded state size in bytes")
