"""Kubernetes Manager for code execution.

This is the main entry point for Kubernetes-based code execution.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import structlog

from .client import (
    get_core_api,
    get_current_namespace,
    get_initialization_error,
)
from .client import (
    is_available as k8s_available,
)
from .job_executor import JobExecutor
from .models import (
    ExecutionResult,
    FileData,
    PodHandle,
    PodSpec,
    PoolConfig,
)
from .pool import PodPoolManager

logger = structlog.get_logger(__name__)


class KubernetesManager:
    """Manages code execution in Kubernetes pods.

    This class provides a unified interface for code execution,
    automatically choosing between warm pod pools (for hot-path
    languages) and Jobs (for cold-path languages).
    """

    def __init__(
        self,
        namespace: str | None = None,
        pool_configs: list[PoolConfig] | None = None,
        sidecar_image: str = "aronmuon/kubecoderun-sidecar:latest",
        default_cpu_limit: str = "1",
        default_memory_limit: str = "512Mi",
        default_cpu_request: str = "100m",
        default_memory_request: str = "128Mi",
        seccomp_profile_type: str = "RuntimeDefault",
        network_isolated: bool = False,
        gke_sandbox_enabled: bool = False,
        runtime_class_name: str = "gvisor",
        sandbox_node_selector: dict[str, str] | None = None,
        custom_tolerations: list[dict[str, str]] | None = None,
    ):
        """Initialize the Kubernetes manager.

        Args:
            namespace: Kubernetes namespace for execution pods
            pool_configs: Pool configurations for each language
            sidecar_image: Default sidecar container image
            default_cpu_limit: Default CPU limit for pods
            default_memory_limit: Default memory limit for pods
            default_cpu_request: Default CPU request for pods
            default_memory_request: Default memory request for pods
            seccomp_profile_type: Seccomp profile type (RuntimeDefault, Unconfined, Localhost)
            network_isolated: Whether network isolation is enabled (disables network-dependent features)
            gke_sandbox_enabled: Enable GKE Sandbox (gVisor) for additional kernel isolation
            runtime_class_name: Runtime class name for sandboxed pods
            sandbox_node_selector: Node selector for sandbox-enabled nodes
            custom_tolerations: Custom tolerations for node pool taints
        """
        self.namespace = namespace or get_current_namespace()
        self.sidecar_image = sidecar_image
        self.default_cpu_limit = default_cpu_limit
        self.default_memory_limit = default_memory_limit
        self.default_cpu_request = default_cpu_request
        self.default_memory_request = default_memory_request
        self.seccomp_profile_type = seccomp_profile_type
        self.network_isolated = network_isolated
        self.gke_sandbox_enabled = gke_sandbox_enabled
        self.runtime_class_name = runtime_class_name
        self.sandbox_node_selector = sandbox_node_selector
        self.custom_tolerations = custom_tolerations

        # Pool manager for warm pods
        self._pool_manager = PodPoolManager(
            namespace=self.namespace,
            configs=pool_configs or [],
        )

        # Job executor for cold languages
        self._job_executor = JobExecutor(
            namespace=self.namespace,
            sidecar_image=sidecar_image,
        )

        # Language image mappings (can be overridden by pool configs)
        self._language_images: dict[str, str] = {
            "python": "aronmuon/kubecoderun-python:latest",
            "py": "aronmuon/kubecoderun-python:latest",
            "javascript": "aronmuon/kubecoderun-javascript:latest",
            "js": "aronmuon/kubecoderun-javascript:latest",
            "typescript": "aronmuon/kubecoderun-typescript:latest",
            "ts": "aronmuon/kubecoderun-typescript:latest",
            "go": "aronmuon/kubecoderun-go:latest",
            "rust": "aronmuon/kubecoderun-rust:latest",
            "rs": "aronmuon/kubecoderun-rust:latest",
        }

        # Track active executions
        self._active_handles: dict[str, PodHandle] = {}  # session_id -> handle

        self._started = False

    async def start(self):
        """Start the manager and warm up pools."""
        if self._started:
            return

        logger.info(
            "Starting Kubernetes manager",
            namespace=self.namespace,
        )

        await self._pool_manager.start()
        self._started = True

        logger.info(
            "Kubernetes manager started",
            pool_stats=self._pool_manager.get_pool_stats(),
        )

    async def stop(self):
        """Stop the manager and clean up resources."""
        logger.info("Stopping Kubernetes manager")

        await self._pool_manager.stop()
        await self._job_executor.close()

        # Clean up any active handles
        for session_id, handle in list(self._active_handles.items()):
            await self.destroy_pod(handle)

        self._started = False
        logger.info("Kubernetes manager stopped")

    def is_available(self) -> bool:
        """Check if Kubernetes is available."""
        return k8s_available()

    def get_initialization_error(self) -> str | None:
        """Get any initialization error."""
        return get_initialization_error()

    def get_image_for_language(self, language: str) -> str:
        """Get the container image for a language.

        Args:
            language: Programming language

        Returns:
            Container image URL
        """
        # Check pool config first
        config = self._pool_manager.get_config(language)
        if config:
            return config.image

        # Fall back to default mapping
        return self._language_images.get(
            language.lower(),
            f"aronmuon/kubecoderun-{language}:latest",
        )

    def uses_pool(self, language: str) -> bool:
        """Check if a language uses warm pod pools."""
        return self._pool_manager.uses_pool(language.lower())

    async def acquire_pod(
        self,
        session_id: str,
        language: str,
    ) -> tuple[PodHandle | None, str]:
        """Acquire a pod for code execution.

        For languages with warm pools, acquires from the pool.
        For other languages, returns None (use execute_with_job instead).

        Args:
            session_id: Session identifier
            language: Programming language

        Returns:
            Tuple of (PodHandle or None, source) where source is
            'pool_hit' or 'pool_miss'
        """
        language = language.lower()

        # Normalize language aliases
        if language == "python":
            language = "py"
        elif language == "javascript":
            language = "js"

        if self.uses_pool(language):
            handle = await self._pool_manager.acquire(
                language,
                session_id,
                timeout=10,
            )
            if handle:
                self._active_handles[session_id] = handle
                return handle, "pool_hit"
            else:
                logger.warning(
                    "Failed to acquire pod from pool",
                    language=language,
                    session_id=session_id[:12],
                )
                return None, "pool_miss"

        return None, "pool_miss"

    async def execute_code(
        self,
        session_id: str,
        code: str,
        language: str,
        timeout: int = 30,
        files: list[dict[str, Any]] | None = None,
        initial_state: str | None = None,
        capture_state: bool = False,
    ) -> tuple[ExecutionResult, PodHandle | None, str]:
        """Execute code in a pod.

        Automatically chooses between warm pool and Job execution
        based on language configuration.

        Args:
            session_id: Session identifier
            code: Code to execute
            language: Programming language
            timeout: Execution timeout
            files: Files to upload (list of dicts with filename, content)
            initial_state: State to restore (base64)
            capture_state: Whether to capture state after execution

        Returns:
            Tuple of (ExecutionResult, PodHandle or None, source)
        """
        language = language.lower()

        # Convert files to FileData
        file_data = None
        if files:
            file_data = [
                FileData(
                    filename=f.get("filename", f.get("name", "file")),
                    content=f.get("content", b""),
                    session_id=f.get("session_id"),
                )
                for f in files
                if isinstance(f.get("content"), bytes)
            ]

        # Try to acquire from pool
        handle, source = await self.acquire_pod(session_id, language)

        if handle:
            # Execute using pool
            result = await self._pool_manager.execute(
                handle,
                code,
                timeout=timeout,
                files=file_data,
                initial_state=initial_state,
                capture_state=capture_state,
            )
            return result, handle, source
        else:
            # Use Job execution
            spec = PodSpec(
                language=language,
                image=self.get_image_for_language(language),
                session_id=session_id,
                namespace=self.namespace,
                sidecar_image=self.sidecar_image,
                cpu_limit=self.default_cpu_limit,
                memory_limit=self.default_memory_limit,
                cpu_request=self.default_cpu_request,
                memory_request=self.default_memory_request,
                seccomp_profile_type=self.seccomp_profile_type,
                network_isolated=self.network_isolated,
                gke_sandbox_enabled=self.gke_sandbox_enabled,
                runtime_class_name=self.runtime_class_name,
                sandbox_node_selector=self.sandbox_node_selector,
                custom_tolerations=self.custom_tolerations,
            )

            result = await self._job_executor.execute_with_job(
                spec,
                session_id,
                code,
                timeout=timeout,
                files=file_data,
                initial_state=initial_state,
                capture_state=capture_state,
            )
            return result, None, "job"

    async def destroy_pod(self, handle: PodHandle):
        """Destroy an execution pod.

        Args:
            handle: Pod handle to destroy
        """
        if not handle:
            return

        # Remove from active handles
        if handle.session_id and handle.session_id in self._active_handles:
            del self._active_handles[handle.session_id]

        # Release from pool (with destroy=True)
        await self._pool_manager.release(handle, destroy=True)

    async def copy_files_to_pod(
        self,
        handle: PodHandle,
        files: list[FileData],
    ) -> bool:
        """Copy files to a pod.

        Args:
            handle: Pod handle
            files: Files to copy

        Returns:
            True if successful
        """
        if not handle.pod_ip:
            return False

        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            for file_data in files:
                try:
                    await client.post(
                        f"{handle.sidecar_url}/files",
                        files={"files": (file_data.filename, file_data.content)},
                    )
                except Exception as e:
                    logger.error(
                        "Failed to copy file to pod",
                        pod_name=handle.name,
                        filename=file_data.filename,
                        error=str(e),
                    )
                    return False

        return True

    async def copy_file_from_pod(
        self,
        handle: PodHandle,
        path: str,
    ) -> bytes | None:
        """Copy a file from a pod.

        Args:
            handle: Pod handle
            path: File path in the pod

        Returns:
            File content or None
        """
        if not handle.pod_ip:
            return None

        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{handle.sidecar_url}/files/{path}",
                )
                if response.status_code == 200:
                    return response.content
            except Exception as e:
                logger.error(
                    "Failed to copy file from pod",
                    pod_name=handle.name,
                    path=path,
                    error=str(e),
                )

        return None

    def get_pool_stats(self) -> dict[str, dict[str, int]]:
        """Get statistics for all pod pools."""
        return self._pool_manager.get_pool_stats()

    async def destroy_pods_batch(
        self,
        handles: list[PodHandle],
    ) -> int:
        """Destroy multiple pods.

        Args:
            handles: List of pod handles to destroy

        Returns:
            Number of pods successfully destroyed
        """
        count = 0
        for handle in handles:
            try:
                await self.destroy_pod(handle)
                count += 1
            except Exception:
                pass
        return count
