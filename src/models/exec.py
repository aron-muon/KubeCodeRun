"""Models for the /exec endpoint compatible with LibreChat API."""

# Standard library imports
from typing import Any, List, Optional

# Third-party imports
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field


class FileRef(BaseModel):
    """File reference model for execution response.

    LibreChat 0.8.5 (packages/data-provider/src/codeEnvRef.ts) expects
    ``storage_session_id`` rather than ``session_id`` on each file ref;
    we emit both for back-compat. ``resource_id``/``kind``/``version``
    carry the LC ``CodeEnvFile`` discriminator into the response so
    skill/agent attribution survives a generation round-trip.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    path: str | None = None  # Make path optional
    session_id: str | None = None
    resource_id: str | None = None
    kind: str | None = None  # 'skill' | 'agent' | 'user'
    version: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_session_id(self) -> str | None:
        """LibreChat alias for session_id; expected by @librechat/agents."""
        return self.session_id


class RequestFile(BaseModel):
    """Request file model.

    Accepts both ``storage_session_id`` (LibreChat 0.8.5 convention) and
    ``session_id`` (legacy) so old and new clients work side by side.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    session_id: str = Field(
        validation_alias=AliasChoices("storage_session_id", "session_id"),
    )
    name: str
    resource_id: str | None = None
    kind: str | None = None  # 'skill' | 'agent' | 'user'
    version: int | None = None


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
    auto_mounted_files: int = Field(
        default=0,
        description=(
            "Number of session-scoped uploaded files that were automatically "
            "re-hydrated from storage into /mnt/data because the pod was fresh. "
            "Surfaces pod rotations caused by pool churn, OOMKills or evictions."
        ),
    )
