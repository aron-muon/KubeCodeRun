"""Programmatic Tool Calling orchestration (replay loop).

Ties the harness, sentinel parser and Redis state store to KubeCodeRun's
existing execution path. Each ``execute`` call runs one sandbox iteration via
``ExecutionOrchestrator`` (injecting the replay history as an inline file) and
maps the result onto the PTC wire contract:

  * sentinel present  -> ``tool_call_required`` (+ continuation_token)
  * no sentinel       -> ``completed`` (stdout / stderr / files)

Bash PTC is not yet supported; such requests are rejected with a clear 400.
"""

import json
import uuid

import structlog

from ...models.exec import ExecRequest
from ...models.programmatic import (
    ProgrammaticRequest,
    ProgrammaticResponse,
    ProgrammaticTool,
    ProgrammaticToolCall,
)
from ..orchestrator import ExecutionOrchestrator
from .constants import (
    MAX_REPLAY_CALLS,
    MAX_TOOLS_PER_REQUEST,
    PTC_HISTORY_FILENAME,
    is_reserved_ptc_filename,
)
from .harness import build_python_code, normalize_python_function_name
from .sentinel import extract_pending_from_stdout
from .state import ExecutionState, ProgrammaticStateStore

logger = structlog.get_logger(__name__)


class ProgrammaticError(Exception):
    """Raised for client-facing PTC failures (mapped to an HTTP status)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _validate_tool_names(tools: list[ProgrammaticTool]) -> None:
    """Reject tool names that would generate invalid Python in the replay harness.

    An empty/whitespace name or one with no identifier-safe characters yields
    ``async def ()`` (a syntax error) which would otherwise surface as a
    confusing ``completed`` response carrying a traceback instead of a clear
    400. Two distinct names that normalize to the same identifier are also
    rejected, since the second stub would silently shadow the first.
    """
    seen: dict[str, str] = {}
    for tool in tools:
        if not tool.name or not tool.name.strip():
            raise ProgrammaticError(400, "Each tool must have a non-empty name")
        normalized = normalize_python_function_name(tool.name)
        if not normalized:
            raise ProgrammaticError(400, f'Tool name "{tool.name}" has no identifier-safe characters')
        prior = seen.get(normalized)
        if prior is not None and prior != tool.name:
            raise ProgrammaticError(
                400,
                f'Tool names "{prior}" and "{tool.name}" both normalize to the Python '
                f'identifier "{normalized}"; rename one to avoid a collision',
            )
        seen[normalized] = tool.name


class ProgrammaticService:
    """Drives the stateless replay loop for ``POST /exec/programmatic``."""

    def __init__(
        self,
        orchestrator: ExecutionOrchestrator,
        state_store: ProgrammaticStateStore | None = None,
    ):
        self.orchestrator = orchestrator
        self.state = state_store or ProgrammaticStateStore()

    async def execute(
        self,
        request: ProgrammaticRequest,
        user_id: str | None = None,
        request_id: str = "",
        api_key_hash: str | None = None,
        is_env_key: bool = False,
    ) -> ProgrammaticResponse:
        if request.is_continuation:
            return await self._continue(request, user_id, request_id, api_key_hash, is_env_key)
        return await self._initial(request, user_id, request_id, api_key_hash, is_env_key)

    async def _initial(
        self,
        request: ProgrammaticRequest,
        user_id: str | None,
        request_id: str,
        api_key_hash: str | None,
        is_env_key: bool,
    ) -> ProgrammaticResponse:
        if not request.code:
            raise ProgrammaticError(400, "Missing required field: code")
        if not request.tools:
            raise ProgrammaticError(400, "Missing required field: tools (must be a non-empty array)")
        if len(request.tools) > MAX_TOOLS_PER_REQUEST:
            raise ProgrammaticError(
                400, f"Too many tools provided ({len(request.tools)}). Maximum is {MAX_TOOLS_PER_REQUEST}."
            )
        _validate_tool_names(request.tools)
        if request.resolved_language == "bash":
            raise ProgrammaticError(400, "bash programmatic tool calling is not supported by this server")
        if request.resolved_language != "python":
            raise ProgrammaticError(400, f'Unsupported language "{request.resolved_language}"')
        for f in request.files:
            if f.name and is_reserved_ptc_filename(f.name):
                raise ProgrammaticError(
                    400, f'files[].name "{f.name}" is reserved for PTC runtime and cannot be supplied by callers'
                )

        state = ExecutionState(
            execution_id=uuid.uuid4().hex,
            session_id=request.session_id or "",
            language="python",
            code=request.code,
            tools=request.tools,
            files=request.files,
            timeout=request.timeout,
        )
        return await self._run_and_respond(state, user_id, request_id, api_key_hash, is_env_key)

    async def _continue(
        self,
        request: ProgrammaticRequest,
        user_id: str | None,
        request_id: str,
        api_key_hash: str | None,
        is_env_key: bool,
    ) -> ProgrammaticResponse:
        execution_id = request.continuation_token or ""
        state = await self.state.load(execution_id)
        if state is None:
            raise ProgrammaticError(404, "Execution not found or expired")

        self.state.merge_tool_results(state, request.tool_results or [])
        if state.call_count > MAX_REPLAY_CALLS:
            await self.state.delete(execution_id)
            raise ProgrammaticError(400, f"Exceeded maximum tool calls ({MAX_REPLAY_CALLS})")

        return await self._run_and_respond(state, user_id, request_id, api_key_hash, is_env_key)

    async def _run_and_respond(
        self,
        state: ExecutionState,
        user_id: str | None,
        request_id: str,
        api_key_hash: str | None,
        is_env_key: bool,
    ) -> ProgrammaticResponse:
        tools = [t.model_dump() for t in state.tools]
        harness_code = build_python_code(state.execution_id, tools, state.code)
        history_json = json.dumps(state.history)
        extra_files = [{"filename": PTC_HISTORY_FILENAME, "content": history_json, "read_only": True}]

        exec_request = ExecRequest(
            code=harness_code,
            lang="py",
            session_id=state.session_id or None,
            user_id=user_id,
            files=state.files,
        )
        resp = await self.orchestrator.execute(
            exec_request,
            request_id=request_id,
            api_key_hash=api_key_hash,
            is_env_key=is_env_key,
            extra_files=extra_files,
            capture_state_override=False,
        )
        # Reuse the resolved session across rounds so /mnt/data persists.
        state.session_id = resp.session_id

        clean_stdout, pending = extract_pending_from_stdout(resp.stdout, state.execution_id)

        if pending is not None:
            return await self._respond_tool_calls(state, pending, clean_stdout, resp.stderr)

        # No new tool call: the run completed.
        await self.state.delete(state.execution_id)
        files = [f for f in (resp.files or []) if f.name != PTC_HISTORY_FILENAME]
        return ProgrammaticResponse(
            status="completed",
            stdout=clean_stdout,
            stderr=resp.stderr,
            files=files,
            session_id=state.session_id,
        )

    async def _respond_tool_calls(
        self,
        state: ExecutionState,
        pending: list[dict],
        clean_stdout: str,
        stderr: str,
    ) -> ProgrammaticResponse:
        if not pending:
            await self.state.delete(state.execution_id)
            return ProgrammaticResponse(
                status="error",
                error="sandbox emitted an empty pending tool call block",
                stdout=clean_stdout,
                stderr=stderr,
                session_id=state.session_id,
            )

        registered = {t.name for t in state.tools}
        unregistered = next((p for p in pending if p["tool_name"] not in registered), None)
        if unregistered is not None:
            await self.state.delete(state.execution_id)
            return ProgrammaticResponse(
                status="error",
                error=f"Sandbox requested an unregistered tool: {unregistered['tool_name']}",
                stdout=clean_stdout,
                stderr=stderr,
                session_id=state.session_id,
            )

        for p in pending:
            if p["call_id"] not in state.emitted_call_ids:
                state.emitted_call_ids.append(p["call_id"])
        await self.state.save(state)

        return ProgrammaticResponse(
            status="tool_call_required",
            continuation_token=state.execution_id,
            tool_calls=[ProgrammaticToolCall(id=p["call_id"], name=p["tool_name"], input=p["input"]) for p in pending],
            partial_stdout=clean_stdout or None,
            partial_stderr=stderr or None,
            session_id=state.session_id,
        )
