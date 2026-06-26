"""Pydantic models for the Programmatic Tool Calling endpoint.

Wire contract mirrors the reference ``code-interpreter`` service
(``ProgrammaticRequestBody`` / ``ProgrammaticResponse``) so the
``@librechat/agents`` PTC client interoperates without changes.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exec import FileRef, RequestFile


class ProgrammaticTool(BaseModel):
    """A tool definition the sandbox exposes as an async function."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None


class ToolResultIn(BaseModel):
    """A tool result returned by LibreChat for a previously requested call."""

    call_id: str
    result: Any = None
    is_error: bool = False
    error_message: str | None = None


class ProgrammaticRequest(BaseModel):
    """Request body for ``POST /exec/programmatic``.

    Two shapes flow through this model:
      * **initial** — ``code`` + ``tools`` (+ optional ``session_id`` /
        ``files`` / ``timeout`` / ``language``).
      * **continuation** — ``continuation_token`` + ``tool_results``.
    """

    model_config = ConfigDict(populate_by_name=True)

    code: str | None = None
    tools: list[ProgrammaticTool] | None = None
    session_id: str | None = None
    timeout: int | None = Field(default=None, description="Per-iteration sandbox timeout (milliseconds)")
    continuation_token: str | None = None
    tool_results: list[ToolResultIn] | None = None
    user_id: str | None = None
    files: list[RequestFile] = Field(default_factory=list)
    language: Literal["python", "bash"] | None = None
    # Back-compat alias: the agents bash PTC client sends ``lang``.
    lang: Literal["python", "bash"] | None = None

    @model_validator(mode="after")
    def _normalize_language(self) -> "ProgrammaticRequest":
        # ``language`` wins when both are present (mirrors the reference router).
        if self.language is None and self.lang is not None:
            self.language = self.lang
        return self

    @property
    def resolved_language(self) -> str:
        return self.language or "python"

    @property
    def is_continuation(self) -> bool:
        return bool(self.continuation_token) and self.tool_results is not None


class ProgrammaticToolCall(BaseModel):
    """A pending tool call the sandbox requested (sent to LibreChat)."""

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ProgrammaticResponse(BaseModel):
    """Response body for ``POST /exec/programmatic``."""

    status: Literal["tool_call_required", "completed", "error"]
    continuation_token: str | None = None
    tool_calls: list[ProgrammaticToolCall] | None = None
    partial_stdout: str | None = None
    partial_stderr: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    files: list[FileRef] | None = None
    session_id: str | None = None
    error: str | None = None
