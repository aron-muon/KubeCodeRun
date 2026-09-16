"""Programmatic Tool Calling endpoint compatible with @librechat/agents.

``POST /exec/programmatic`` runs the stateless replay loop: the model's code
calls tools as async functions; the sandbox replays prior results from an
injected history file and surfaces the first un-cached call, which LibreChat
executes and feeds back via ``continuation_token`` + ``tool_results``.

Auth + user_id resolution mirror ``/exec`` (JWT.sub > body > User-Id header).
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..dependencies.services import (
    ExecutionServiceDep,
    FileServiceDep,
    SessionServiceDep,
    StateArchivalServiceDep,
    StateServiceDep,
)
from ..models.programmatic import ProgrammaticRequest, ProgrammaticResponse
from ..services.orchestrator import ExecutionOrchestrator
from ..services.programmatic import ProgrammaticError, ProgrammaticService
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/exec/programmatic", response_model=ProgrammaticResponse)
async def execute_programmatic(
    request: ProgrammaticRequest,
    http_request: Request,
    session_service: SessionServiceDep,
    file_service: FileServiceDep,
    execution_service: ExecutionServiceDep,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
):
    """Run one Programmatic Tool Calling iteration (replay mode).

    The body is either an **initial** request (``code`` + ``tools``) or a
    **continuation** (``continuation_token`` + ``tool_results``). Returns
    ``tool_call_required`` (with the next tool call) or ``completed``.
    """
    request_id = generate_request_id()[:8]

    api_key_hash = getattr(http_request.state, "api_key_hash", None)
    is_env_key = getattr(http_request.state, "is_env_key", False)

    # Resolve user_id: JWT.sub (verified) > body > User-Id/X-User-Id header.
    jwt_user_id = getattr(http_request.state, "user_id", None)
    user_id = jwt_user_id or request.user_id
    if not user_id:
        user_id = http_request.headers.get("user-id") or http_request.headers.get("x-user-id")

    logger.info(
        "Programmatic execution request",
        request_id=request_id,
        language=request.resolved_language,
        is_continuation=request.is_continuation,
        tool_count=len(request.tools or []),
        user_id=user_id,
    )

    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=file_service,
        execution_service=execution_service,
        state_service=state_service,
        state_archival_service=state_archival_service,
    )
    service = ProgrammaticService(orchestrator)

    try:
        response = await service.execute(
            request,
            user_id=user_id,
            request_id=request_id,
            api_key_hash=api_key_hash,
            is_env_key=is_env_key,
        )
    except ProgrammaticError as exc:
        logger.warning(
            "Programmatic request rejected", request_id=request_id, status=exc.status_code, error=exc.message
        )
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    logger.info(
        "Programmatic execution response",
        request_id=request_id,
        status=response.status,
        session_id=response.session_id,
    )
    return response
