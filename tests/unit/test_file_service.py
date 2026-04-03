"""Unit tests for File Service."""

import asyncio
from datetime import datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from minio.error import S3Error

from src.models import FileInfo


@pytest.fixture
def mock_minio_client():
    """Create a mock MinIO client."""
    client = MagicMock()
    client.bucket_exists = MagicMock(return_value=True)
    client.make_bucket = MagicMock()
    client.put_object = MagicMock()
    client.get_object = MagicMock()
    client.remove_object = MagicMock()
    client.list_objects = MagicMock(return_value=[])
    return client


@pytest.fixture
def mock_redis_client():
    """Create a mock async Redis client."""
    client = AsyncMock()
    client.hset = AsyncMock()
    client.hgetall = AsyncMock(return_value={})
    client.expire = AsyncMock()
    client.sadd = AsyncMock()
    client.smembers = AsyncMock(return_value=set())
    client.srem = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def file_service(mock_minio_client, mock_redis_client):
    """Create a file service with mocked dependencies."""
    with patch("src.services.file.settings") as mock_settings:
        mock_minio_config = MagicMock()
        mock_minio_config.create_client.return_value = mock_minio_client
        mock_settings.minio = mock_minio_config
        mock_settings.get_redis_url.return_value = "redis://localhost:6379"
        mock_settings.minio_bucket = "test-bucket"
        mock_settings.get_session_ttl_minutes.return_value = 60

        with patch("src.services.file.redis.from_url", return_value=mock_redis_client):
            from src.services.file import FileService

            service = FileService()
            service.minio_client = mock_minio_client
            service.redis_client = mock_redis_client
            return service


class TestFileServiceInit:
    """Tests for FileService initialization."""

    def test_init(self, file_service):
        """Test service initialization."""
        assert file_service.bucket_name == "test-bucket"


class TestGetFileKey:
    """Tests for _get_file_key method."""

    def test_get_file_key_uploads(self, file_service):
        """Test generating file key for uploads."""
        key = file_service._get_file_key("session-123", "file-456")
        assert key == "sessions/session-123/uploads/file-456"

    def test_get_file_key_outputs(self, file_service):
        """Test generating file key for outputs."""
        key = file_service._get_file_key("session-123", "file-456", "outputs")
        assert key == "sessions/session-123/outputs/file-456"


class TestGetFileMetadataKey:
    """Tests for _get_file_metadata_key method."""

    def test_get_metadata_key(self, file_service):
        """Test generating metadata key."""
        key = file_service._get_file_metadata_key("session-123", "file-456")
        assert key == "files:session-123:file-456"


class TestGetSessionFilesKey:
    """Tests for _get_session_files_key method."""

    def test_get_session_files_key(self, file_service):
        """Test generating session files key."""
        key = file_service._get_session_files_key("session-123")
        assert key == "session_files:session-123"


class TestEnsureBucketExists:
    """Tests for _ensure_bucket_exists method."""

    @pytest.mark.asyncio
    async def test_ensure_bucket_exists_already_exists(self, file_service, mock_minio_client):
        """Test when bucket already exists."""
        mock_minio_client.bucket_exists.return_value = True

        await file_service._ensure_bucket_exists()

        mock_minio_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_minio_client.make_bucket.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_bucket_creates_if_missing(self, file_service, mock_minio_client):
        """Test bucket creation when missing."""
        mock_minio_client.bucket_exists.return_value = False

        await file_service._ensure_bucket_exists()

        mock_minio_client.make_bucket.assert_called_once_with("test-bucket")

    @pytest.mark.asyncio
    async def test_ensure_bucket_handles_s3_error(self, file_service, mock_minio_client):
        """Test handling S3 error."""
        mock_minio_client.bucket_exists.side_effect = S3Error(
            "Error", "BucketNotFound", "resource", "request_id", "host_id", "response"
        )

        with pytest.raises(S3Error):
            await file_service._ensure_bucket_exists()


class TestStoreFileMetadata:
    """Tests for _store_file_metadata method."""

    @pytest.mark.asyncio
    async def test_store_metadata_success(self, file_service, mock_redis_client):
        """Test successful metadata storage."""
        metadata = {"filename": "test.txt", "size": 1024}

        await file_service._store_file_metadata("session-123", "file-456", metadata)

        mock_redis_client.hset.assert_called_once()
        mock_redis_client.sadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_metadata_sets_ttl(self, file_service, mock_redis_client):
        """Test that TTL is set on metadata when session TTL > 0."""
        metadata = {"filename": "test.txt", "size": 1024}

        with patch("src.services.file.settings") as mock_settings:
            mock_settings.get_session_ttl_minutes.return_value = 60  # 1 hour
            await file_service._store_file_metadata("session-123", "file-456", metadata)

        # expire should be called for both metadata and session files list
        assert mock_redis_client.expire.call_count == 2

    @pytest.mark.asyncio
    async def test_store_metadata_skips_ttl_when_infinite(self, file_service, mock_redis_client):
        """Test that TTL is not set when session TTL is 0 (infinite)."""
        metadata = {"filename": "test.txt", "size": 1024}

        with patch("src.services.file.settings") as mock_settings:
            mock_settings.get_session_ttl_minutes.return_value = 0
            await file_service._store_file_metadata("session-123", "file-456", metadata)

        # expire should NOT be called
        mock_redis_client.expire.assert_not_called()


class TestGetFileMetadata:
    """Tests for _get_file_metadata method."""

    @pytest.mark.asyncio
    async def test_get_metadata_success(self, file_service, mock_redis_client):
        """Test successful metadata retrieval."""
        mock_redis_client.hgetall.return_value = {
            "filename": "test.txt",
            "size": "1024",
            "created_at": "2024-01-01T00:00:00",
        }

        result = await file_service._get_file_metadata("session-123", "file-456")

        assert result is not None
        assert result["filename"] == "test.txt"
        assert result["size"] == 1024

    @pytest.mark.asyncio
    async def test_get_metadata_not_found(self, file_service, mock_redis_client):
        """Test when metadata not found."""
        mock_redis_client.hgetall.return_value = {}

        result = await file_service._get_file_metadata("session-123", "file-456")

        assert result is None


class TestDeleteFileMetadata:
    """Tests for _delete_file_metadata method."""

    @pytest.mark.asyncio
    async def test_delete_metadata(self, file_service, mock_redis_client):
        """Test metadata deletion."""
        await file_service._delete_file_metadata("session-123", "file-456")

        mock_redis_client.srem.assert_called_once()
        mock_redis_client.delete.assert_called_once()


class TestUploadFile:
    """Tests for upload_file method."""

    @pytest.mark.asyncio
    async def test_upload_file_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful file upload - generates presigned URL."""
        from src.models import FileUploadRequest

        mock_minio_client.bucket_exists.return_value = True
        mock_minio_client.presigned_put_object = MagicMock(return_value="https://presigned-url")

        request = FileUploadRequest(filename="test.txt", content_type="text/plain", size=1024)

        with patch("src.services.file.generate_file_id", return_value="file-123"):
            file_id, upload_url = await file_service.upload_file(
                session_id="session-123",
                request=request,
            )

        assert file_id == "file-123"
        assert upload_url is not None


class TestGetFileInfo:
    """Tests for get_file_info method."""

    @pytest.mark.asyncio
    async def test_get_file_info_success(self, file_service, mock_redis_client):
        """Test successful file info retrieval."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "size": "1024",
            "content_type": "text/plain",
            "path": "/mnt/data/test.txt",
            "created_at": "2024-01-01T00:00:00",
        }

        result = await file_service.get_file_info("session-123", "file-456")

        assert result is not None
        assert result.filename == "test.txt"

    @pytest.mark.asyncio
    async def test_get_file_info_not_found(self, file_service, mock_redis_client):
        """Test when file info not found."""
        mock_redis_client.hgetall.return_value = {}

        result = await file_service.get_file_info("session-123", "file-456")

        assert result is None


class TestDownloadFile:
    """Tests for download_file method - generates presigned URL."""

    @pytest.mark.asyncio
    async def test_download_file_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful file download URL generation."""
        # Setup metadata with object_key
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "size": "12",
            "content_type": "text/plain",
            "object_key": "sessions/session-123/uploads/file-456",
            "path": "/mnt/data/test.txt",
            "created_at": "2024-01-01T00:00:00",
        }

        mock_minio_client.presigned_get_object = MagicMock(return_value="https://download-url")

        download_url = await file_service.download_file("session-123", "file-456")

        assert download_url is not None
        assert "https" in download_url or download_url == "https://download-url"

    @pytest.mark.asyncio
    async def test_download_file_not_found(self, file_service, mock_redis_client):
        """Test download when file not found."""
        mock_redis_client.hgetall.return_value = {}

        result = await file_service.download_file("session-123", "file-456")

        assert result is None


class TestDeleteFile:
    """Tests for delete_file method."""

    @pytest.mark.asyncio
    async def test_delete_file_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful file deletion."""
        # Setup metadata with object_key
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "size": "1024",
            "object_key": "sessions/session-123/uploads/file-456",
        }

        result = await file_service.delete_file("session-123", "file-456")

        assert result is True
        mock_minio_client.remove_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_file_not_found(self, file_service, mock_redis_client):
        """Test delete when file not found."""
        mock_redis_client.hgetall.return_value = {}

        result = await file_service.delete_file("session-123", "file-456")

        assert result is False


class TestListFiles:
    """Tests for list_files method."""

    @pytest.mark.asyncio
    async def test_list_files_success(self, file_service, mock_redis_client):
        """Test successful file listing."""
        mock_redis_client.smembers.return_value = {"file-1", "file-2"}
        mock_redis_client.hgetall.side_effect = [
            {
                "file_id": "file-1",
                "filename": "file1.txt",
                "size": "1024",
                "content_type": "text/plain",
                "path": "/mnt/data/file1.txt",
                "created_at": "2024-01-01T00:00:00",
            },
            {
                "file_id": "file-2",
                "filename": "file2.txt",
                "size": "2048",
                "content_type": "text/plain",
                "path": "/mnt/data/file2.txt",
                "created_at": "2024-01-01T00:00:00",
            },
        ]

        result = await file_service.list_files("session-123")

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_files_empty(self, file_service, mock_redis_client):
        """Test listing when no files."""
        mock_redis_client.smembers.return_value = set()

        result = await file_service.list_files("session-123")

        assert result == []


class TestCleanupSessionFiles:
    """Tests for cleanup_session_files method."""

    @pytest.mark.asyncio
    async def test_cleanup_session_files(self, file_service, mock_redis_client, mock_minio_client):
        """Test cleaning up all session files."""
        mock_redis_client.smembers.return_value = {"file-1", "file-2"}
        mock_redis_client.hgetall.side_effect = [
            {
                "file_id": "file-1",
                "filename": "file1.txt",
                "size": "1024",
                "object_key": "sessions/session-123/uploads/file-1",
            },
            {
                "file_id": "file-2",
                "filename": "file2.txt",
                "size": "2048",
                "object_key": "sessions/session-123/uploads/file-2",
            },
        ]

        count = await file_service.cleanup_session_files("session-123")

        assert count == 2
        assert mock_minio_client.remove_object.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_session_files_empty(self, file_service, mock_redis_client):
        """Test cleanup when no files."""
        mock_redis_client.smembers.return_value = set()

        count = await file_service.cleanup_session_files("session-123")

        assert count == 0


class TestStoreExecutionOutputFile:
    """Tests for store_execution_output_file method."""

    @pytest.mark.asyncio
    async def test_store_output_file_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful output file storage."""
        mock_minio_client.bucket_exists.return_value = True

        with patch("src.services.file.generate_file_id", return_value="file-output-123"):
            file_id = await file_service.store_execution_output_file(
                session_id="session-123",
                filename="output.png",
                content=b"fake image content",
            )

        assert file_id == "file-output-123"
        mock_minio_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_output_file_s3_error(self, file_service, mock_minio_client, mock_redis_client):
        """Test output file storage S3 error."""
        mock_minio_client.bucket_exists.return_value = True
        mock_minio_client.put_object.side_effect = S3Error(
            "Error", "UploadFailed", "resource", "request_id", "host_id", "response"
        )

        with patch("src.services.file.generate_file_id", return_value="file-output-123"):
            with pytest.raises(S3Error):
                await file_service.store_execution_output_file(
                    session_id="session-123",
                    filename="output.png",
                    content=b"fake content",
                )


class TestGetFileContent:
    """Tests for get_file_content method."""

    @pytest.mark.asyncio
    async def test_get_file_content_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful file content retrieval."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
        }

        # Mock MinIO response
        mock_response = MagicMock()
        mock_response.read.return_value = b"file content here"
        mock_minio_client.get_object.return_value = mock_response

        content = await file_service.get_file_content("session-123", "file-456")

        assert content == b"file content here"
        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_file_content_not_found(self, file_service, mock_redis_client):
        """Test get content when file not found."""
        mock_redis_client.hgetall.return_value = {}

        content = await file_service.get_file_content("session-123", "file-456")

        assert content is None

    @pytest.mark.asyncio
    async def test_get_file_content_s3_error(self, file_service, mock_minio_client, mock_redis_client):
        """Test get content with S3 error."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
        }

        mock_minio_client.get_object.side_effect = S3Error(
            "Error", "NoSuchKey", "resource", "request_id", "host_id", "response"
        )

        content = await file_service.get_file_content("session-123", "file-456")

        assert content is None


class TestStoreUploadedFile:
    """Tests for store_uploaded_file method."""

    @pytest.mark.asyncio
    async def test_store_uploaded_file_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful file upload storage."""
        mock_minio_client.bucket_exists.return_value = True

        with patch("src.services.file.generate_file_id", return_value="file-upload-123"):
            file_id = await file_service.store_uploaded_file(
                session_id="session-123",
                filename="uploaded.txt",
                content=b"uploaded content",
                content_type="text/plain",
            )

        assert file_id == "file-upload-123"
        mock_minio_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_uploaded_file_with_none_content_type(self, file_service, mock_minio_client, mock_redis_client):
        """Test upload with no content type uses default."""
        mock_minio_client.bucket_exists.return_value = True

        with patch("src.services.file.generate_file_id", return_value="file-upload-123"):
            file_id = await file_service.store_uploaded_file(
                session_id="session-123",
                filename="data.bin",
                content=b"binary data",
                content_type=None,
            )

        assert file_id == "file-upload-123"


class TestConfirmUpload:
    """Tests for confirm_upload method."""

    @pytest.mark.asyncio
    async def test_confirm_upload_success(self, file_service, mock_minio_client, mock_redis_client):
        """Test successful upload confirmation."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
            "path": "/mnt/data/test.txt",
            "content_type": "text/plain",
            "created_at": "2024-01-01T00:00:00Z",
            "size": 0,
        }

        # Mock stat_object response
        mock_stat = MagicMock()
        mock_stat.size = 1024
        mock_minio_client.stat_object.return_value = mock_stat

        result = await file_service.confirm_upload("session-123", "file-456")

        assert result.file_id == "file-456"
        assert result.size == 1024
        assert result.filename == "test.txt"

    @pytest.mark.asyncio
    async def test_confirm_upload_file_not_found(self, file_service, mock_redis_client):
        """Test confirmation when file not found."""
        mock_redis_client.hgetall.return_value = {}

        with pytest.raises(ValueError) as exc_info:
            await file_service.confirm_upload("session-123", "nonexistent")

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_confirm_upload_s3_error(self, file_service, mock_minio_client, mock_redis_client):
        """Test confirmation when S3 stat fails."""
        from minio.error import S3Error

        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
            "path": "/mnt/data/test.txt",
            "content_type": "text/plain",
            "created_at": "2024-01-01T00:00:00Z",
            "size": 0,
        }

        mock_minio_client.stat_object.side_effect = S3Error("test", "NoSuchKey", "test", "test", "test", "test")

        with pytest.raises(S3Error):
            await file_service.confirm_upload("session-123", "file-456")


class TestStoreFileMetadataErrors:
    """Tests for _store_file_metadata error handling."""

    @pytest.mark.asyncio
    async def test_store_metadata_redis_error(self, file_service, mock_redis_client):
        """Test _store_file_metadata raises on Redis error."""
        mock_redis_client.hset.side_effect = Exception("Redis connection error")

        metadata = {"file_id": "file-123", "filename": "test.txt"}

        with pytest.raises(Exception) as exc_info:
            await file_service._store_file_metadata("session-123", "file-123", metadata)

        assert "Redis" in str(exc_info.value)


class TestGetFileMetadataErrors:
    """Tests for _get_file_metadata error handling."""

    @pytest.mark.asyncio
    async def test_get_metadata_redis_error(self, file_service, mock_redis_client):
        """Test _get_file_metadata returns None on Redis error."""
        mock_redis_client.hgetall.side_effect = Exception("Redis connection error")

        result = await file_service._get_file_metadata("session-123", "file-123")

        assert result is None


class TestDeleteFileMetadataErrors:
    """Tests for _delete_file_metadata error handling."""

    @pytest.mark.asyncio
    async def test_delete_metadata_redis_error(self, file_service, mock_redis_client):
        """Test _delete_file_metadata raises on Redis error."""
        mock_redis_client.delete.side_effect = Exception("Redis connection error")

        with pytest.raises(Exception) as exc_info:
            await file_service._delete_file_metadata("session-123", "file-123")

        assert "Redis" in str(exc_info.value)


class TestDownloadFileErrors:
    """Tests for download_file error handling."""

    @pytest.mark.asyncio
    async def test_download_file_s3_error(self, file_service, mock_minio_client, mock_redis_client):
        """Test download_file returns None on S3 error."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
        }

        mock_minio_client.presigned_get_object.side_effect = S3Error(
            "test", "AccessDenied", "test", "test", "test", "test"
        )

        result = await file_service.download_file("session-123", "file-456")

        assert result is None


class TestDeleteFileErrors:
    """Tests for delete_file error handling."""

    @pytest.mark.asyncio
    async def test_delete_file_s3_error(self, file_service, mock_minio_client, mock_redis_client):
        """Test delete_file returns False on S3 error."""
        mock_redis_client.hgetall.return_value = {
            "file_id": "file-456",
            "filename": "test.txt",
            "object_key": "sessions/session-123/uploads/file-456",
        }

        mock_minio_client.remove_object.side_effect = S3Error("test", "AccessDenied", "test", "test", "test", "test")

        result = await file_service.delete_file("session-123", "file-456")

        assert result is False


class TestListFilesErrors:
    """Tests for list_files error handling."""

    @pytest.mark.asyncio
    async def test_list_files_redis_error(self, file_service, mock_redis_client):
        """Test list_files returns empty list on Redis error."""
        mock_redis_client.smembers.side_effect = Exception("Redis connection error")

        result = await file_service.list_files("session-123")

        assert result == []


class TestCleanupSessionFilesErrors:
    """Tests for cleanup_session_files error handling."""

    @pytest.mark.asyncio
    async def test_cleanup_session_files_redis_error(self, file_service, mock_redis_client):
        """Test cleanup_session_files returns 0 on Redis error."""
        mock_redis_client.smembers.side_effect = Exception("Redis connection error")

        result = await file_service.cleanup_session_files("session-123")

        assert result == 0


class TestUploadFileErrors:
    """Tests for upload_file error handling."""

    @pytest.mark.asyncio
    async def test_upload_file_s3_error(self, file_service, mock_minio_client):
        """Test upload_file raises on S3 error."""
        from src.models import FileUploadRequest

        mock_minio_client.bucket_exists.return_value = True
        mock_minio_client.presigned_put_object.side_effect = S3Error(
            "test", "AccessDenied", "test", "test", "test", "test"
        )

        request = FileUploadRequest(filename="test.txt")

        with pytest.raises(S3Error):
            await file_service.upload_file("session-123", request)


class TestStoreUploadedFileErrors:
    """Tests for store_uploaded_file error handling."""

    @pytest.mark.asyncio
    async def test_store_uploaded_file_s3_error(self, file_service, mock_minio_client):
        """Test store_uploaded_file raises on S3 error."""
        mock_minio_client.bucket_exists.return_value = True
        mock_minio_client.put_object.side_effect = S3Error("test", "AccessDenied", "test", "test", "test", "test")

        with pytest.raises(S3Error):
            await file_service.store_uploaded_file(
                session_id="session-123",
                filename="test.txt",
                content=b"test content",
            )


class TestCleanupOrphanObjects:
    """Tests for cleanup_orphan_objects method."""

    @pytest.mark.asyncio
    async def test_cleanup_orphan_objects_empty_index(self, file_service, mock_redis_client):
        """Test cleanup skips when sessions index is empty."""
        mock_redis_client.smembers.return_value = set()

        result = await file_service.cleanup_orphan_objects()

        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_orphan_objects_error(self, file_service, mock_redis_client):
        """Test cleanup returns 0 on error."""
        mock_redis_client.smembers.side_effect = Exception("Redis error")

        result = await file_service.cleanup_orphan_objects()

        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_orphan_objects_with_active_sessions(
        self, file_service, mock_minio_client, mock_redis_client
    ):
        """Test cleanup skips files belonging to active sessions."""
        from datetime import timedelta

        mock_redis_client.smembers.return_value = {"session-123"}

        # Mock object with active session
        mock_obj = MagicMock()
        mock_obj.object_name = "sessions/session-123/uploads/file-1"
        mock_obj.last_modified = datetime.now() - timedelta(hours=2)
        mock_minio_client.list_objects.return_value = [mock_obj]

        result = await file_service.cleanup_orphan_objects()

        # No objects deleted because session is active
        assert result == 0
        mock_minio_client.remove_object.assert_not_called()


class TestCloseMethod:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_success(self, file_service, mock_redis_client):
        """Test close closes Redis connection."""
        await file_service.close()

        mock_redis_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_error(self, file_service, mock_redis_client):
        """Test close handles Redis error."""
        mock_redis_client.close.side_effect = Exception("Connection error")

        # Should not raise
        await file_service.close()
