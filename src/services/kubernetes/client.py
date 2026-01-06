"""Kubernetes client factory.

Provides configured Kubernetes API clients for pod and job management.
Supports both in-cluster and out-of-cluster (kubeconfig) authentication.
"""

import os
from functools import lru_cache
from typing import Optional, Tuple

import structlog
from kubernetes import client, config
from kubernetes.client import (
    CoreV1Api,
    BatchV1Api,
    ApiException,
)

logger = structlog.get_logger(__name__)

# Global client instances
_core_api: Optional[CoreV1Api] = None
_batch_api: Optional[BatchV1Api] = None
_initialized: bool = False
_init_error: Optional[str] = None


def _load_config() -> bool:
    """Load Kubernetes configuration.

    Tries in-cluster config first, falls back to kubeconfig.

    Returns:
        True if configuration was loaded successfully.
    """
    global _init_error

    # Try in-cluster config first (when running in a pod)
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
        return True
    except config.ConfigException:
        pass

    # Try kubeconfig file
    kubeconfig_path = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
    try:
        config.load_kube_config(config_file=kubeconfig_path)
        logger.info("Loaded kubeconfig", path=kubeconfig_path)
        return True
    except Exception as e:
        _init_error = f"Failed to load Kubernetes config: {e}"
        logger.error(_init_error)
        return False


def initialize_client() -> bool:
    """Initialize the Kubernetes client.

    Returns:
        True if initialization was successful.
    """
    global _core_api, _batch_api, _initialized, _init_error

    if _initialized:
        return _core_api is not None

    if not _load_config():
        _initialized = True
        return False

    try:
        _core_api = CoreV1Api()
        _batch_api = BatchV1Api()
        _initialized = True

        # Test the connection
        _core_api.get_api_resources()
        logger.info("Kubernetes client initialized successfully")
        return True

    except ApiException as e:
        _init_error = f"Kubernetes API error: {e.reason}"
        logger.error(_init_error)
        _initialized = True
        return False
    except Exception as e:
        _init_error = f"Failed to initialize Kubernetes client: {e}"
        logger.error(_init_error)
        _initialized = True
        return False


def get_kubernetes_client() -> Tuple[Optional[CoreV1Api], Optional[BatchV1Api]]:
    """Get the Kubernetes API clients.

    Returns:
        Tuple of (CoreV1Api, BatchV1Api) or (None, None) if not available.
    """
    if not _initialized:
        initialize_client()

    return _core_api, _batch_api


def get_core_api() -> Optional[CoreV1Api]:
    """Get the Core V1 API client for pod operations."""
    core, _ = get_kubernetes_client()
    return core


def get_batch_api() -> Optional[BatchV1Api]:
    """Get the Batch V1 API client for job operations."""
    _, batch = get_kubernetes_client()
    return batch


def is_available() -> bool:
    """Check if Kubernetes client is available."""
    if not _initialized:
        initialize_client()
    return _core_api is not None


def get_initialization_error() -> Optional[str]:
    """Get the initialization error message if any."""
    return _init_error


def get_current_namespace() -> str:
    """Get the current namespace.

    When running in-cluster, reads from the service account.
    Otherwise, uses the default namespace or NAMESPACE env var.
    """
    # Check environment variable first
    namespace = os.getenv("NAMESPACE", os.getenv("POD_NAMESPACE"))
    if namespace:
        return namespace

    # Try to read from service account (in-cluster)
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            return f.read().strip()
    except (FileNotFoundError, IOError):
        pass

    # Default namespace
    return "default"


class KubernetesClientContext:
    """Context manager for Kubernetes operations.

    Provides convenient access to API clients with error handling.
    """

    def __init__(self):
        self.core_api: Optional[CoreV1Api] = None
        self.batch_api: Optional[BatchV1Api] = None
        self.namespace: str = get_current_namespace()

    def __enter__(self):
        self.core_api, self.batch_api = get_kubernetes_client()
        if not self.core_api:
            raise RuntimeError(
                f"Kubernetes client not available: {get_initialization_error()}"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # No cleanup needed for now
        pass


def create_pod_manifest(
    name: str,
    namespace: str,
    main_image: str,
    sidecar_image: str,
    language: str,
    labels: dict,
    annotations: dict = None,
    cpu_limit: str = "1",
    memory_limit: str = "512Mi",
    cpu_request: str = "100m",
    memory_request: str = "128Mi",
    run_as_user: int = 1000,
    sidecar_port: int = 8080,
    image_pull_policy: str = "Always",
) -> client.V1Pod:
    """Create a Pod manifest for code execution.

    Args:
        name: Pod name
        namespace: Kubernetes namespace
        main_image: Image for the main (language) container
        sidecar_image: Image for the sidecar container
        language: Programming language
        labels: Pod labels
        annotations: Pod annotations
        cpu_limit: CPU limit
        memory_limit: Memory limit
        cpu_request: CPU request
        memory_request: Memory request
        run_as_user: UID to run containers as
        sidecar_port: Port for sidecar HTTP API

    Returns:
        V1Pod manifest ready for creation.
    """
    # Shared volume for code and data
    shared_volume = client.V1Volume(
        name="shared-data",
        empty_dir=client.V1EmptyDirVolumeSource(
            medium="",
            size_limit="1Gi",
        ),
    )

    shared_mount = client.V1VolumeMount(
        name="shared-data",
        mount_path="/mnt/data",
    )

    # Security context for main container
    security_context = client.V1SecurityContext(
        run_as_user=run_as_user,
        run_as_group=run_as_user,
        run_as_non_root=True,
        allow_privilege_escalation=False,
        capabilities=client.V1Capabilities(drop=["ALL"]),
    )

    # Security context for sidecar - needs elevated privileges for nsenter
    #
    # The sidecar uses nsenter to execute code in the main container's mount namespace.
    # nsenter requires these capabilities:
    # - SYS_PTRACE: access /proc/<pid>/ns/ of other processes
    # - SYS_ADMIN: call setns() to enter namespaces
    # - SYS_CHROOT: required for mount namespace operations
    #
    # For non-root users, Linux capabilities only populate the bounding set, not
    # effective/permitted sets. To make capabilities usable, the sidecar Docker image
    # uses setcap on the nsenter binary:
    #   setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter
    #
    # The pod spec must still:
    # - Add capabilities to the bounding set (capabilities.add)
    # - Allow privilege escalation (for file capabilities to be honored)
    #
    # This approach allows running as non-root while still having nsenter work.
    sidecar_security_context = client.V1SecurityContext(
        run_as_user=run_as_user,
        run_as_group=run_as_user,
        run_as_non_root=True,
        allow_privilege_escalation=True,  # Required for file capabilities
        capabilities=client.V1Capabilities(
            add=["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"],
            drop=["ALL"],
        ),
    )

    # Resource requirements
    resources = client.V1ResourceRequirements(
        limits={"cpu": cpu_limit, "memory": memory_limit},
        requests={"cpu": cpu_request, "memory": memory_request},
    )

    # Main container (language runtime)
    main_container = client.V1Container(
        name="main",
        image=main_image,
        image_pull_policy=image_pull_policy,
        volume_mounts=[shared_mount],
        security_context=security_context,
        resources=resources,
        env=[
            client.V1EnvVar(name="PYTHONUNBUFFERED", value="1"),
            client.V1EnvVar(name="HOME", value="/mnt/data"),
        ],
        # Main container can just sleep, sidecar handles execution
        command=["sleep", "infinity"],
    )

    # Sidecar container (HTTP API)
    sidecar_container = client.V1Container(
        name="sidecar",
        image=sidecar_image,
        image_pull_policy=image_pull_policy,
        ports=[client.V1ContainerPort(container_port=sidecar_port, name="http")],
        volume_mounts=[shared_mount],
        security_context=sidecar_security_context,
        resources=client.V1ResourceRequirements(
            # Memory increased to handle memory-heavy executions like TypeScript compilation
            # The spawned nsenter process runs in the sidecar's cgroup, not the main container's
            limits={"cpu": "500m", "memory": "512Mi"},
            requests={"cpu": "100m", "memory": "256Mi"},
        ),
        env=[
            client.V1EnvVar(name="LANGUAGE", value=language),
            client.V1EnvVar(name="WORKING_DIR", value="/mnt/data"),
            client.V1EnvVar(name="SIDECAR_PORT", value=str(sidecar_port)),
        ],
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/ready", port=sidecar_port),
            initial_delay_seconds=5,
            period_seconds=3,
            timeout_seconds=5,
            failure_threshold=5,
        ),
        liveness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/health", port=sidecar_port),
            initial_delay_seconds=5,
            period_seconds=10,
            timeout_seconds=5,
            failure_threshold=3,
        ),
    )

    # Pod spec
    pod_spec = client.V1PodSpec(
        containers=[main_container, sidecar_container],
        volumes=[shared_volume],
        restart_policy="Never",
        termination_grace_period_seconds=10,
        # Share process namespace so sidecar can use nsenter to execute in main container
        share_process_namespace=True,
        security_context=client.V1PodSecurityContext(
            # Note: We don't set run_as_user at pod level; each container
            # sets its own security context. Both run as non-root UID 1000.
            # The sidecar uses file capabilities (setcap) on nsenter for privileges.
            fs_group=run_as_user,
        ),
        # Prevent scheduling on same node as other execution pods
        # (optional, can be configured via affinity)
    )

    # Pod metadata
    metadata = client.V1ObjectMeta(
        name=name,
        namespace=namespace,
        labels=labels,
        annotations=annotations or {},
    )

    return client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=metadata,
        spec=pod_spec,
    )


def create_job_manifest(
    name: str,
    namespace: str,
    main_image: str,
    sidecar_image: str,
    language: str,
    labels: dict,
    ttl_seconds_after_finished: int = 60,
    active_deadline_seconds: int = 300,
    **kwargs,
) -> client.V1Job:
    """Create a Job manifest for code execution.

    Jobs are used for cold-path languages where we don't maintain
    a warm pod pool.

    Args:
        name: Job name
        namespace: Kubernetes namespace
        main_image: Image for the main container
        sidecar_image: Image for the sidecar container
        language: Programming language
        labels: Job labels
        ttl_seconds_after_finished: TTL for completed jobs
        active_deadline_seconds: Maximum execution time

    Returns:
        V1Job manifest ready for creation.
    """
    # Create pod template using the same logic
    pod = create_pod_manifest(
        name=f"{name}-pod",
        namespace=namespace,
        main_image=main_image,
        sidecar_image=sidecar_image,
        language=language,
        labels=labels,
        **kwargs,
    )

    # Job spec
    job_spec = client.V1JobSpec(
        template=client.V1PodTemplateSpec(
            metadata=pod.metadata,
            spec=pod.spec,
        ),
        backoff_limit=0,  # Don't retry failed jobs
        ttl_seconds_after_finished=ttl_seconds_after_finished,
        active_deadline_seconds=active_deadline_seconds,
    )

    # Job metadata
    metadata = client.V1ObjectMeta(
        name=name,
        namespace=namespace,
        labels=labels,
    )

    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=metadata,
        spec=job_spec,
    )
