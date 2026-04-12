"""Kubernetes Job executor for cold-path languages.

For languages without a warm pod pool (poolSize=0), we create a Job
for each execution. This has higher latency but simpler management.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
import structlog
from kubernetes.client import ApiException

from .client import (
    create_job_manifest,
    get_batch_api,
    get_core_api,
    get_current_namespace,
)
from .models import (
    ExecutionResult,
    FileData,
    JobHandle,
    PodSpec,
)

logger = structlog.get_logger(__name__)


class JobExecutor:
    """Executes code using Kubernetes Jobs.

    Creates a Job for each execution request, waits for pod readiness,
    executes code via the runner HTTP API, and cleans up.
    """

    def __init__(
        self,
        namespace: str | None = None,
        ttl_seconds_after_finished: int = 60,
        active_deadline_seconds: int = 300,
    ):
        """Initialize the Job executor.

        Args:
            namespace: Kubernetes namespace for jobs
            ttl_seconds_after_finished: TTL for completed jobs
            active_deadline_seconds: Maximum execution time
        """
        self.namespace = namespace or get_current_namespace()
        self.ttl_seconds_after_finished = ttl_seconds_after_finished
        self.active_deadline_seconds = active_deadline_seconds
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for runner communication."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        """Close resources."""
        if self._http_client:
            await self._http_client.aclose()

    def _generate_job_name(self, session_id: str, language: str) -> str:
        """Generate a unique job name."""
        short_uuid = uuid4().hex[:8]
        # Kubernetes names must be lowercase, alphanumeric, and max 63 chars
        safe_session = session_id[:12].lower().replace("_", "-")
        return f"exec-{language}-{safe_session}-{short_uuid}"

    async def create_job(
        self,
        spec: PodSpec,
        session_id: str,
    ) -> JobHandle:
        """Create a Kubernetes Job for code execution.

        Args:
            spec: Pod specification
            session_id: Session identifier

        Returns:
            JobHandle for the created job
        """
        batch_api = get_batch_api()
        if not batch_api:
            raise RuntimeError("Kubernetes Batch API not available")

        job_name = self._generate_job_name(session_id, spec.language)
        namespace = spec.namespace or self.namespace

        labels = {
            "app.kubernetes.io/name": "kubecoderun",
            "app.kubernetes.io/component": "execution",
            "app.kubernetes.io/managed-by": "kubecoderun",
            "kubecoderun.io/language": spec.language,
            "kubecoderun.io/session-id": session_id[:63],
            "kubecoderun.io/type": "job",
            **spec.labels,
        }

        job_manifest = create_job_manifest(
            name=job_name,
            namespace=namespace,
            main_image=spec.image,
            language=spec.language,
            labels=labels,
            cpu_limit=spec.cpu_limit,
            memory_limit=spec.memory_limit,
            cpu_request=spec.cpu_request,
            memory_request=spec.memory_request,
            run_as_user=spec.run_as_user,
            runner_port=spec.runner_port,
            seccomp_profile_type=spec.seccomp_profile_type,
            network_isolated=spec.network_isolated,
            runtime_class_name=spec.runtime_class_name,
            pod_node_selector=spec.pod_node_selector,
            pod_tolerations=spec.pod_tolerations,
            image_pull_secrets=spec.image_pull_secrets,
            ttl_seconds_after_finished=self.ttl_seconds_after_finished,
            active_deadline_seconds=self.active_deadline_seconds,
        )

        try:
            loop = asyncio.get_event_loop()
            job = await loop.run_in_executor(
                None,
                lambda: batch_api.create_namespaced_job(namespace, job_manifest),
            )

            logger.info(
                "Created execution job",
                job_name=job_name,
                namespace=namespace,
                language=spec.language,
                session_id=session_id[:12],
            )

            return JobHandle(
                name=job_name,
                namespace=namespace,
                uid=job.metadata.uid,
                language=spec.language,
                session_id=session_id,
            )

        except ApiException as e:
            logger.error(
                "Failed to create job",
                job_name=job_name,
                error=str(e),
            )
            raise RuntimeError(f"Failed to create job: {e.reason}")

    async def wait_for_pod_ready(
        self,
        job: JobHandle,
        timeout: int = 60,
    ) -> bool:
        """Wait for the job's pod to be ready.

        Args:
            job: Job handle
            timeout: Maximum wait time in seconds

        Returns:
            True if pod is ready, False otherwise
        """
        core_api = get_core_api()
        if not core_api:
            return False

        label_selector = f"job-name={job.name}"
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                loop = asyncio.get_event_loop()
                pods = await loop.run_in_executor(
                    None,
                    lambda: core_api.list_namespaced_pod(
                        job.namespace,
                        label_selector=label_selector,
                    ),
                )

                if pods.items:
                    pod = pods.items[0]
                    job.pod_name = pod.metadata.name
                    job.pod_ip = pod.status.pod_ip

                    # Check if pod is ready
                    if pod.status.phase == "Running":
                        # Check container readiness
                        if pod.status.container_statuses:
                            main_ready = any(cs.name == "main" and cs.ready for cs in pod.status.container_statuses)
                            if main_ready:
                                job.status = "running"
                                logger.info(
                                    "Job pod ready",
                                    job_name=job.name,
                                    pod_name=job.pod_name,
                                    pod_ip=job.pod_ip,
                                    elapsed_seconds=round(asyncio.get_event_loop().time() - start_time, 2),
                                )
                                return True

                    elif pod.status.phase in ("Failed", "Succeeded"):
                        job.status = "failed"
                        logger.warning(
                            "Job pod failed",
                            job_name=job.name,
                            phase=pod.status.phase,
                        )
                        return False

            except ApiException as e:
                logger.warning(
                    "Error checking pod status",
                    job_name=job.name,
                    error=str(e),
                )

            await asyncio.sleep(0.5)

        logger.warning(
            "Timeout waiting for job pod",
            job_name=job.name,
            timeout=timeout,
        )
        return False

    async def execute(
        self,
        job: JobHandle,
        code: str,
        timeout: int = 30,
        files: list[FileData] | None = None,
        initial_state: str | None = None,
        capture_state: bool = False,
    ) -> ExecutionResult:
        """Execute code in the job's pod.

        Args:
            job: Job handle with ready pod
            code: Code to execute
            timeout: Execution timeout
            files: Files to upload before execution
            initial_state: State to restore
            capture_state: Whether to capture state after execution

        Returns:
            ExecutionResult with stdout, stderr, exit code
        """
        if not job.pod_ip:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr="Job pod not ready",
                execution_time_ms=0,
            )

        runner_url = job.runner_url
        if not runner_url:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr="Job runner URL not available",
                execution_time_ms=0,
            )

        client = await self._get_http_client()

        # Upload files if provided
        if files:
            await self._upload_files(client, runner_url, files)

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

            logger.debug(
                "Sending execute request",
                runner_url=runner_url,
                code_len=len(code),
                timeout=timeout,
            )

            response = await client.post(
                f"{runner_url}/execute",
                json=request_data,
                timeout=timeout + 10,  # Extra time for network
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
                    stderr=f"Runner error: {response.status_code} - {response.text}",
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
                job_name=job.name,
                error=str(e),
            )
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {str(e)}",
                execution_time_ms=0,
            )

    async def _upload_files(
        self,
        client: httpx.AsyncClient,
        runner_url: str,
        files: list[FileData],
    ):
        """Upload files to the pod."""
        for file_data in files:
            try:
                files_payload = {"files": (file_data.filename, file_data.content)}
                await client.post(
                    f"{runner_url}/files",
                    files=files_payload,
                    timeout=30,
                )
            except Exception as e:
                logger.warning(
                    "Failed to upload file",
                    filename=file_data.filename,
                    error=str(e),
                )

    async def delete_job(self, job: JobHandle):
        """Delete a job and its pods.

        Args:
            job: Job handle to delete
        """
        batch_api = get_batch_api()
        if not batch_api:
            return

        try:
            from kubernetes.client import V1DeleteOptions

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: batch_api.delete_namespaced_job(
                    job.name,
                    job.namespace,
                    body=V1DeleteOptions(
                        propagation_policy="Background",
                    ),
                ),
            )
            logger.debug("Deleted job", job_name=job.name)

        except ApiException as e:
            if e.status != 404:
                logger.warning(
                    "Failed to delete job",
                    job_name=job.name,
                    error=str(e),
                )

    async def _collect_generated_files(
        self,
        job: JobHandle,
        uploaded_files: list[FileData] | None = None,
    ) -> list[dict[str, Any]]:
        """Detect and download generated files from a Job pod before cleanup.

        For Job-based execution the pod is destroyed after this method returns,
        so we must download file contents here.

        Args:
            job: Job handle with ready pod
            uploaded_files: Files that were uploaded before execution (to exclude)

        Returns:
            List of dicts with keys: name, path, size, content (bytes)
        """
        from ...config import settings
        from ...services.execution.runner import _CODE_FILENAMES

        runner_url = job.runner_url
        if not runner_url:
            return []

        uploaded_names = set()
        if uploaded_files:
            for f in uploaded_files:
                uploaded_names.add(f.filename)

        try:
            client = await self._get_http_client()

            # List files in the working directory
            response = await client.get(f"{runner_url}/files", timeout=10)
            if response.status_code != 200:
                return []

            all_files = response.json().get("files", [])
            collected = []

            for f in all_files:
                name = f.get("name", "")
                if not name or name in _CODE_FILENAMES or name in uploaded_names:
                    continue
                size = f.get("size", 0)
                if size <= 0 or size > settings.max_file_size_mb * 1024 * 1024:
                    continue

                # Download file content
                try:
                    dl_response = await client.get(
                        f"{runner_url}/files/{name}",
                        timeout=30,
                    )
                    if dl_response.status_code == 200:
                        collected.append(
                            {
                                "name": name,
                                "path": f"/mnt/data/{name}",
                                "size": size,
                                "content": dl_response.content,
                            }
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to download generated file from job",
                        job_name=job.name,
                        filename=name,
                        error=str(e),
                    )

                if len(collected) >= settings.max_output_files:
                    break

            return collected

        except Exception as e:
            logger.warning(
                "Failed to collect generated files from job",
                job_name=job.name,
                error=str(e),
            )
            return []

    async def execute_with_job(
        self,
        spec: PodSpec,
        session_id: str,
        code: str,
        timeout: int = 30,
        files: list[FileData] | None = None,
        initial_state: str | None = None,
        capture_state: bool = False,
    ) -> ExecutionResult:
        """Execute code by creating a job, waiting for ready, executing, and cleaning up.

        This is the main entry point for job-based execution.

        Args:
            spec: Pod specification
            session_id: Session identifier
            code: Code to execute
            timeout: Execution timeout
            files: Files to upload
            initial_state: State to restore
            capture_state: Whether to capture state

        Returns:
            ExecutionResult
        """
        job = None
        try:
            # Create job
            job = await self.create_job(spec, session_id)

            # Wait for pod ready
            ready = await self.wait_for_pod_ready(job, timeout=60)
            if not ready:
                return ExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr="Job pod failed to start",
                    execution_time_ms=0,
                )

            # Log the job state before executing
            logger.info(
                "Job ready, starting execution",
                job_name=job.name,
                pod_name=job.pod_name,
                pod_ip=job.pod_ip,
                runner_url=job.runner_url,
            )

            # Execute code
            result = await self.execute(
                job,
                code,
                timeout=timeout,
                files=files,
                initial_state=initial_state,
                capture_state=capture_state,
            )

            # Detect and download generated files before job cleanup
            if job.runner_url and result.exit_code == 0:
                result.generated_files = await self._collect_generated_files(job, files)

            logger.info(
                "Job execution completed",
                job_name=job.name,
                exit_code=result.exit_code,
                stdout_len=len(result.stdout),
                stderr_len=len(result.stderr),
                stderr_preview=result.stderr[:200] if result.stderr else "",
                generated_files_count=len(result.generated_files) if result.generated_files else 0,
            )

            return result

        finally:
            # Clean up job (TTL will also handle this)
            if job:
                asyncio.create_task(self.delete_job(job))
