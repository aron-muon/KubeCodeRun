"""File management API endpoints."""

# Standard library imports
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

# Third-party imports
import structlog
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from unidecode import unidecode

# Local application imports
from ..config import settings
from ..dependencies import FileServiceDep, SessionServiceDep
from ..models.session import SessionCreate, SessionStatus
from ..services.execution.output import OutputProcessor

logger = structlog.get_logger(__name__)
router = APIRouter()


_ASCII_FILENAME_CHARS = "-_.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _ascii_fallback_filename(name: str) -> str:
    """Generate an ASCII-safe fallback filename component."""
    safe_basename = Path(name).name
    transliterated = unidecode(safe_basename)
    transliterated = transliterated.replace(" ", "_")
    sanitized = "".join(ch if ch in _ASCII_FILENAME_CHARS else "_" for ch in transliterated)
    return sanitized or "download"


def _build_content_disposition(filename: str | None, fallback_identifier: str) -> str:
    """Build Content-Disposition header that supports Unicode filenames."""
    default_name = fallback_identifier or "download"
    original_name = Path(filename or default_name).name
    ascii_fallback = _ascii_fallback_filename(original_name)
    encoded_original = quote(original_name, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_original}"


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    entity_id: str | None = Form(None),
    user_id_header: str | None = Header(None, alias="User-Id"),
    x_user_id_header: str | None = Header(None, alias="X-User-Id"),
    file_service: FileServiceDep = None,
    session_service: SessionServiceDep = None,
):
    """Upload files with multipart form handling - LibreChat compatible.

    Accepts files in either 'file' (singular) or 'files' (plural) field names.
    LibreChat uses 'file' while our tests use 'files'.

    user_id resolution (most-trustworthy first):
      1. JWT.sub via request.state.user_id (cryptographically authenticated
         by SecurityMiddleware when codeapi_jwt_enabled).
      2. ``User-Id`` HTTP header (LibreChat 0.8.5 convention).
      3. ``X-User-Id`` HTTP header (X- naming convention).

    The resolved value is persisted on session.metadata.user_id so the
    cross-user session-isolation check (orchestrator._get_or_create_session)
    can prove ownership at exec time.
    """
    jwt_user_id = getattr(request.state, "user_id", None) if request else None
    request_user_id = jwt_user_id or user_id_header or x_user_id_header
    try:
        # Handle both singular and plural field names
        upload_files = []

        # LibreChat sends single file with field name 'file'
        if file is not None:
            upload_files = [file]
        # Tests and other clients may use 'files'
        elif files is not None:
            upload_files = files
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Request validation failed",
                    "error_type": "validation",
                    "details": [
                        {
                            "field": "body -> files",
                            "message": "Field required",
                            "code": "missing",
                        }
                    ],
                },
            )

        # Check file size limits
        for file in upload_files:
            if file.size and file.size > settings.max_file_size_mb * 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {file.filename} exceeds maximum size of {settings.max_file_size_mb}MB",
                )

        # Check number of files limit
        if len(upload_files) > settings.max_files_per_session:
            raise HTTPException(
                status_code=413,
                detail=f"Too many files. Maximum {settings.max_files_per_session} files allowed",
            )

        uploaded_files = []

        # Resolve or create session for this upload.
        # When entity_id is provided, reuse the existing session for that
        # entity so that multiple file uploads land in the same session
        # (fixes issue #34 where separate uploads created isolated sessions).
        #
        # SECURITY: same-user gating mirrors orchestrator._get_or_create_session.
        # Without this, two different users uploading files for the same
        # shared agent would collapse onto a single session and see each
        # other's uploads. Only reuse when the existing session's
        # metadata.user_id matches the current request's User-Id header.
        session_id = None
        if entity_id and request_user_id:
            try:
                existing = await session_service.list_sessions_by_entity(entity_id, limit=10)
                for candidate in existing:
                    if getattr(candidate.status, "value", str(candidate.status)) != "active":
                        continue
                    candidate_user = (candidate.metadata or {}).get("user_id")
                    if candidate_user and candidate_user == request_user_id:
                        session_id = candidate.session_id
                        logger.info(
                            "Reusing existing session for entity (same user)",
                            session_id=session_id,
                            entity_id=entity_id,
                        )
                        break
            except Exception as e:
                logger.warning(
                    "Failed to look up session by entity_id",
                    entity_id=entity_id,
                    error=str(e),
                )

        if not session_id:
            session_metadata: dict = {}
            if entity_id:
                session_metadata["entity_id"] = entity_id
            if request_user_id:
                session_metadata["user_id"] = request_user_id
            session = await session_service.create_session(SessionCreate(metadata=session_metadata))
            session_id = session.session_id

        for file in upload_files:
            # Read file content
            content = await file.read()

            # Sanitize filename before storage so the name on disk in the
            # execution pod matches what LibreChat reports to the model.
            sanitized_name = OutputProcessor.sanitize_filename(file.filename)

            # Store file with the sanitized name
            file_id = await file_service.store_uploaded_file(
                session_id=session_id,
                filename=sanitized_name,
                content=content,
                content_type=file.content_type,
            )

            uploaded_files.append(
                {
                    "id": file_id,
                    "name": sanitized_name,
                    "session_id": session_id,
                    "content": None,  # LibreChat doesn't return content in upload response
                    "size": len(content),
                    "lastModified": datetime.now(UTC).isoformat(),
                    "etag": f'"{file_id}"',
                    "metadata": {
                        "content-type": file.content_type or "application/octet-stream",
                        "original-filename": file.filename,
                    },
                    "contentType": file.content_type or "application/octet-stream",
                }
            )

        logger.info(
            "Files uploaded successfully",
            count=len(uploaded_files),
            entity_id=entity_id,
        )

        # Return LibreChat-compatible response.
        # `storage_session_id` is the field LC 0.8.5 reads
        # (api/server/services/Files/Code/crud.js); `session_id` is dual-
        # emitted for back-compat with older clients.
        return {
            "message": "success",
            "storage_session_id": session_id,
            "session_id": session_id,
            "files": [{"filename": file["name"], "fileId": file["id"]} for file in uploaded_files],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to upload files", error=str(e), entity_id=entity_id)
        raise HTTPException(status_code=500, detail="Failed to upload files")


@router.post("/upload/batch")
async def upload_files_batch(
    request: Request,
    file: list[UploadFile] | None = File(None),
    files: list[UploadFile] | None = File(None),
    entity_id: str | None = Form(None),
    kind: str | None = Form(None),
    id: str | None = Form(None),
    version: str | None = Form(None),
    read_only: str | None = Form(None),
    user_id_header: str | None = Header(None, alias="User-Id"),
    x_user_id_header: str | None = Header(None, alias="X-User-Id"),
    file_service: FileServiceDep = None,
    session_service: SessionServiceDep = None,
):
    """Batch upload endpoint - LibreChat 0.8.5 compatible.

    Used by ``@librechat/agents`` for skill priming (uploading a bundle of
    files in a single request) — see api/server/services/Files/Code/crud.js
    ``batchUploadCodeEnvFiles``. The single-file ``/upload`` works for
    one-at-a-time uploads; this endpoint accepts a multi-file batch and
    returns per-file ``succeeded`` / ``failed`` counts.

    Form fields:

      - ``file`` (or ``files``): one or more file parts (LC uses ``file``).
      - ``entity_id``: optional, mirrors /upload.
      - ``kind``: ``skill`` | ``agent`` | ``user``. LC sends this on every
        batch upload. Treated as a hint; passed back in the response
        envelope but not used for ACL today.
      - ``id``: resource id (skillId / agentId / userId).
      - ``version``: only meaningful with ``kind=skill``.
      - ``read_only``: ``true`` marks every file as infrastructure (skill
        bundle). Accepted but not yet enforced — placeholder for future
        sandbox-side write-protection.

    Returns ``{ message, storage_session_id, session_id, files: [...],
    succeeded, failed }``.

    user_id resolution mirrors ``/upload`` — JWT.sub via
    ``request.state.user_id`` wins over the User-Id / X-User-Id headers.
    """
    jwt_user_id = getattr(request.state, "user_id", None) if request else None
    request_user_id = jwt_user_id or user_id_header or x_user_id_header
    upload_files: list[UploadFile] = file or files or []

    if not upload_files:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Request validation failed",
                "error_type": "validation",
                "details": [{"field": "body -> file", "message": "Field required", "code": "missing"}],
            },
        )

    if len(upload_files) > settings.max_files_per_session:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files in batch. Maximum {settings.max_files_per_session} files allowed",
        )

    # Resolve session (same-user reuse, mirrors /upload).
    session_id = None
    if entity_id and request_user_id:
        try:
            existing = await session_service.list_sessions_by_entity(entity_id, limit=10)
            for candidate in existing:
                if getattr(candidate.status, "value", str(candidate.status)) != "active":
                    continue
                candidate_user = (candidate.metadata or {}).get("user_id")
                if candidate_user and candidate_user == request_user_id:
                    session_id = candidate.session_id
                    break
        except Exception as e:
            logger.warning(
                "Failed to look up batch upload session by entity_id",
                entity_id=entity_id,
                error=str(e),
            )

    if not session_id:
        session_metadata: dict = {}
        if entity_id:
            session_metadata["entity_id"] = entity_id
        if request_user_id:
            session_metadata["user_id"] = request_user_id
        if kind:
            session_metadata["kind"] = kind
        if id:
            session_metadata["resource_id"] = id
        session = await session_service.create_session(SessionCreate(metadata=session_metadata))
        session_id = session.session_id

    results: list[dict] = []
    succeeded = 0
    failed = 0

    for upload in upload_files:
        try:
            if upload.size and upload.size > settings.max_file_size_mb * 1024 * 1024:
                raise ValueError(f"File {upload.filename} exceeds maximum size of {settings.max_file_size_mb}MB")

            content = await upload.read()
            sanitized_name = OutputProcessor.sanitize_filename(upload.filename or "file")
            file_id = await file_service.store_uploaded_file(
                session_id=session_id,
                filename=sanitized_name,
                content=content,
                content_type=upload.content_type,
            )
            results.append(
                {
                    "status": "success",
                    "fileId": file_id,
                    "filename": sanitized_name,
                }
            )
            succeeded += 1
        except Exception as e:  # noqa: BLE001 — per-file isolation for batch
            logger.warning(
                "Batch upload file failed",
                filename=upload.filename,
                error=str(e),
            )
            results.append(
                {
                    "status": "error",
                    "filename": upload.filename,
                    "error": str(e),
                }
            )
            failed += 1

    message = "success" if succeeded > 0 else "error"

    return {
        "message": message,
        "storage_session_id": session_id,
        "session_id": session_id,
        "files": results,
        "succeeded": succeeded,
        "failed": failed,
    }


@router.get("/files/{session_id}")
async def list_files(
    session_id: str,
    detail: str | None = Query(
        None,
        description="Detail level: 'simple' for basic info, otherwise full details",
    ),
    kind: str | None = Query(
        None,
        description="Resource kind filter for LibreChat scoped listing: 'skill', 'agent', or 'user'",
    ),
    id: str | None = Query(
        None,
        description="Resource id for scoped listing (LibreChat agents lib fetchSessionFiles)",
    ),
    version: int | None = Query(
        None,
        description="Resource version (only meaningful when kind=skill)",
    ),
    file_service: FileServiceDep = None,
    session_service: SessionServiceDep = None,
):
    """List all files in a session with optional detail parameter - LibreChat compatible.

    ``kind``/``id``/``version`` query params are accepted for LibreChat 0.8.5
    compatibility (``@librechat/agents`` fetchSessionFiles). They are currently
    pass-through metadata: we do not filter by them server-side because our
    file storage does not yet carry the discriminator. Accepting them avoids
    422 validation errors from LC clients that send the params unconditionally.
    """
    try:
        files = await file_service.list_files(session_id)

        if not files:
            # Return empty array instead of 404
            return []

        if detail == "summary":
            # For summary responses, use a fresh UTC timestamp as
            # lastModified when the session is still active.  LibreChat
            # treats files older than 23 hours as inactive and triggers
            # a re-upload cycle.  Since get_session() refreshes
            # last_activity in Redis for active sessions but returns
            # the pre-refresh model, we use datetime.now(UTC) so the
            # response reflects the just-refreshed activity time.
            # Only do this for ACTIVE sessions — idle/terminated ones
            # should report their actual last_activity.
            session_last_activity = None
            try:
                session = await session_service.get_session(session_id)
                if session:
                    if session.status == SessionStatus.ACTIVE:
                        session_last_activity = datetime.now(UTC)
                    elif session.last_activity:
                        act = session.last_activity
                        if isinstance(act, str):
                            act = datetime.fromisoformat(act)
                        if act.tzinfo is None:
                            act = act.replace(tzinfo=UTC)
                        session_last_activity = act
            except Exception as e:
                logger.warning(
                    "failed_to_fetch_session_last_activity",
                    session_id=session_id,
                    error=str(e),
                )

            summary_files = []
            for file_info in files:
                dt = session_last_activity or file_info.created_at
                # Ensure UTC with 'Z' and millisecond precision
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt)
                    except Exception:
                        dt = datetime.now(UTC)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                last_modified = dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                summary_files.append(
                    {
                        "name": f"{session_id}/{file_info.file_id}",
                        "lastModified": last_modified,
                    }
                )
            return summary_files
        elif detail == "simple":
            # Return simple file information
            simple_files = []
            for file_info in files:
                # Return sanitized filename to match container
                sanitized_name = OutputProcessor.sanitize_filename(file_info.filename)
                simple_files.append(
                    {
                        "id": file_info.file_id,
                        "name": sanitized_name,
                        "path": file_info.path,
                    }
                )
            return simple_files
        else:
            # Return full file details - LibreChat format
            detailed_files = []
            for file_info in files:
                # Return sanitized filename to match container
                sanitized_name = OutputProcessor.sanitize_filename(file_info.filename)
                detailed_files.append(
                    {
                        "name": sanitized_name,
                        "id": file_info.file_id,
                        # `storage_session_id` is the field LC 0.8.5 reads;
                        # `session_id` is dual-emitted for back-compat.
                        "storage_session_id": session_id,
                        "session_id": session_id,
                        "content": None,  # Not returned in list
                        "size": file_info.size,
                        "lastModified": file_info.created_at.isoformat(),
                        "etag": f'"{file_info.file_id}"',
                        "metadata": {
                            "content-type": file_info.content_type,
                            "original-filename": file_info.filename,
                        },
                        "contentType": file_info.content_type,
                    }
                )
            return detailed_files

    except Exception as e:
        logger.error("Failed to list files", session_id=session_id, error=str(e))
        # Return 404 if session not found
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/download/{session_id}/{file_id}")
async def download_file(session_id: str, file_id: str, file_service: FileServiceDep = None):
    """Download a file directly - LibreChat compatible."""
    try:
        # Get file info first
        file_info = await file_service.get_file_info(session_id, file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        # Get file content
        file_content = await file_service.get_file_content(session_id, file_id)
        if file_content is None:
            raise HTTPException(status_code=404, detail="File content not found")

        # Create a generator that yields chunks for proper streaming
        async def generate_chunks():
            chunk_size = 8192  # 8KB chunks
            bytes_remaining = len(file_content)
            offset = 0

            while bytes_remaining > 0:
                chunk_size_to_read = min(chunk_size, bytes_remaining)
                yield file_content[offset : offset + chunk_size_to_read]
                offset += chunk_size_to_read
                bytes_remaining -= chunk_size_to_read

        # Determine content type based on file extension if needed
        content_type = file_info.content_type or "application/octet-stream"
        if content_type == "application/octet-stream" and file_info.filename:
            # Try to guess content type from filename
            import mimetypes

            guessed_type, _ = mimetypes.guess_type(file_info.filename)
            if guessed_type:
                content_type = guessed_type

        content_disposition = _build_content_disposition(file_info.filename, file_info.file_id)

        # Return streaming response WITHOUT Content-Length to force chunked encoding
        return StreamingResponse(
            generate_chunks(),
            media_type=content_type,
            headers={
                "Content-Disposition": content_disposition,
                # DO NOT include Content-Length - this forces chunked transfer encoding
                "Cache-Control": "private, max-age=3600",
                # Add CORS headers for browser compatibility
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "x-api-key, Content-Type",
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to download file",
            session_id=session_id,
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=404, detail="File not found")


@router.options("/download/{session_id}/{file_id}")
async def download_file_options(session_id: str, file_id: str):
    """Handle OPTIONS preflight request for download endpoint."""
    return Response(
        status_code=204,  # No Content
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "x-api-key, Content-Type",
            "Access-Control-Max-Age": "3600",
        },
    )


@router.delete("/files/{session_id}/{file_id}")
async def delete_file(session_id: str, file_id: str, file_service: FileServiceDep = None):
    """Delete a file from the session - LibreChat compatible."""
    try:
        # Get file info before deletion
        file_info = await file_service.get_file_info(session_id, file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        success = await file_service.delete_file(session_id, file_id)

        if success:
            # Return 200 with empty response for LibreChat compatibility
            return Response(status_code=200)
        else:
            raise HTTPException(status_code=500, detail="Failed to delete file")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to delete file",
            session_id=session_id,
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to delete file")
