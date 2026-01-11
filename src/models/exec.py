"""Models for the /exec endpoint compatible with LibreChat API."""

# Standard library imports
from typing import Any, List, Optional

# Third-party imports
from pydantic import BaseModel, Field


class FileRef(BaseModel):
    """File reference model for execution response."""

    id: str
    name: str
    path: str | None = None  # Make path optional


class RequestFile(BaseModel):
    """Request file model."""

    id: str
    session_id: str
    name: str


class ExecRequest(BaseModel):
    """Request model for /exec endpoint."""

    code: str = Field(..., description="The source code to be executed")
    lang: str = Field(..., description="The programming language of the code")
    # Accept any JSON type for args to avoid 422s when clients send objects/arrays
    args: Any | None = Field(default=None, description="Optional command line arguments (any JSON type)")
    user_id: str | None = Field(default=None, description="Optional user identifier")
    entity_id: str | None = Field(
        default=None,
        description="Optional assistant/agent identifier for file sharing",
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session ID to continue an existing session (for state persistence)",
    )
    files: list[RequestFile] = Field(
        default_factory=list,
        description="Array of file references to be used during execution",
    )


class ExecResponse(BaseModel):
    """Response model for /exec endpoint - LibreChat compatible format."""

    session_id: str
    files: list[FileRef] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    # State persistence fields (Python only)
    has_state: bool = Field(
        default=False,
        description="Whether Python state was captured (Python executions only)",
    )
    state_size: int | None = Field(default=None, description="Compressed state size in bytes")
    state_hash: str | None = Field(default=None, description="SHA256 hash for ETag/change detection")
