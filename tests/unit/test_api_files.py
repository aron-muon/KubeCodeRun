"""Unit tests for Files API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Response, UploadFile

from src.api.files import (
    _ascii_fallback_filename,
    _build_content_disposition,
    delete_file,
    download_file,
    download_file_options,
    list_files,
    upload_file,
)


@pytest.fixture
def mock_file_service():
    """Create a mock file service."""
    service = MagicMock()
    service.store_uploaded_file = AsyncMock(return_value="file-123")
    service.list_files = AsyncMock(return_value=[])
    service.get_file_info = AsyncMock(return_value=None)
    service.get_file_content = AsyncMock(return_value=None)
    service.delete_file = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    service = MagicMock()
    mock_session = MagicMock()
    mock_session.session_id = "session-123"
    service.create_session = AsyncMock(return_value=mock_session)
    return service


@pytest.fixture
def mock_upload_file():
    """Create a mock UploadFile."""
    file = MagicMock(spec=UploadFile)
    file.filename = "test.txt"
    file.content_type = "text/plain"
    file.size = 100
    file.read = AsyncMock(return_value=b"test content")
    return file


@pytest.fixture
def mock_file_info():
    """Create a mock file info object."""
    info = MagicMock()
    info.file_id = "file-123"
    info.filename = "test.txt"
    info.path = "/mnt/data/test.txt"
    info.size = 100
    info.content_type = "text/plain"
    info.created_at = datetime.now(UTC)
    return info


class TestAsciiFilename:
    """Tests for _ascii_fallback_filename helper."""

    def test_ascii_filename_simple(self):
        """Test ASCII filename passthrough."""
        result = _ascii_fallback_filename("test.txt")
        assert result == "test.txt"

    def test_ascii_filename_unicode(self):
        """Test Unicode filename transliteration."""
        result = _ascii_fallback_filename("tëst.txt")
        assert "test.txt" in result.lower()

    def test_ascii_filename_spaces(self):
        """Test spaces converted to underscores."""
        result = _ascii_fallback_filename("my file.txt")
        assert result == "my_file.txt"

    def test_ascii_filename_path(self):
        """Test path stripped to just filename."""
        result = _ascii_fallback_filename("/path/to/file.txt")
        assert result == "file.txt"

    def test_ascii_filename_empty(self):
        """Test empty filename returns default."""
        result = _ascii_fallback_filename("")
        assert result == "download"


class TestBuildContentDisposition:
    """Tests for _build_content_disposition helper."""

    def test_simple_filename(self):
        """Test content disposition with simple filename."""
        result = _build_content_disposition("test.txt", "fallback-id")
        assert "test.txt" in result
        assert "attachment" in result

    def test_unicode_filename(self):
        """Test content disposition with Unicode filename."""
        result = _build_content_disposition("tëst.txt", "fallback-id")
        assert "filename*=UTF-8''" in result

    def test_none_filename(self):
        """Test content disposition with None filename."""
        result = _build_content_disposition(None, "fallback-id")
        assert "fallback-id" in result


class TestUploadFile:
    """Tests for upload_file endpoint."""

    @pytest.mark.asyncio
    async def test_upload_single_file(self, mock_file_service, mock_session_service, mock_upload_file):
        """Test uploading a single file."""
        result = await upload_file(
            file=mock_upload_file,
            files=None,
            entity_id="entity-123",
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        assert result["message"] == "success"
        assert result["session_id"] == "session-123"
        assert len(result["files"]) == 1
        mock_file_service.store_uploaded_file.assert_called_once()
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_multiple_files(self, mock_file_service, mock_session_service, mock_upload_file):
        """Test uploading multiple files."""
        files = [mock_upload_file, mock_upload_file]

        result = await upload_file(
            file=None,
            files=files,
            entity_id="entity-123",
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        assert result["message"] == "success"
        assert len(result["files"]) == 2
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_no_files(self, mock_file_service):
        """Test upload with no files raises 422."""
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(
                file=None,
                files=None,
                entity_id="entity-123",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_file_too_large(self, mock_file_service, mock_upload_file):
        """Test upload file too large raises 413."""
        mock_upload_file.size = 500 * 1024 * 1024  # 500MB

        with patch("src.api.files.settings") as mock_settings:
            mock_settings.max_file_size_mb = 100

            with pytest.raises(HTTPException) as exc_info:
                await upload_file(
                    file=mock_upload_file,
                    files=None,
                    entity_id="entity-123",
                    file_service=mock_file_service,
                )

            assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_too_many_files(self, mock_file_service, mock_upload_file):
        """Test upload too many files raises 413."""
        files = [mock_upload_file] * 100

        with patch("src.api.files.settings") as mock_settings:
            mock_settings.max_files_per_session = 10
            mock_settings.max_file_size_mb = 100

            with pytest.raises(HTTPException) as exc_info:
                await upload_file(
                    file=None,
                    files=files,
                    entity_id="entity-123",
                    file_service=mock_file_service,
                )

            assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_file_service_error(self, mock_file_service, mock_session_service, mock_upload_file):
        """Test upload file service error raises 500."""
        mock_file_service.store_uploaded_file.side_effect = Exception("Storage error")

        with patch("src.api.files.settings") as mock_settings:
            mock_settings.max_file_size_mb = 100
            mock_settings.max_files_per_session = 10

            with pytest.raises(HTTPException) as exc_info:
                await upload_file(
                    file=mock_upload_file,
                    files=None,
                    entity_id="entity-123",
                    file_service=mock_file_service,
                    session_service=mock_session_service,
                )

                assert exc_info.value.status_code == 500


class TestListFiles:
    """Tests for list_files endpoint."""

    @pytest.mark.asyncio
    async def test_list_files_empty(self, mock_file_service):
        """Test listing files when none exist."""
        mock_file_service.list_files.return_value = []

        result = await list_files(
            session_id="session-123",
            detail=None,
            file_service=mock_file_service,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_list_files_full_details(self, mock_file_service, mock_file_info):
        """Test listing files with full details."""
        mock_file_service.list_files.return_value = [mock_file_info]

        result = await list_files(
            session_id="session-123",
            detail=None,
            file_service=mock_file_service,
        )

        assert len(result) == 1
        assert result[0]["id"] == "file-123"
        assert "contentType" in result[0]

    @pytest.mark.asyncio
    async def test_list_files_simple(self, mock_file_service, mock_file_info):
        """Test listing files with simple details."""
        mock_file_service.list_files.return_value = [mock_file_info]

        result = await list_files(
            session_id="session-123",
            detail="simple",
            file_service=mock_file_service,
        )

        assert len(result) == 1
        assert "id" in result[0]
        assert "name" in result[0]
        assert "contentType" not in result[0]

    @pytest.mark.asyncio
    async def test_list_files_summary(self, mock_file_service, mock_file_info):
        """Test listing files with summary details."""
        mock_file_service.list_files.return_value = [mock_file_info]

        result = await list_files(
            session_id="session-123",
            detail="summary",
            file_service=mock_file_service,
        )

        assert len(result) == 1
        assert "name" in result[0]
        assert "lastModified" in result[0]

    @pytest.mark.asyncio
    async def test_list_files_error(self, mock_file_service):
        """Test list files error returns 404."""
        mock_file_service.list_files.side_effect = Exception("Not found")

        with pytest.raises(HTTPException) as exc_info:
            await list_files(
                session_id="session-123",
                detail=None,
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 404


class TestDownloadFile:
    """Tests for download_file endpoint."""

    @pytest.mark.asyncio
    async def test_download_file_success(self, mock_file_service, mock_file_info):
        """Test successful file download."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.get_file_content.return_value = b"test content"

        response = await download_file(
            session_id="session-123",
            file_id="file-123",
            file_service=mock_file_service,
        )

        # StreamingResponse should be returned
        assert hasattr(response, "body_iterator")

    @pytest.mark.asyncio
    async def test_download_file_not_found(self, mock_file_service):
        """Test download non-existent file raises 404."""
        mock_file_service.get_file_info.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await download_file(
                session_id="session-123",
                file_id="nonexistent",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_download_file_content_not_found(self, mock_file_service, mock_file_info):
        """Test download when content is missing raises 404."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.get_file_content.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await download_file(
                session_id="session-123",
                file_id="file-123",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_download_file_error(self, mock_file_service, mock_file_info):
        """Test download error raises 404."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.get_file_content.side_effect = Exception("Storage error")

        with pytest.raises(HTTPException) as exc_info:
            await download_file(
                session_id="session-123",
                file_id="file-123",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 404


class TestDownloadFileOptions:
    """Tests for download_file_options endpoint."""

    @pytest.mark.asyncio
    async def test_options_returns_cors_headers(self):
        """Test OPTIONS request returns CORS headers."""
        response = await download_file_options(
            session_id="session-123",
            file_id="file-123",
        )

        assert response.status_code == 204
        assert "Access-Control-Allow-Origin" in response.headers


class TestDeleteFile:
    """Tests for delete_file endpoint."""

    @pytest.mark.asyncio
    async def test_delete_file_success(self, mock_file_service, mock_file_info):
        """Test successful file deletion."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.delete_file.return_value = True

        response = await delete_file(
            session_id="session-123",
            file_id="file-123",
            file_service=mock_file_service,
        )

        assert response.status_code == 200
        mock_file_service.delete_file.assert_called_once_with("session-123", "file-123")

    @pytest.mark.asyncio
    async def test_delete_file_not_found(self, mock_file_service):
        """Test delete non-existent file raises 404."""
        mock_file_service.get_file_info.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await delete_file(
                session_id="session-123",
                file_id="nonexistent",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_file_failed(self, mock_file_service, mock_file_info):
        """Test delete failure raises 500."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.delete_file.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_file(
                session_id="session-123",
                file_id="file-123",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_delete_file_error(self, mock_file_service, mock_file_info):
        """Test delete error raises 500."""
        mock_file_service.get_file_info.return_value = mock_file_info
        mock_file_service.delete_file.side_effect = Exception("Storage error")

        with pytest.raises(HTTPException) as exc_info:
            await delete_file(
                session_id="session-123",
                file_id="file-123",
                file_service=mock_file_service,
            )

        assert exc_info.value.status_code == 500
