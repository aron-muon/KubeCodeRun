"""Code execution runner - core execution logic.

This module provides the CodeExecutionRunner that coordinates code execution
using Kubernetes pods with HTTP runner communication.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import structlog

from ...config import settings
from ...config.languages import get_language
from ...models import (
    CodeExecution,
    ExecuteCodeRequest,
    ExecutionOutput,
    ExecutionStatus,
    OutputType,
)
from ...utils.id_generator import generate_execution_id
from ..kubernetes import ExecutionResult, KubernetesManager, PodHandle
from ..metrics import ExecutionMetrics, metrics_collector
from .output import OutputProcessor

logger = structlog.get_logger(__name__)


class CodeExecutionRunner:
    """Core code execution runner using Kubernetes pods.

    This runner uses KubernetesManager which:
    - Uses warm pod pools for hot-path languages (Python, JS)
    - Uses Kubernetes Jobs for cold-path languages (Go, Rust)
    - Communicates with pods via HTTP runner
    """

    def __init__(self, kubernetes_manager: KubernetesManager = None):
        """Initialize the execution runner.

        Args:
            kubernetes_manager: Optional KubernetesManager instance
        """
        self._kubernetes_manager = kubernetes_manager
        self._manager_started = False
        self.active_executions: dict[str, CodeExecution] = {}
        self.session_handles: dict[str, PodHandle] = {}

    @property
    def kubernetes_manager(self) -> KubernetesManager:
        """Get the Kubernetes manager, creating it lazily if needed."""
        if self._kubernetes_manager is None:
            self._kubernetes_manager = KubernetesManager()
        return self._kubernetes_manager

    async def _ensure_started(self):
        """Ensure the Kubernetes manager is started."""
        if not self._manager_started:
            await self.kubernetes_manager.start()
            self._manager_started = True

    async def _get_pod(self, session_id: str, language: str) -> tuple[PodHandle | None, str]:
        """Get pod for execution, using pool if available.

        Priority:
        1. Get fresh pod from pool (fast, ~50-100ms)
        2. Return None to indicate Job execution needed

        Returns:
            Tuple of (PodHandle or None, source) where source is 'pool_hit' or 'pool_miss'
        """
        await self._ensure_started()

        handle, source = await self.kubernetes_manager.acquire_pod(session_id, language)

        if handle:
            logger.debug(
                "Acquired pod from pool",
                session_id=session_id[:12],
                pod_name=handle.name,
            )
        else:
            logger.debug(
                "No pool available, will use Job execution",
                session_id=session_id[:12],
                language=language,
            )

        return handle, source

    async def execute(
        self,
        session_id: str,
        request: ExecuteCodeRequest,
        files: list[dict[str, Any]] | None = None,
        initial_state: str | None = None,
        capture_state: bool = True,
    ) -> tuple[CodeExecution, PodHandle | None, str | None, list[str], str]:
        """Execute code in a session with optional state persistence.

        Args:
            session_id: Session identifier
            request: Execution request with code and language
            files: Optional list of files to mount
            initial_state: Base64-encoded state to restore before execution (Python only)
            capture_state: Whether to capture state after execution (Python only)

        Returns:
            Tuple of (CodeExecution record, PodHandle, new_state, state_errors, container_source)
            container_source is 'pool_hit', 'pool_miss', or 'job'.
        """
        execution_id = generate_execution_id()

        logger.info(
            "Starting code execution",
            execution_id=execution_id[:8],
            session_id=session_id,
            language=request.language,
            code_length=len(request.code),
        )

        # Create execution record
        execution = CodeExecution(
            execution_id=execution_id,
            session_id=session_id,
            code=request.code,
            language=request.language,
            status=ExecutionStatus.PENDING,
        )

        self.active_executions[execution_id] = execution

        # Check if Kubernetes is available
        await self._ensure_started()
        if not self.kubernetes_manager.is_available():
            logger.error(
                "Kubernetes not available",
                execution_id=execution_id[:8],
                error=self.kubernetes_manager.get_initialization_error(),
            )
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = datetime.now(UTC)
            execution.error_message = f"Kubernetes unavailable: {self.kubernetes_manager.get_initialization_error()}"
            return execution, None, None, [], "pool_miss"

        handle = None
        container_source = "pool_miss"
        new_state = None
        state_errors: list[str] = []

        try:
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.now(UTC)

            # Execute code using Kubernetes manager
            start_time = datetime.now(UTC)

            # Use language-specific timeout if not explicitly provided
            execution_timeout = request.timeout or settings.get_execution_timeout(request.language)

            result, handle, container_source = await self.kubernetes_manager.execute_code(
                session_id=session_id,
                code=request.code,
                language=request.language,
                timeout=execution_timeout,
                files=files,
                initial_state=initial_state,
                capture_state=capture_state,
            )

            end_time = datetime.now(UTC)
            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # Extract state from result
            new_state = result.state
            state_errors = result.state_errors or []

            # Process outputs
            outputs = self._process_outputs(result.stdout, result.stderr, end_time)

            # For pod-based execution, check for generated files
            generated_files = []
            if handle:
                # Only detect files if code likely generates files
                should_detect_files = files or any(
                    kw in request.code for kw in ["open(", "savefig", "to_csv", "write(", ".save("]
                )
                if should_detect_files:
                    generated_files = await self._detect_generated_files(handle)

            mounted_filenames = self._get_mounted_filenames(files)
            filtered_files = self._filter_generated_files(generated_files, mounted_filenames)

            for file_info in filtered_files:
                if OutputProcessor.validate_generated_file(file_info):
                    outputs.append(
                        ExecutionOutput(
                            type=OutputType.FILE,
                            content=file_info["path"],
                            mime_type=file_info.get("mime_type"),
                            size=file_info.get("size"),
                            timestamp=end_time,
                        )
                    )

            # Update execution record
            execution.status = OutputProcessor.determine_execution_status(
                result.exit_code, result.stderr, execution_time_ms
            )
            execution.completed_at = end_time
            execution.outputs = outputs
            execution.exit_code = result.exit_code
            execution.execution_time_ms = execution_time_ms

            if execution.status == ExecutionStatus.FAILED:
                execution.error_message = OutputProcessor.format_error_message(result.exit_code, result.stderr)

            logger.info(
                f"Code execution {execution_id} completed: status={execution.status}, "
                f"exit_code={result.exit_code}, time={execution_time_ms}ms, source={container_source}"
            )

            # Log state info if captured
            if new_state:
                logger.debug(
                    "State captured",
                    session_id=session_id[:12],
                    state_size=len(new_state),
                )
            if state_errors:
                for err in state_errors[:3]:
                    logger.debug("State serialization warning", warning=err)

            # Store handle for session
            if handle:
                self.session_handles[session_id] = handle

        except TimeoutError:
            execution.status = ExecutionStatus.TIMEOUT
            execution.completed_at = datetime.now(UTC)
            execution.error_message = (
                f"Execution timed out after {request.timeout or settings.max_execution_time} seconds"
            )
            execution.execution_time_ms = (
                int((datetime.now(UTC) - execution.started_at).total_seconds() * 1000) if execution.started_at else 0
            )
            new_state = None
            state_errors = []
            logger.warning(f"Code execution {execution_id} timed out")

        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = datetime.now(UTC)
            execution.error_message = str(e)
            execution.execution_time_ms = (
                int((datetime.now(UTC) - execution.started_at).total_seconds() * 1000) if execution.started_at else 0
            )
            new_state = None
            state_errors = []
            logger.error(f"Code execution {execution_id} failed: {e}")

        # Record metrics
        self._record_metrics(execution, session_id, request.language, files)

        return execution, handle, new_state, state_errors, container_source

    def _process_outputs(self, stdout: str, stderr: str, timestamp: datetime) -> list[ExecutionOutput]:
        """Process stdout and stderr into ExecutionOutput list."""
        outputs = []

        if stdout and stdout.strip():
            outputs.append(
                ExecutionOutput(
                    type=OutputType.STDOUT,
                    content=OutputProcessor.sanitize_output(stdout),
                    timestamp=timestamp,
                )
            )

        if stderr and stderr.strip():
            outputs.append(
                ExecutionOutput(
                    type=OutputType.STDERR,
                    content=OutputProcessor.sanitize_output(stderr),
                    timestamp=timestamp,
                )
            )

        return outputs

    def _get_mounted_filenames(self, files: list[dict[str, Any]] | None) -> set:
        """Get set of mounted filenames for filtering."""
        mounted = set()
        if files:
            try:
                for f in files:
                    name = f.get("filename") or f.get("name")
                    if name:
                        mounted.add(name)
                        mounted.add(OutputProcessor.normalize_filename(name))
            except Exception:
                pass
        return mounted

    def _filter_generated_files(self, generated: list[dict[str, Any]], mounted_filenames: set) -> list[dict[str, Any]]:
        """Filter out mounted files from generated files list."""
        return [f for f in generated if Path(f.get("path", "")).name not in mounted_filenames]

    def _record_metrics(
        self,
        execution: CodeExecution,
        session_id: str,
        language: str,
        files: list[dict[str, Any]] | None,
    ) -> None:
        """Record execution metrics."""
        try:
            metrics = ExecutionMetrics(
                execution_id=execution.execution_id,
                session_id=session_id,
                language=language,
                status=execution.status.value,
                execution_time_ms=execution.execution_time_ms or 0,
                memory_peak_mb=execution.memory_peak_mb,
                exit_code=execution.exit_code,
                file_count=len(files) if files else 0,
                output_size_bytes=(sum(len(o.content) for o in execution.outputs) if execution.outputs else 0),
            )
            metrics_collector.record_execution_metrics(metrics)
        except Exception as e:
            logger.error("Failed to record execution metrics", error=str(e))

    async def _detect_generated_files(self, handle: PodHandle) -> list[dict[str, Any]]:
        """Detect files generated during execution via runner HTTP API."""
        if not handle or not handle.pod_ip:
            return []

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{handle.runner_url}/files")
                if response.status_code == 200:
                    data = response.json()
                    files = data.get("files", [])
                    generated_files = []
                    for f in files:
                        # Skip code files
                        name = f.get("name", "")
                        if name.startswith("code.") or name == "Code.java":
                            continue
                        if f.get("size", 0) > settings.max_file_size_mb * 1024 * 1024:
                            continue
                        generated_files.append(
                            {
                                "path": f"/mnt/data/{name}",
                                "size": f.get("size", 0),
                                "mime_type": OutputProcessor.guess_mime_type(name),
                            }
                        )
                        if len(generated_files) >= settings.max_output_files:
                            break
                    return generated_files
        except Exception as e:
            logger.warning(
                "Failed to detect generated files via runner",
                pod_name=handle.name,
                error=str(e),
            )

        return []

    def get_container_by_session(self, session_id: str) -> PodHandle | None:
        """Get pod handle for a session.

        DEPRECATED: Handle is now returned directly from execute() method.
        This method is kept for backward compatibility only.
        """
        return self.session_handles.get(session_id)

    async def get_execution(self, execution_id: str) -> CodeExecution | None:
        """Retrieve an execution by ID."""
        return self.active_executions.get(execution_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution."""
        execution = self.active_executions.get(execution_id)
        if not execution or execution.status not in [
            ExecutionStatus.PENDING,
            ExecutionStatus.RUNNING,
        ]:
            return False

        try:
            handle = self.session_handles.get(execution.session_id)
            if handle:
                await self.kubernetes_manager.destroy_pod(handle)
                del self.session_handles[execution.session_id]

            execution.status = ExecutionStatus.CANCELLED
            execution.completed_at = datetime.now(UTC)
            execution.error_message = "Execution cancelled by user"

            logger.info(f"Cancelled execution {execution_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel execution {execution_id}: {e}")
            return False

    async def list_executions(self, session_id: str, limit: int = 100) -> list[CodeExecution]:
        """List executions for a session."""
        executions = [e for e in self.active_executions.values() if e.session_id == session_id]
        executions.sort(key=lambda x: x.created_at, reverse=True)
        return executions[:limit]

    async def cleanup_session(self, session_id: str) -> bool:
        """Clean up resources for a session."""
        try:
            if session_id in self.session_handles:
                handle = self.session_handles[session_id]
                await self.kubernetes_manager.destroy_pod(handle)
                del self.session_handles[session_id]

            execution_ids = [eid for eid, e in self.active_executions.items() if e.session_id == session_id]
            for eid in execution_ids:
                del self.active_executions[eid]

            logger.info("Cleaned up session resources", session_id=session_id)
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup session: {e}")
            return False

    async def cleanup_expired_executions(self, max_age_hours: int = 24) -> int:
        """Clean up old execution records."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        expired = [
            eid
            for eid, e in self.active_executions.items()
            if e.created_at < cutoff
            and e.status
            in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMEOUT,
                ExecutionStatus.CANCELLED,
            ]
        ]

        for eid in expired:
            del self.active_executions[eid]

        logger.info(f"Cleaned up {len(expired)} expired executions")
        return len(expired)

    async def cleanup_all_containers(self) -> None:
        """Clean up all active pods during shutdown."""
        logger.info("Cleaning up all pods", count=len(self.session_handles))

        handles = list(self.session_handles.values())
        if handles:
            cleaned = await self.kubernetes_manager.destroy_pods_batch(handles)
            logger.info(f"Cleaned up {cleaned}/{len(handles)} pods")

        self.session_handles.clear()
        self.active_executions.clear()

        # Stop the Kubernetes manager
        await self.kubernetes_manager.stop()
