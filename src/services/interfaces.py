"""Service interfaces for the Code Interpreter API."""

# Standard library imports
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

# Local application imports
from ..models import (
    CodeExecution,
    ExecuteCodeRequest,
    FileInfo,
    FileUploadRequest,
    Session,
    SessionCreate,
)


class SessionServiceInterface(ABC):
    """Interface for session management service."""

    @abstractmethod
    async def create_session(self, request: SessionCreate) -> Session:
        """Create a new code execution session."""
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID."""
        pass

    @abstractmethod
    async def update_session(self, session_id: str, **updates) -> Session | None:
        """Update session properties."""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and cleanup resources."""
        pass

    @abstractmethod
    async def list_sessions(self, limit: int = 100, offset: int = 0) -> list[Session]:
        """List all active sessions."""
        pass

    @abstractmethod
    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions and return count of cleaned sessions."""
        pass

    @abstractmethod
    async def list_sessions_by_entity(self, entity_id: str, limit: int = 100) -> list[Session]:
        """List sessions for an entity."""
        pass


class ExecutionServiceInterface(ABC):
    """Interface for code execution service."""

    @property
    @abstractmethod
    def kubernetes_manager(self) -> Any:
        """Get the Kubernetes manager instance."""
        pass

    @abstractmethod
    async def execute_code(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: list[dict[str, Any]] | None = None,
        initial_state: str | None = None,
        capture_state: bool = True,
    ) -> tuple[CodeExecution, Any, str | None, list[str], str]:
        """Execute code in a session.

        Returns:
            Tuple of (CodeExecution, container, new_state, state_errors, container_source)
        """
        pass

    @abstractmethod
    async def get_execution(self, execution_id: str) -> CodeExecution | None:
        """Retrieve an execution by ID."""
        pass

    @abstractmethod
    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution."""
        pass

    @abstractmethod
    async def list_executions(self, session_id: str, limit: int = 100) -> list[CodeExecution]:
        """List executions for a session."""
        pass

    def pop_job_file_content(self, file_path: str) -> bytes | None:
        """Pop pre-downloaded file content from a Job execution.

        Returns None by default. Overridden by implementations that support
        Job-path file collection.
        """
        return None


class FileServiceInterface(ABC):
    """Interface for file management service."""

    @abstractmethod
    async def upload_file(self, session_id: str, request: FileUploadRequest) -> tuple[str, str]:
        """Generate upload URL for a file. Returns (file_id, upload_url)."""
        pass

    @abstractmethod
    async def confirm_upload(self, session_id: str, file_id: str) -> FileInfo:
        """Confirm file upload completion and return file info."""
        pass

    @abstractmethod
    async def get_file_info(self, session_id: str, file_id: str) -> FileInfo | None:
        """Get file information."""
        pass

    @abstractmethod
    async def list_files(self, session_id: str) -> list[FileInfo]:
        """List all files in a session."""
        pass

    @abstractmethod
    async def download_file(self, session_id: str, file_id: str) -> str | None:
        """Generate download URL for a file."""
        pass

    @abstractmethod
    async def get_file_content(self, session_id: str, file_id: str) -> bytes | None:
        """Get actual file content."""
        pass

    @abstractmethod
    async def delete_file(self, session_id: str, file_id: str) -> bool:
        """Delete a file from storage."""
        pass

    @abstractmethod
    async def cleanup_session_files(self, session_id: str) -> int:
        """Clean up all files for a session. Returns count of deleted files."""
        pass

    @abstractmethod
    async def store_uploaded_file(
        self, session_id: str, filename: str, content: bytes, content_type: str | None = None
    ) -> str:
        """Store an uploaded file directly. Returns file_id."""
        pass

    @abstractmethod
    async def store_execution_output_file(self, session_id: str, filename: str, content: bytes) -> str:
        """Store a file generated during execution. Returns file_id."""
        pass
