"""Kubernetes-specific configuration.

This module provides Kubernetes-related configuration for pod execution,
including namespace, resource limits, and RBAC.
"""

from dataclasses import dataclass


@dataclass
class KubernetesConfig:
    """Kubernetes execution configuration."""

    # Namespace for execution pods (defaults to API's namespace if empty)
    namespace: str = ""

    # Service account for execution pods
    service_account: str = "kubecoderun-executor"

    # Resource limits for execution pods
    cpu_limit: str = "1"
    memory_limit: str = "512Mi"
    cpu_request: str = "100m"
    memory_request: str = "128Mi"

    # Security context
    run_as_user: int = 65532
    run_as_group: int = 65532
    run_as_non_root: bool = True
    seccomp_profile_type: str = "RuntimeDefault"

    # Pod scheduling (for targeting sandboxed node pools, etc.)
    runtime_class_name: str = ""
    pod_node_selector: str = ""  # JSON-encoded dict, e.g. '{"sandbox": "true"}'
    pod_tolerations: str = ""  # JSON-encoded list, e.g. '[{"key":"sandbox","operator":"Exists","effect":"NoSchedule"}]'

    # Extra pod labels (JSON-encoded dict) and language-suffix keys (JSON-encoded list)
    pod_labels: str = ""  # e.g. '{"team":"platform"}'
    pod_label_language_suffix: str = ""  # e.g. '["team"]' → appends "-py", "-js", etc.

    # Job settings (for languages with pool_size=0)
    job_ttl_seconds_after_finished: int = 60
    job_active_deadline_seconds: int = 300

    # Pod pool configuration
    pool_replenish_interval_seconds: int = 2
    pool_health_check_interval_seconds: int = 30

    # Image registry configuration
    # Format: {image_registry}-{language}:{image_tag}
    # e.g., aronmuon/kubecoderun-python:latest
    image_registry: str = "aronmuon/kubecoderun"
    image_tag: str = "latest"

    def get_image_for_language(self, language: str) -> str:
        """Get the container image for a language.

        Args:
            language: Programming language code

        Returns:
            Full image URL (format: {registry}-{language}:{tag})
        """
        # Map language codes to image names
        image_map = {
            "py": "python",
            "python": "python",
            "js": "javascript",
            "javascript": "javascript",
            "ts": "typescript",
            "typescript": "typescript",
            "go": "go",
            "java": "java",
            "c": "c-cpp",
            "cpp": "c-cpp",
            "php": "php",
            "rs": "rust",
            "rust": "rust",
            "r": "r",
            "f90": "fortran",
            "fortran": "fortran",
            "d": "d",
        }

        image_name = image_map.get(language.lower(), language.lower())
        return f"{self.image_registry}-{image_name}:{self.image_tag}"
