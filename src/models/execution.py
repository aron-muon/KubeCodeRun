"""Execution data models for the Code Interpreter API."""

# Standard library imports
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import List, Optional

# Third-party imports
from pydantic import BaseModel, Field, field_serializer


class ExecutionStatus(str, Enum):
    """Execution status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class OutputType(str, Enum):
    """Output type enumeration."""

    STDOUT = "stdout"
    STDERR = "stderr"
    IMAGE = "image"
    FILE = "file"
    ERROR = "error"


class ExecutionOutput(BaseModel):
    """Model for execution output."""

    type: OutputType
    content: str = Field(..., description="Output content or file path")
    mime_type: str | None = Field(default=None, description="MIME type for file outputs")
    size: int | None = Field(default=None, description="Size in bytes for file outputs")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


class CodeExecution(BaseModel):
    """Model for code execution request and response."""

    execution_id: str = Field(..., description="Unique execution identifier")
    session_id: str = Field(..., description="Associated session ID")
    code: str = Field(..., description="Code to execute")
    language: str = Field(default="py", description="Programming language")

    # Execution state
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    # Results
    outputs: list[ExecutionOutput] = Field(default_factory=list)
    exit_code: int | None = Field(default=None)
    error_message: str | None = Field(default=None)

    # Resource usage
    execution_time_ms: int | None = Field(default=None)
    memory_peak_mb: float | None = Field(default=None)

    @field_serializer("created_at", "started_at", "completed_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class ExecuteCodeRequest(BaseModel):
    """Request model for code execution."""

    code: str = Field(..., description="Code to execute", min_length=1)
    language: str = Field(default="py", description="Programming language")
    timeout: int | None = Field(default=None, description="Execution timeout in seconds")


class ExecuteCodeResponse(BaseModel):
    """Response model for code execution."""

    execution_id: str
    status: ExecutionStatus
    outputs: list[ExecutionOutput] = Field(default_factory=list)
    exit_code: int | None = None
    error_message: str | None = None
    execution_time_ms: int | None = None
