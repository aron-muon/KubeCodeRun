"""Redis-backed continuation state for Programmatic Tool Calling.

Replay mode persists the full request (``code`` + ``tools`` + ``files``) plus
the accumulated tool-result ``history`` keyed by ``execution_id`` so each
continuation can re-run the sandbox without the client re-sending it. The
``continuation_token`` handed back to LibreChat is the ``execution_id``.
"""

from __future__ import annotations

import time
from typing import Any

import redis.asyncio as redis
import structlog
from pydantic import BaseModel, Field

from ...models.exec import RequestFile
from ...models.programmatic import ProgrammaticTool, ToolResultIn
from .constants import EXECUTION_STATE_TTL_SECONDS

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "ptc:state:"


class ExecutionState(BaseModel):
    """Persisted state for one PTC execution across replay round-trips."""

    execution_id: str
    session_id: str
    language: str = "python"
    code: str
    tools: list[ProgrammaticTool] = Field(default_factory=list)
    files: list[RequestFile] = Field(default_factory=list)
    timeout: int | None = None
    call_count: int = 0
    # call_id -> {"result", "is_error", "error_message"}
    history: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # call_ids the server has handed to the client (guards against forged ids).
    emitted_call_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    last_activity: float = Field(default_factory=time.time)


class ProgrammaticStateStore:
    """Persists :class:`ExecutionState` in Redis with a bounded TTL."""

    def __init__(self, redis_client: redis.Redis | None = None):
        self._redis = redis_client

    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            from ...core.pool import redis_pool

            self._redis = redis_pool.get_client()
        return self._redis

    @staticmethod
    def _key(execution_id: str) -> str:
        return f"{_KEY_PREFIX}{execution_id}"

    async def save(self, state: ExecutionState) -> None:
        state.last_activity = time.time()
        await self.redis.set(
            self._key(state.execution_id),
            state.model_dump_json(),
            ex=EXECUTION_STATE_TTL_SECONDS,
        )

    async def load(self, execution_id: str) -> ExecutionState | None:
        raw = await self.redis.get(self._key(execution_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return ExecutionState.model_validate_json(raw)
        except Exception as exc:
            logger.warning("Failed to deserialize PTC execution state", execution_id=execution_id, error=str(exc))
            return None

    async def delete(self, execution_id: str) -> None:
        await self.redis.delete(self._key(execution_id))

    @staticmethod
    def merge_tool_results(state: ExecutionState, tool_results: list[ToolResultIn]) -> None:
        """Merge tool results into history (idempotent by ``call_id``).

        Only results for call_ids the server actually issued are accepted; a
        forged id would otherwise be served as a cache hit on the next sandbox
        run, silently skipping the real tool call the user code makes.
        """
        issued = set(state.emitted_call_ids)
        for result in tool_results:
            if result.call_id not in issued:
                logger.warning(
                    "Ignoring tool_result for un-issued call_id",
                    execution_id=state.execution_id,
                    call_id=result.call_id,
                )
                continue
            state.history[result.call_id] = {
                "result": result.result,
                "is_error": result.is_error,
                "error_message": result.error_message,
            }
        state.call_count = len(state.history)
