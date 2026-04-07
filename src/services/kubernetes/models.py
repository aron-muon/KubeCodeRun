"""Data models for Kubernetes execution.

These models represent pods, execution results, and related types
used throughout the Kubernetes execution layer.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class PodStatus(str, Enum):
    """Status of an execution pod."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"

    # Pool-specific states
    WARM = "warm"  # Ready in pool, waiting for work
    SPECIALIZING = "specializing"  # Being assigned to a session
    EXECUTING = "executing"  # Currently running code


@dataclass
class PodHandle:
    """Handle to a Kubernetes pod for execution.

    Provides the necessary information to communicate with and
    manage an execution pod via its runner HTTP API.
    """

    name: str
    namespace: str
    uid: str
    language: str
    session_id: str | None = None
    status: PodStatus = PodStatus.PENDING
    pod_ip: str | None = None
    runner_port: int = 8080
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def runner_url(self) -> str:
        """Get the URL for the runner HTTP API."""
        if self.pod_ip:
            return f"http://{self.pod_ip}:{self.runner_port}"
        return f"http://{self.name}.{self.namespace}:{self.runner_port}"

    @property
    def id(self) -> str:
        """Compatibility property for code expecting container.id."""
        return self.uid

    def __hash__(self):
        return hash(self.uid)

    def __eq__(self, other):
        if isinstance(other, PodHandle):
            return self.uid == other.uid
        return False


@dataclass
class ExecutionResult:
    """Result of code execution in a pod.

    This matches the response from the runner HTTP API.
    """

    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int
    state: str | None = None  # Base64-encoded state
    state_errors: list[str] | None = None


@dataclass
class FileData:
    """File to be uploaded to a pod."""

    filename: str
    content: bytes
    session_id: str | None = None


@dataclass
class PodSpec:
    """Specification for creating an execution pod."""

    language: str
    image: str
    session_id: str | None = None
    namespace: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)

    # Resource limits
    cpu_limit: str = "1"
    memory_limit: str = "512Mi"
    cpu_request: str = "100m"
    memory_request: str = "128Mi"

    # Security context
    run_as_user: int = 65532
    run_as_group: int = 65532
    run_as_non_root: bool = True
    seccomp_profile_type: str = "RuntimeDefault"

    # Runner HTTP API port
    runner_port: int = 8080

    # Network isolation mode - disables network-dependent features (e.g., Go module proxy)
    network_isolated: bool = False

    # Pod scheduling (for targeting sandboxed node pools, etc.)
    runtime_class_name: str = ""
    pod_node_selector: str = ""  # JSON-encoded dict
    pod_tolerations: str = ""  # JSON-encoded list

    # Image pull secrets for private registries
    image_pull_secrets: str = ""  # Comma-separated secret names


@dataclass
class PoolConfig:
    """Configuration for a language pool."""

    language: str
    image: str
    pool_size: int = 0  # 0 = use Jobs instead of pool

    # Resource limits (can override defaults)
    cpu_limit: str | None = None
    memory_limit: str | None = None

    # Image pull policy (Always, IfNotPresent, Never)
    image_pull_policy: str = "Always"

    # Seccomp profile type (RuntimeDefault, Unconfined, Localhost)
    seccomp_profile_type: str = "RuntimeDefault"

    # Network isolation mode - disables network-dependent features (e.g., Go module proxy)
    network_isolated: bool = False

    # Pod scheduling (for targeting sandboxed node pools, etc.)
    runtime_class_name: str = ""
    pod_node_selector: str = ""  # JSON-encoded dict
    pod_tolerations: str = ""  # JSON-encoded list

    # Image pull secrets for private registries
    image_pull_secrets: str = ""  # Comma-separated secret names

    @property
    def uses_pool(self) -> bool:
        """Whether this language uses a warm pod pool."""
        return self.pool_size > 0


@dataclass
class PooledPod:
    """A pod in the warm pool."""

    handle: PodHandle
    language: str
    acquired: bool = False
    acquired_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    health_check_failures: int = 0

    @property
    def is_available(self) -> bool:
        """Check if pod is available for acquisition."""
        return not self.acquired and self.handle.status == PodStatus.WARM


@dataclass
class JobHandle:
    """Handle to a Kubernetes Job for execution.

    Used for cold-path languages where we create a Job per execution
    rather than using a warm pod pool.
    """

    name: str
    namespace: str
    uid: str
    language: str
    session_id: str
    pod_name: str | None = None
    pod_ip: str | None = None
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @property
    def runner_url(self) -> str | None:
        """Get the URL for the runner HTTP API."""
        if self.pod_ip:
            return f"http://{self.pod_ip}:8080"
        return None

    @property
    def id(self) -> str:
        """Compatibility property."""
        return self.uid
