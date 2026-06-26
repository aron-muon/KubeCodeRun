"""Tests for the LibreChat 0.8.5 wire contract.

LibreChat (``@librechat/agents`` >= 3.1.74 + packages/api auth refactor) shipped
a contract revision where:

  - upload/list responses must carry ``storage_session_id`` (renamed from
    ``session_id``); we dual-emit both for back-compat.
  - request/response file references gain ``resource_id``, ``kind``,
    ``version`` discriminator fields (``CodeEnvFile`` type).
  - request file refs accept both ``storage_session_id`` (preferred) and
    ``session_id`` (legacy) via Pydantic ``AliasChoices``.
  - ``POST /upload/batch`` exists for skill priming and accepts ``kind``,
    ``id``, ``version``, ``read_only`` form fields.

These tests pin those contract points so a future model refactor cannot
silently regress LibreChat compatibility again.
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.files import get_session_object, list_files, upload_file, upload_files_batch
from src.models.exec import FileRef, RequestFile

# ---------------------------------------------------------------------------
# Model alias / computed-field contract
# ---------------------------------------------------------------------------


class TestRequestFileAliases:
    """RequestFile must accept LibreChat's ``storage_session_id`` field name."""

    def test_accepts_storage_session_id(self):
        rf = RequestFile(id="f", storage_session_id="sess-A", name="x.csv")
        assert rf.session_id == "sess-A"

    def test_accepts_legacy_session_id(self):
        rf = RequestFile(id="f", session_id="sess-A", name="x.csv")
        assert rf.session_id == "sess-A"

    def test_storage_session_id_wins_when_both_provided(self):
        """When both names are present, the preferred (LC 0.8.5) wins."""
        rf = RequestFile.model_validate({"id": "f", "session_id": "old", "storage_session_id": "new", "name": "x"})
        assert rf.session_id == "new"

    def test_resource_id_kind_version_optional(self):
        rf = RequestFile(id="f", session_id="s", name="x")
        assert rf.resource_id is None
        assert rf.kind is None
        assert rf.version is None

    def test_resource_id_kind_version_set(self):
        rf = RequestFile(
            id="f",
            storage_session_id="s",
            name="x",
            resource_id="r1",
            kind="skill",
            version=3,
        )
        assert rf.resource_id == "r1"
        assert rf.kind == "skill"
        assert rf.version == 3


class TestFileRefStorageSessionId:
    """FileRef must emit ``storage_session_id`` (computed alias of session_id)."""

    def test_storage_session_id_mirrors_session_id(self):
        ref = FileRef(id="f", name="out.png", session_id="sess-A")
        dumped = ref.model_dump()
        assert dumped["storage_session_id"] == "sess-A"
        assert dumped["session_id"] == "sess-A"

    def test_storage_session_id_none_when_session_id_none(self):
        ref = FileRef(id="f", name="out.png")
        dumped = ref.model_dump()
        assert dumped["storage_session_id"] is None
        assert dumped["session_id"] is None

    def test_resource_id_kind_version_round_trip(self):
        ref = FileRef(
            id="f",
            name="out.png",
            session_id="sess-A",
            resource_id="r1",
            kind="skill",
            version=2,
        )
        dumped = ref.model_dump()
        assert dumped["resource_id"] == "r1"
        assert dumped["kind"] == "skill"
        assert dumped["version"] == 2

    def test_resource_id_kind_version_excluded_when_none(self):
        ref = FileRef(id="f", name="out.png", session_id="sess-A")
        dumped = ref.model_dump(exclude_none=True)
        assert "resource_id" not in dumped
        assert "kind" not in dumped
        assert "version" not in dumped


class TestFileRefInherited:
    """``inherited`` is emitted inside each files[] entry so LibreChat's host
    (callbacks.js / tools.js ``if (file.inherited) continue``) and
    @librechat/agents (CodeSessionFileSummary ``file.inherited !== true``) can
    skip read-only passthroughs when building user downloads and the model
    summary. The flag lives on the file ref, not a separate response field."""

    def test_inherited_defaults_false(self):
        ref = FileRef(id="f", name="out.png", session_id="sess-A")
        assert ref.model_dump()["inherited"] is False

    def test_inherited_true_round_trips(self):
        ref = FileRef(id="skill-1", name="skillName/SKILL.md", session_id="sess-A", inherited=True)
        dumped = ref.model_dump()
        assert dumped["inherited"] is True
        assert dumped["id"] == "skill-1"
        assert dumped["name"] == "skillName/SKILL.md"


# ---------------------------------------------------------------------------
# Endpoint contract — /upload, /upload/batch, /files/{session_id}
# ---------------------------------------------------------------------------


def _mock_session(session_id: str = "new-session"):
    session = MagicMock()
    session.session_id = session_id
    return session


def _mock_file(filename: str = "f.csv", content: bytes = b"x"):
    f = MagicMock()
    f.filename = filename
    f.content_type = "text/csv"
    f.size = len(content)
    f.read = AsyncMock(return_value=content)
    return f


def _anon_http_request():
    """Anonymous HTTP request (no JWT-resolved user_id).

    upload_file/upload_files_batch read ``request.state.user_id`` first
    (set by SecurityMiddleware when JWT auth is enabled). Contract tests
    don't exercise that path; explicit None forces resolution to fall
    through to the User-Id / X-User-Id headers as before.
    """
    request = MagicMock()
    request.state = MagicMock()
    request.state.user_id = None
    return request


class TestUploadResponseShape:
    @pytest.mark.asyncio
    async def test_upload_emits_storage_session_id(self):
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-1"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-1")

        result = await upload_file(
            request=_anon_http_request(),
            file=_mock_file(),
            files=None,
            entity_id=None,
            kind=None,
            id=None,
            version=None,
            read_only=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert result["storage_session_id"] == "sess-1"
        # Dual-emit: session_id still present for back-compat.
        assert result["session_id"] == "sess-1"
        assert result["files"][0]["fileId"] == "fid-1"

    @pytest.mark.asyncio
    async def test_upload_preserves_nested_skill_path(self):
        """Skill bundles upload files whose multipart filename carries a
        relative path (e.g. ``skillName/SKILL.md`` — see packages/api
        .../form.ts ``getCodeEnvFileOptions``). The stored name must keep the
        directory structure instead of being flattened to a basename, so the
        skill's relative references resolve under /mnt/data."""
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-1"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-1")

        result = await upload_file(
            request=_anon_http_request(),
            file=_mock_file(filename="skillName/SKILL.md"),
            files=None,
            entity_id=None,
            kind=None,
            id=None,
            version=None,
            read_only=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        stored_name = file_service.store_uploaded_file.call_args.kwargs["filename"]
        assert stored_name == "skillName/SKILL.md"
        assert result["files"][0]["filename"] == "skillName/SKILL.md"

    @pytest.mark.asyncio
    async def test_upload_threads_identity_fields(self):
        """Single /upload accepts kind/id/version/read_only like /upload/batch:
        ``read_only`` is persisted on the file and ``kind`` / ``id`` land on
        session metadata."""
        from src.models.session import SessionCreate

        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-1"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-1")

        await upload_file(
            request=_anon_http_request(),
            file=_mock_file(filename="skillName/SKILL.md"),
            files=None,
            entity_id=None,
            kind="skill",
            id="skill-123",
            version="3",
            read_only="true",
            user_id_header="user-A",
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert file_service.store_uploaded_file.call_args.kwargs["read_only"] is True
        sc: SessionCreate = session_service.create_session.call_args.args[0]
        assert sc.metadata.get("kind") == "skill"
        assert sc.metadata.get("resource_id") == "skill-123"

    @pytest.mark.asyncio
    async def test_upload_read_only_defaults_false(self):
        """Without identity fields, single /upload defaults read_only=False."""
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-1"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-1")

        await upload_file(
            request=_anon_http_request(),
            file=_mock_file(),
            files=None,
            entity_id=None,
            kind=None,
            id=None,
            version=None,
            read_only=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert file_service.store_uploaded_file.call_args.kwargs["read_only"] is False


class TestUploadBatchEndpoint:
    @pytest.mark.asyncio
    async def test_batch_returns_storage_session_id_and_counts(self):
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-b"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(side_effect=["fid-a", "fid-b"])

        files = [_mock_file("a.csv", b"a"), _mock_file("b.csv", b"b")]

        result = await upload_files_batch(
            request=_anon_http_request(),
            file=files,
            files=None,
            entity_id=None,
            kind="skill",
            id="skill-123",
            version="3",
            read_only="true",
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert result["storage_session_id"] == "sess-b"
        assert result["session_id"] == "sess-b"
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert {f["fileId"] for f in result["files"]} == {"fid-a", "fid-b"}

    @pytest.mark.asyncio
    async def test_batch_read_only_threaded_to_storage(self):
        """``read_only=true`` is persisted on each stored file so the executor
        can echo it as an inherited input instead of a generated artifact."""
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-b"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(side_effect=["fid-a", "fid-b"])

        await upload_files_batch(
            request=_anon_http_request(),
            file=[_mock_file("skillName/SKILL.md", b"x"), _mock_file("skillName/run.py", b"y")],
            files=None,
            entity_id=None,
            kind="skill",
            id="skill-123",
            version="3",
            read_only="true",
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert file_service.store_uploaded_file.await_count == 2
        assert all(c.kwargs["read_only"] is True for c in file_service.store_uploaded_file.await_args_list)

    @pytest.mark.asyncio
    async def test_batch_read_only_defaults_false(self):
        """Without ``read_only``, stored files default to writable (read_only=False)."""
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-b"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-a")

        await upload_files_batch(
            request=_anon_http_request(),
            file=[_mock_file("data.csv", b"x")],
            files=None,
            entity_id=None,
            kind="user",
            id=None,
            version=None,
            read_only=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert file_service.store_uploaded_file.await_args.kwargs["read_only"] is False

    @pytest.mark.asyncio
    async def test_batch_kind_skill_persists_on_session_metadata(self):
        """``kind`` / ``id`` form fields land on session.metadata so the
        session can later be filtered by skill/resource."""
        from src.models.session import SessionCreate

        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-b"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(return_value="fid-a")

        await upload_files_batch(
            request=_anon_http_request(),
            file=[_mock_file("a.toml", b"[tool]")],
            files=None,
            entity_id=None,
            kind="skill",
            id="skill-123",
            version="2",
            read_only="true",
            user_id_header="user-A",
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        call = session_service.create_session.call_args
        sc: SessionCreate = call.args[0]
        assert sc.metadata.get("kind") == "skill"
        assert sc.metadata.get("resource_id") == "skill-123"
        assert sc.metadata.get("user_id") == "user-A"

    @pytest.mark.asyncio
    async def test_batch_per_file_failure_does_not_abort_batch(self):
        """One file failing must not torpedo the whole batch — succeeded
        files still get committed, failed ones surface in `failed`."""
        session_service = MagicMock()
        session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        session_service.create_session = AsyncMock(return_value=_mock_session("sess-b"))

        file_service = MagicMock()
        file_service.store_uploaded_file = AsyncMock(side_effect=["fid-a", Exception("MinIO down"), "fid-c"])

        result = await upload_files_batch(
            request=_anon_http_request(),
            file=[
                _mock_file("a.csv", b"a"),
                _mock_file("b.csv", b"b"),
                _mock_file("c.csv", b"c"),
            ],
            files=None,
            entity_id=None,
            kind=None,
            id=None,
            version=None,
            read_only=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert result["succeeded"] == 2
        assert result["failed"] == 1
        statuses = [f["status"] for f in result["files"]]
        assert statuses == ["success", "error", "success"]

    @pytest.mark.asyncio
    async def test_batch_empty_returns_422(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await upload_files_batch(
                request=_anon_http_request(),
                file=None,
                files=None,
                entity_id=None,
                kind=None,
                id=None,
                version=None,
                read_only=None,
                user_id_header=None,
                x_user_id_header=None,
                file_service=MagicMock(),
                session_service=MagicMock(),
            )
        assert exc.value.status_code == 422


class TestListFilesContract:
    @pytest.mark.asyncio
    async def test_list_files_accepts_kind_id_version_query_params(self):
        """The kind/id/version params are pass-through (no server-side
        filtering today) but MUST be accepted so LC's fetchSessionFiles
        does not 422."""
        file_service = MagicMock()
        file_service.list_files = AsyncMock(return_value=[])
        session_service = MagicMock()

        # No exception, returns empty list cleanly.
        result = await list_files(
            session_id="sess-1",
            detail=None,
            kind="skill",
            id="skill-123",
            version=2,
            file_service=file_service,
            session_service=session_service,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_files_detail_full_emits_storage_session_id(self):
        from datetime import datetime

        from src.models.files import FileInfo

        info = FileInfo(
            file_id="fid",
            filename="data.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data.csv",
        )
        file_service = MagicMock()
        file_service.list_files = AsyncMock(return_value=[info])
        session_service = MagicMock()

        result = await list_files(
            session_id="sess-1",
            detail=None,
            kind=None,
            id=None,
            version=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert len(result) == 1
        assert result[0]["storage_session_id"] == "sess-1"
        assert result[0]["session_id"] == "sess-1"


class TestGetSessionObject:
    """Tests for GET /sessions/{session_id}/objects/{file_id}.

    LibreChat's ``getSessionInfo`` (process.js) calls this endpoint to check
    whether a previously-uploaded file is still active.  It expects a JSON
    response with ``lastModified``.  A 404 triggers a re-upload cycle.
    """

    @pytest.mark.asyncio
    async def test_returns_last_modified_for_existing_file(self):
        from datetime import UTC, datetime

        from src.models.files import FileInfo
        from src.models.session import Session, SessionStatus

        file_info = FileInfo(
            file_id="file-abc",
            filename="data.csv",
            size=42,
            content_type="text/csv",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            path="/data.csv",
        )
        file_service = MagicMock()
        file_service.get_file_info = AsyncMock(return_value=file_info)

        session = MagicMock()
        session.status = SessionStatus.ACTIVE
        session.last_activity = datetime(2026, 1, 2, tzinfo=UTC)

        session_service = MagicMock()
        session_service.get_session = AsyncMock(return_value=session)

        result = await get_session_object(
            session_id="sess-1",
            file_id="file-abc",
            kind="user",
            resource_id="user-123",
            version=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert "lastModified" in result
        assert result["name"] == "sess-1/file-abc"
        # Active session should yield a timestamp close to now (not file's created_at)
        assert result["lastModified"].endswith("Z")
        parsed = datetime.fromisoformat(result["lastModified"].replace("Z", "+00:00"))
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_returns_404_when_file_not_found(self):
        from fastapi import HTTPException

        file_service = MagicMock()
        file_service.get_file_info = AsyncMock(return_value=None)
        session_service = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await get_session_object(
                session_id="sess-1",
                file_id="nonexistent",
                kind="user",
                resource_id="user-123",
                version=None,
                file_service=file_service,
                session_service=session_service,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_accepts_kind_id_version_params_without_error(self):
        """The kind/id/version query params must be accepted (no 422)."""
        from datetime import UTC, datetime

        from src.models.files import FileInfo

        file_info = FileInfo(
            file_id="fid",
            filename="script.py",
            size=10,
            content_type="text/x-python",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
            path="/script.py",
        )
        file_service = MagicMock()
        file_service.get_file_info = AsyncMock(return_value=file_info)
        session_service = MagicMock()
        session_service.get_session = AsyncMock(return_value=None)

        result = await get_session_object(
            session_id="s1",
            file_id="fid",
            kind="skill",
            resource_id="skill-42",
            version=3,
            file_service=file_service,
            session_service=session_service,
        )

        assert "lastModified" in result

    @pytest.mark.asyncio
    async def test_returns_last_activity_for_inactive_session(self):
        """Inactive session with last_activity should use that timestamp."""
        from datetime import UTC, datetime

        from src.models.files import FileInfo
        from src.models.session import Session, SessionStatus

        file_info = FileInfo(
            file_id="file-abc",
            filename="data.csv",
            size=42,
            content_type="text/csv",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            path="/data.csv",
        )
        file_service = MagicMock()
        file_service.get_file_info = AsyncMock(return_value=file_info)

        session = MagicMock()
        session.status = SessionStatus.TERMINATED
        session.last_activity = datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC)

        session_service = MagicMock()
        session_service.get_session = AsyncMock(return_value=session)

        result = await get_session_object(
            session_id="sess-1",
            file_id="file-abc",
            kind="user",
            resource_id="user-123",
            version=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert result["lastModified"] == "2026-03-15T10:30:00.000Z"


class TestProgrammaticToolCallingContract:
    """Wire contract for ``POST /exec/programmatic`` (@librechat/agents PTC).

    The agents ``ProgrammaticToolCalling`` client POSTs ``{code, tools, ...}``
    (initial) or ``{continuation_token, tool_results}`` (continuation) and reads
    ``{status, tool_calls:[{id,name,input}], continuation_token}`` /
    ``{status:'completed', stdout, stderr, files}``.
    """

    def test_language_lang_alias_normalizes(self):
        from src.models.programmatic import ProgrammaticRequest

        # bash client sends `lang`; canonical field is `language`
        assert ProgrammaticRequest(code="x", lang="bash").resolved_language == "bash"
        # `language` wins when both present
        assert ProgrammaticRequest(code="x", language="python", lang="bash").resolved_language == "python"
        # default is python
        assert ProgrammaticRequest(code="x").resolved_language == "python"

    def test_continuation_detection(self):
        from src.models.programmatic import ProgrammaticRequest, ToolResultIn

        initial = ProgrammaticRequest(code="x", tools=[])
        assert initial.is_continuation is False
        cont = ProgrammaticRequest(continuation_token="tok", tool_results=[ToolResultIn(call_id="call_001", result=1)])
        assert cont.is_continuation is True

    def test_tool_result_shape(self):
        from src.models.programmatic import ToolResultIn

        tr = ToolResultIn(call_id="call_001", result={"k": "v"}, is_error=False)
        assert tr.call_id == "call_001"
        assert tr.result == {"k": "v"}
        assert tr.is_error is False
        # error result carries a message
        err = ToolResultIn(call_id="call_002", result=None, is_error=True, error_message="boom")
        assert err.is_error is True
        assert err.error_message == "boom"

    def test_tool_call_out_shape(self):
        from src.models.programmatic import ProgrammaticToolCall

        tc = ProgrammaticToolCall(id="call_001", name="get_weather", input={"city": "SF"})
        dumped = tc.model_dump()
        assert dumped == {"id": "call_001", "name": "get_weather", "input": {"city": "SF"}}

    def test_response_status_values(self):
        from src.models.programmatic import ProgrammaticResponse

        assert ProgrammaticResponse(status="tool_call_required").status == "tool_call_required"
        assert ProgrammaticResponse(status="completed").status == "completed"
        assert ProgrammaticResponse(status="error").status == "error"

    def test_completed_response_carries_files_and_session(self):
        from src.models.programmatic import ProgrammaticResponse

        resp = ProgrammaticResponse(
            status="completed",
            stdout="done",
            stderr="",
            files=[FileRef(id="f1", name="out.csv", session_id="s1")],
            session_id="s1",
        )
        dumped = resp.model_dump()
        assert dumped["status"] == "completed"
        assert dumped["files"][0]["storage_session_id"] == "s1"
        assert dumped["session_id"] == "s1"
