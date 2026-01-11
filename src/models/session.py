"""Session data models for the Code Interpreter API."""

# Standard library imports
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

# Third-party imports
from pydantic import BaseModel, Field, field_serializer


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    IDLE = "idle"
    TERMINATED = "terminated"
    ERROR = "error"


class FileInfo(BaseModel):
    """Information about a file in the session."""

    filename: str
    size: int
    mime_type: str
    created_at: datetime
    path: str


class Session(BaseModel):
    """Session model representing a code execution environment."""

    session_id: str = Field(..., description="Unique session identifier")
    status: SessionStatus = Field(default=SessionStatus.ACTIVE, description="Current session status")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Session creation timestamp")
    last_activity: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last activity timestamp")
    expires_at: datetime = Field(..., description="Session expiration timestamp")

    # Pod information
    pod_name: str | None = Field(default=None, description="Kubernetes pod name")
    pod_status: str | None = Field(default=None, description="Pod status")

    # File management
    files: dict[str, FileInfo] = Field(default_factory=dict, description="Files in the session")
    working_directory: str = Field(default="/mnt/data", description="Working directory path")

    # Resource usage
    memory_usage_mb: float | None = Field(default=None, description="Current memory usage in MB")
    cpu_usage_percent: float | None = Field(default=None, description="Current CPU usage percentage")

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional session metadata")

    @field_serializer("created_at", "last_activity", "expires_at")
    def serialize_datetime(self, value: datetime) -> str:
        return value.isoformat()


class SessionCreate(BaseModel):
    """Request model for creating a new session."""

    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional session metadata")


class SessionResponse(BaseModel):
    """Response model for session operations."""

    session_id: str
    status: SessionStatus
    created_at: datetime
    expires_at: datetime
    message: str | None = None

    @field_serializer("created_at", "expires_at")
    def serialize_datetime(self, value: datetime) -> str:
        return value.isoformat()
