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

from src.api.files import list_files, upload_file, upload_files_batch
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
            user_id_header=None,
            x_user_id_header=None,
            file_service=file_service,
            session_service=session_service,
        )

        assert result["storage_session_id"] == "sess-1"
        # Dual-emit: session_id still present for back-compat.
        assert result["session_id"] == "sess-1"
        assert result["files"][0]["fileId"] == "fid-1"


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
