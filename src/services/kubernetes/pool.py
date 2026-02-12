"""Kubernetes Pod Pool Manager.

Maintains warm pools of pre-created pods for fast code execution,
similar to Fission's PoolManager. Each language can have its own
pool with configurable size.
"""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from uuid import uuid4

import httpx
import structlog
from kubernetes.client import ApiException

from .client import (
    create_pod_manifest,
    get_core_api,
    get_current_namespace,
)
from .models import (
    ExecutionResult,
    FileData,
    PodHandle,
    PodSpec,
    PodStatus,
    PoolConfig,
    PooledPod,
)

logger = structlog.get_logger(__name__)


class PodPool:
    """Manages a pool of warm pods for a specific language.

    The pool maintains a set of pre-created pods that are ready
    to execute code immediately, eliminating cold start latency.
    """

    def __init__(
        self,
        config: PoolConfig,
        namespace: str | None = None,
    ):
        """Initialize the pod pool.

        Args:
            config: Pool configuration
            namespace: Kubernetes namespace
        """
        self.config = config
        self.namespace = namespace or get_current_namespace()
        self.language = config.language
        self.pool_size = config.pool_size

        # Pool state
        self._pods: dict[str, PooledPod] = {}  # uid -> PooledPod
        self._available: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()

        # Session tracking (for cleanup)
        self._session_pods: dict[str, str] = {}  # session_id -> pod_uid

        # HTTP client for health checks and execution
        self._http_client: httpx.AsyncClient | None = None

        # Background tasks
        self._replenish_task: asyncio.Task | None = None
        self._health_check_task: asyncio.Task | None = None
        self._running = False

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    def _generate_pod_name(self) -> str:
        """Generate a unique pod name."""
        short_uuid = uuid4().hex[:8]
        return f"pool-{self.language}-{short_uuid}"

    async def start(self):
        """Start the pool and warm up pods."""
        if self._running:
            return

        self._running = True
        logger.info(
            "Starting pod pool",
            language=self.language,
            pool_size=self.pool_size,
        )

        # Initial warmup
        await self._warmup()

        # Start background tasks
        self._replenish_task = asyncio.create_task(self._replenish_loop())
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def stop(self):
        """Stop the pool and clean up all pods."""
        self._running = False

        # Cancel background tasks
        if self._replenish_task:
            self._replenish_task.cancel()
            try:
                await self._replenish_task
            except asyncio.CancelledError:
                pass

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Delete all pods
        async with self._lock:
            for pooled_pod in list(self._pods.values()):
                await self._delete_pod(pooled_pod.handle)
            self._pods.clear()

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()

        logger.info("Pod pool stopped", language=self.language)

    async def _warmup(self):
        """Create initial warm pods."""
        current_count = len([p for p in self._pods.values() if p.is_available])
        needed = self.pool_size - current_count

        if needed <= 0:
            return

        logger.info(
            "Warming up pool",
            language=self.language,
            current=current_count,
            needed=needed,
        )

        # Create pods in parallel (with limit)
        batch_size = min(needed, 5)
        for i in range(0, needed, batch_size):
            tasks = [self._create_warm_pod() for _ in range(min(batch_size, needed - i))]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _create_warm_pod(self) -> PooledPod | None:
        """Create a single warm pod."""
        core_api = get_core_api()
        if not core_api:
            return None

        pod_name = self._generate_pod_name()

        labels = {
            "app.kubernetes.io/name": "kubecoderun",
            "app.kubernetes.io/component": "execution",
            "app.kubernetes.io/managed-by": "kubecoderun",
            "kubecoderun.io/language": self.language,
            "kubecoderun.io/type": "pool",
            "kubecoderun.io/pool-status": "warm",
        }

        pod_manifest = create_pod_manifest(
            name=pod_name,
            namespace=self.namespace,
            main_image=self.config.image,
            sidecar_image=self.config.sidecar_image,
            language=self.language,
            labels=labels,
            cpu_limit=self.config.cpu_limit or "1",
            memory_limit=self.config.memory_limit or "512Mi",
            image_pull_policy=self.config.image_pull_policy,
            sidecar_cpu_limit=self.config.sidecar_cpu_limit,
            sidecar_memory_limit=self.config.sidecar_memory_limit,
            sidecar_cpu_request=self.config.sidecar_cpu_request,
            sidecar_memory_request=self.config.sidecar_memory_request,
            seccomp_profile_type=self.config.seccomp_profile_type,
            network_isolated=self.config.network_isolated,
            gke_sandbox_enabled=self.config.gke_sandbox_enabled,
            runtime_class_name=self.config.runtime_class_name,
            sandbox_node_selector=self.config.sandbox_node_selector,
            custom_tolerations=self.config.custom_tolerations,
        )

        try:
            loop = asyncio.get_event_loop()
            pod = await loop.run_in_executor(
                None,
                lambda: core_api.create_namespaced_pod(self.namespace, pod_manifest),
            )

            handle = PodHandle(
                name=pod_name,
                namespace=self.namespace,
                uid=pod.metadata.uid,
                language=self.language,
                status=PodStatus.PENDING,
                labels=labels,
            )

            # Wait for pod to be ready
            ready = await self._wait_for_pod_ready(handle)
            if not ready:
                await self._delete_pod(handle)
                return None

            handle.status = PodStatus.WARM

            pooled_pod = PooledPod(
                handle=handle,
                language=self.language,
            )

            async with self._lock:
                self._pods[handle.uid] = pooled_pod
                await self._available.put(handle.uid)

            logger.debug(
                "Created warm pod",
                pod_name=pod_name,
                language=self.language,
            )

            return pooled_pod

        except ApiException as e:
            logger.error(
                "Failed to create warm pod",
                pod_name=pod_name,
                error=str(e),
            )
            return None

    async def _wait_for_pod_ready(
        self,
        handle: PodHandle,
        timeout: int = 60,
    ) -> bool:
        """Wait for a pod to be ready."""
        core_api = get_core_api()
        if not core_api:
            return False

        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                loop = asyncio.get_event_loop()
                pod = await loop.run_in_executor(
                    None,
                    lambda: core_api.read_namespaced_pod(
                        handle.name,
                        handle.namespace,
                    ),
                )

                handle.pod_ip = pod.status.pod_ip

                if pod.status.phase == "Running":
                    if pod.status.container_statuses:
                        sidecar_ready = any(cs.name == "sidecar" and cs.ready for cs in pod.status.container_statuses)
                        if sidecar_ready:
                            return True

                elif pod.status.phase in ("Failed", "Succeeded"):
                    return False

            except ApiException:
                pass

            await asyncio.sleep(0.5)

        return False

    async def _delete_pod(self, handle: PodHandle):
        """Delete a pod."""
        core_api = get_core_api()
        if not core_api:
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: core_api.delete_namespaced_pod(
                    handle.name,
                    handle.namespace,
                ),
            )
            logger.debug("Deleted pod", pod_name=handle.name)

        except ApiException as e:
            if e.status != 404:
                logger.warning(
                    "Failed to delete pod",
                    pod_name=handle.name,
                    error=str(e),
                )

    async def _replenish_loop(self):
        """Background task to maintain pool size."""
        while self._running:
            try:
                await asyncio.sleep(5)

                async with self._lock:
                    available_count = sum(1 for p in self._pods.values() if p.is_available)

                if available_count < self.pool_size:
                    needed = self.pool_size - available_count
                    logger.debug(
                        "Replenishing pool",
                        language=self.language,
                        available=available_count,
                        needed=needed,
                    )
                    for _ in range(min(needed, 3)):
                        await self._create_warm_pod()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error in replenish loop",
                    language=self.language,
                    error=str(e),
                )

    async def _health_check_loop(self):
        """Background task to check pod health."""
        while self._running:
            try:
                await asyncio.sleep(30)

                async with self._lock:
                    pods_to_check = [p for p in self._pods.values() if p.is_available]

                client = await self._get_http_client()

                for pooled_pod in pods_to_check:
                    try:
                        url = pooled_pod.handle.sidecar_url
                        response = await client.get(
                            f"{url}/health",
                            timeout=5,
                        )
                        if response.status_code != 200:
                            pooled_pod.health_check_failures += 1
                        else:
                            pooled_pod.health_check_failures = 0

                    except Exception:
                        pooled_pod.health_check_failures += 1

                    # Remove unhealthy pods
                    if pooled_pod.health_check_failures >= 3:
                        logger.warning(
                            "Removing unhealthy pod",
                            pod_name=pooled_pod.handle.name,
                        )
                        async with self._lock:
                            if pooled_pod.handle.uid in self._pods:
                                del self._pods[pooled_pod.handle.uid]
                        await self._delete_pod(pooled_pod.handle)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Error in health check loop",
                    language=self.language,
                    error=str(e),
                )

    async def acquire(self, session_id: str, timeout: int = 10) -> PodHandle | None:
        """Acquire a warm pod from the pool.

        Args:
            session_id: Session identifier
            timeout: Maximum wait time

        Returns:
            PodHandle if a pod was acquired, None otherwise
        """
        try:
            pod_uid = await asyncio.wait_for(
                self._available.get(),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "Timeout acquiring pod from pool",
                language=self.language,
                session_id=session_id[:12],
            )
            return None

        async with self._lock:
            pooled_pod = self._pods.get(pod_uid)
            if not pooled_pod:
                return None

            pooled_pod.acquired = True
            pooled_pod.acquired_at = datetime.now(UTC)
            pooled_pod.handle.status = PodStatus.EXECUTING
            pooled_pod.handle.session_id = session_id

            self._session_pods[session_id] = pod_uid

            logger.debug(
                "Acquired pod from pool",
                pod_name=pooled_pod.handle.name,
                language=self.language,
                session_id=session_id[:12],
            )

            return pooled_pod.handle

    async def release(self, handle: PodHandle, destroy: bool = True):
        """Release a pod back to the pool or destroy it.

        Args:
            handle: Pod handle
            destroy: If True, destroy the pod instead of returning to pool
        """
        async with self._lock:
            pooled_pod = self._pods.get(handle.uid)
            if not pooled_pod:
                return

            # Remove from session tracking
            if handle.session_id and handle.session_id in self._session_pods:
                del self._session_pods[handle.session_id]

            if destroy:
                # Remove from pool and delete
                del self._pods[handle.uid]
                await self._delete_pod(handle)
                logger.debug(
                    "Destroyed pod after execution",
                    pod_name=handle.name,
                )
            else:
                # Return to pool (reset state)
                pooled_pod.acquired = False
                pooled_pod.acquired_at = None
                pooled_pod.handle.status = PodStatus.WARM
                pooled_pod.handle.session_id = None
                await self._available.put(handle.uid)
                logger.debug(
                    "Released pod back to pool",
                    pod_name=handle.name,
                )

    async def execute(
        self,
        handle: PodHandle,
        code: str,
        timeout: int = 30,
        files: list[FileData] | None = None,
        initial_state: str | None = None,
        capture_state: bool = False,
    ) -> ExecutionResult:
        """Execute code in an acquired pod.

        Args:
            handle: Pod handle (must be acquired)
            code: Code to execute
            timeout: Execution timeout
            files: Files to upload
            initial_state: State to restore
            capture_state: Whether to capture state

        Returns:
            ExecutionResult
        """
        if not handle.pod_ip:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr="Pod not ready",
                execution_time_ms=0,
            )

        client = await self._get_http_client()
        sidecar_url = handle.sidecar_url

        # Upload files if provided
        if files:
            for file_data in files:
                try:
                    await client.post(
                        f"{sidecar_url}/files",
                        files={"files": (file_data.filename, file_data.content)},
                        timeout=30,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to upload file",
                        filename=file_data.filename,
                        error=str(e),
                    )

        # Execute code
        try:
            request_data = {
                "code": code,
                "timeout": timeout,
                "working_dir": "/mnt/data",
            }
            if initial_state:
                request_data["initial_state"] = initial_state
            if capture_state:
                request_data["capture_state"] = True

            response = await client.post(
                f"{sidecar_url}/execute",
                json=request_data,
                timeout=timeout + 10,
            )

            if response.status_code == 200:
                data = response.json()
                return ExecutionResult(
                    exit_code=data.get("exit_code", 0),
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    execution_time_ms=data.get("execution_time_ms", 0),
                    state=data.get("state"),
                    state_errors=data.get("state_errors"),
                )
            else:
                return ExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"Sidecar error: {response.status_code}",
                    execution_time_ms=0,
                )

        except httpx.TimeoutException:
            return ExecutionResult(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {timeout} seconds",
                execution_time_ms=timeout * 1000,
            )
        except Exception as e:
            logger.error(
                "Execution request failed",
                pod_name=handle.name,
                error=str(e),
            )
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {str(e)}",
                execution_time_ms=0,
            )

    @property
    def available_count(self) -> int:
        """Get number of available pods."""
        return sum(1 for p in self._pods.values() if p.is_available)

    @property
    def total_count(self) -> int:
        """Get total number of pods."""
        return len(self._pods)


class PodPoolManager:
    """Manages multiple pod pools for different languages."""

    def __init__(
        self,
        namespace: str | None = None,
        configs: list[PoolConfig] | None = None,
    ):
        """Initialize the pool manager.

        Args:
            namespace: Kubernetes namespace
            configs: Pool configurations per language
        """
        self.namespace = namespace or get_current_namespace()
        self._pools: dict[str, PodPool] = {}
        self._configs: dict[str, PoolConfig] = {}

        if configs:
            for config in configs:
                self._configs[config.language] = config
                if config.uses_pool:
                    self._pools[config.language] = PodPool(config, self.namespace)

    async def start(self):
        """Start all pools."""
        for pool in self._pools.values():
            await pool.start()

    async def stop(self):
        """Stop all pools."""
        for pool in self._pools.values():
            await pool.stop()

    def get_pool(self, language: str) -> PodPool | None:
        """Get the pool for a language."""
        return self._pools.get(language)

    def get_config(self, language: str) -> PoolConfig | None:
        """Get the configuration for a language."""
        return self._configs.get(language)

    def uses_pool(self, language: str) -> bool:
        """Check if a language uses a warm pod pool."""
        config = self._configs.get(language)
        return config is not None and config.uses_pool

    async def acquire(
        self,
        language: str,
        session_id: str,
        timeout: int = 10,
    ) -> PodHandle | None:
        """Acquire a pod from the appropriate pool.

        Args:
            language: Programming language
            session_id: Session identifier
            timeout: Maximum wait time

        Returns:
            PodHandle if acquired, None if pool doesn't exist or timeout
        """
        pool = self._pools.get(language)
        if not pool:
            return None
        return await pool.acquire(session_id, timeout)

    async def release(self, handle: PodHandle, destroy: bool = True):
        """Release a pod back to its pool or destroy it."""
        pool = self._pools.get(handle.language)
        if pool:
            await pool.release(handle, destroy)

    async def execute(
        self,
        handle: PodHandle,
        code: str,
        timeout: int = 30,
        files: list[FileData] | None = None,
        initial_state: str | None = None,
        capture_state: bool = False,
    ) -> ExecutionResult:
        """Execute code in an acquired pod."""
        pool = self._pools.get(handle.language)
        if not pool:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"No pool for language: {handle.language}",
                execution_time_ms=0,
            )
        return await pool.execute(
            handle,
            code,
            timeout,
            files,
            initial_state,
            capture_state,
        )

    def get_pool_stats(self) -> dict[str, dict[str, int]]:
        """Get statistics for all pools."""
        stats = {}
        for lang, pool in self._pools.items():
            stats[lang] = {
                "available": pool.available_count,
                "total": pool.total_count,
                "target": pool.pool_size,
            }
        return stats
