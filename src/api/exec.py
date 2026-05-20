"""Code execution API endpoint compatible with LibreChat API.

This is a thin endpoint that delegates to ExecutionOrchestrator for
the actual execution workflow logic.
"""

import structlog
from fastapi import APIRouter, Request

from ..dependencies.services import (
    ExecutionServiceDep,
    FileServiceDep,
    SessionServiceDep,
    StateArchivalServiceDep,
    StateServiceDep,
)
from ..models import ExecRequest, ExecResponse
from ..services.orchestrator import ExecutionOrchestrator
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/exec", response_model=ExecResponse)
async def execute_code(
    request: ExecRequest,
    http_request: Request,
    session_service: SessionServiceDep,
    file_service: FileServiceDep,
    execution_service: ExecutionServiceDep,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
):
    """Execute code with specified language and parameters.

    This endpoint is compatible with LibreChat's Code Interpreter API.
    It supports 12 programming languages: py, js, ts, go, java, c, cpp, php, rs, r, f90, d

    Python sessions support state persistence - variables and functions defined in
    one execution are available in subsequent executions within the same session.
    State is stored in Redis (2 hour TTL) with automatic archival to MinIO for
    long-term storage (7 day TTL).

    Args:
        request: Execution request with code, language, and optional files
        http_request: HTTP request for accessing state (api_key_hash)
        session_service: Session management service
        file_service: File storage service
        execution_service: Code execution service
        state_service: Python state persistence service (Redis)
        state_archival_service: Python state archival service (MinIO)

    Returns:
        ExecResponse with session_id, stdout, stderr, and generated files
    """
    request_id = generate_request_id()[:8]

    # Get API key info from request state (set by SecurityMiddleware)
    api_key_hash = getattr(http_request.state, "api_key_hash", None)
    is_env_key = getattr(http_request.state, "is_env_key", False)

    # Resolve user_id from the most-trustworthy source available, in order:
    #
    #   1. JWT.sub (request.state.user_id) — set by SecurityMiddleware
    #      after verifying a CodeAPI JWT signed by LibreChat. This is
    #      cryptographically authenticated and OVERRIDES whatever the body
    #      or User-Id header says. Without this override, an attacker
    #      holding a valid JWT for user-A could still pass user_id=B in
    #      the body and operate against user-B's sessions.
    #   2. ExecRequest.user_id (request body) — direct-API integration.
    #   3. User-Id / X-User-Id HTTP header — LibreChat 0.8.5 sends this on
    #      every code-interpreter call (api/server/services/Files/Code/
    #      crud.js). Unsigned; only trusted in the absence of (1)/(2).
    #
    # Without this resolution chain, every LC request reaches the
    # orchestrator with request.user_id=None and the cross-user session-
    # isolation guard forces a fresh session every time, breaking same-
    # user file continuity.
    jwt_user_id = getattr(http_request.state, "user_id", None)
    if jwt_user_id:
        request.user_id = jwt_user_id
    elif not request.user_id:
        header_user_id = http_request.headers.get("user-id") or http_request.headers.get("x-user-id")
        if header_user_id:
            request.user_id = header_user_id

    logger.info(
        "Code execution request",
        request_id=request_id,
        language=request.lang,
        code_length=len(request.code),
        entity_id=request.entity_id,
        user_id=request.user_id,
        api_key_hash=api_key_hash[:8] if api_key_hash else "unknown",
    )

    # Create orchestrator with injected services
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=file_service,
        execution_service=execution_service,
        state_service=state_service,
        state_archival_service=state_archival_service,
    )

    # Execute via orchestrator (handles validation, session, files, execution, cleanup)
    response = await orchestrator.execute(request, request_id, api_key_hash=api_key_hash, is_env_key=is_env_key)

    logger.info(
        "Code execution completed",
        request_id=request_id,
        session_id=response.session_id,
    )

    return response
