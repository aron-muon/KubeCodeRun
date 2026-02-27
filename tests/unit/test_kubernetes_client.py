"""Unit tests for Kubernetes client factory."""

import os
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client import ApiException

from src.services.kubernetes import client


@pytest.fixture(autouse=True)
def reset_client_state():
    """Reset global client state before each test."""
    # Save original state
    orig_core = client._core_api
    orig_batch = client._batch_api
    orig_init = client._initialized
    orig_error = client._init_error

    # Reset to uninitialized state
    client._core_api = None
    client._batch_api = None
    client._initialized = False
    client._init_error = None

    yield

    # Restore original state
    client._core_api = orig_core
    client._batch_api = orig_batch
    client._initialized = orig_init
    client._init_error = orig_error


class TestLoadConfig:
    """Tests for _load_config function."""

    def test_load_incluster_config_success(self):
        """Test loading in-cluster config successfully."""
        with patch("src.services.kubernetes.client.config.load_incluster_config") as mock_load:
            result = client._load_config()

        assert result is True
        mock_load.assert_called_once()

    def test_load_incluster_config_fails_tries_kubeconfig(self):
        """Test fallback to kubeconfig when in-cluster fails."""
        from kubernetes.config import ConfigException

        with patch("src.services.kubernetes.client.config.load_incluster_config") as mock_incluster:
            mock_incluster.side_effect = ConfigException("Not in cluster")
            with patch("src.services.kubernetes.client.config.load_kube_config") as mock_kubeconfig:
                result = client._load_config()

        assert result is True
        mock_kubeconfig.assert_called_once()

    def test_load_config_both_fail(self):
        """Test when both config methods fail."""
        from kubernetes.config import ConfigException

        with patch("src.services.kubernetes.client.config.load_incluster_config") as mock_incluster:
            mock_incluster.side_effect = ConfigException("Not in cluster")
            with patch("src.services.kubernetes.client.config.load_kube_config") as mock_kubeconfig:
                mock_kubeconfig.side_effect = Exception("No kubeconfig")
                result = client._load_config()

        assert result is False
        assert client._init_error is not None
        assert "Failed to load Kubernetes config" in client._init_error


class TestInitializeClient:
    """Tests for initialize_client function."""

    def test_initialize_success(self):
        """Test successful client initialization."""
        with patch("src.services.kubernetes.client._load_config", return_value=True):
            with patch("src.services.kubernetes.client.CoreV1Api") as mock_core:
                mock_core_instance = MagicMock()
                mock_core.return_value = mock_core_instance
                with patch("src.services.kubernetes.client.BatchV1Api") as mock_batch:
                    result = client.initialize_client()

        assert result is True
        assert client._initialized is True
        mock_core_instance.get_api_resources.assert_called_once()

    def test_initialize_returns_cached_result(self):
        """Test that initialization is cached."""
        client._initialized = True
        client._core_api = MagicMock()

        with patch("src.services.kubernetes.client._load_config") as mock_load:
            result = client.initialize_client()

        assert result is True
        mock_load.assert_not_called()

    def test_initialize_returns_false_when_core_none(self):
        """Test that initialization returns false when core_api is None."""
        client._initialized = True
        client._core_api = None

        result = client.initialize_client()

        assert result is False

    def test_initialize_config_fails(self):
        """Test initialization when config loading fails."""
        with patch("src.services.kubernetes.client._load_config", return_value=False):
            result = client.initialize_client()

        assert result is False
        assert client._initialized is True

    def test_initialize_api_exception(self):
        """Test initialization when API call fails."""
        with patch("src.services.kubernetes.client._load_config", return_value=True):
            with patch("src.services.kubernetes.client.CoreV1Api") as mock_core:
                mock_core_instance = MagicMock()
                mock_core_instance.get_api_resources.side_effect = ApiException(status=401, reason="Unauthorized")
                mock_core.return_value = mock_core_instance
                with patch("src.services.kubernetes.client.BatchV1Api"):
                    result = client.initialize_client()

        assert result is False
        assert client._initialized is True
        assert "Kubernetes API error" in client._init_error

    def test_initialize_generic_exception(self):
        """Test initialization when generic exception occurs."""
        with patch("src.services.kubernetes.client._load_config", return_value=True):
            with patch("src.services.kubernetes.client.CoreV1Api") as mock_core:
                mock_core.side_effect = Exception("Connection failed")
                result = client.initialize_client()

        assert result is False
        assert client._initialized is True
        assert "Failed to initialize" in client._init_error


class TestGetKubernetesClient:
    """Tests for get_kubernetes_client function."""

    def test_get_client_initializes_if_not_done(self):
        """Test that get_kubernetes_client initializes if not done."""
        mock_core = MagicMock()
        mock_batch = MagicMock()

        def mock_init_side_effect():
            # Simulate what initialize_client does
            client._initialized = True
            client._core_api = mock_core
            client._batch_api = mock_batch
            return True

        with patch("src.services.kubernetes.client.initialize_client", side_effect=mock_init_side_effect) as mock_init:
            core, batch = client.get_kubernetes_client()

        mock_init.assert_called_once()
        assert core is mock_core
        assert batch is mock_batch

    def test_get_client_returns_none_when_not_available(self):
        """Test that get_kubernetes_client returns None when not available."""
        client._initialized = True
        client._core_api = None
        client._batch_api = None

        core, batch = client.get_kubernetes_client()

        assert core is None
        assert batch is None


class TestGetCoreApi:
    """Tests for get_core_api function."""

    def test_get_core_api_returns_core(self):
        """Test that get_core_api returns the core API."""
        mock_core = MagicMock()
        client._initialized = True
        client._core_api = mock_core

        result = client.get_core_api()

        assert result is mock_core


class TestGetBatchApi:
    """Tests for get_batch_api function."""

    def test_get_batch_api_returns_batch(self):
        """Test that get_batch_api returns the batch API."""
        mock_batch = MagicMock()
        client._initialized = True
        client._batch_api = mock_batch

        result = client.get_batch_api()

        assert result is mock_batch


class TestIsAvailable:
    """Tests for is_available function."""

    def test_is_available_true(self):
        """Test is_available returns True when client is ready."""
        client._initialized = True
        client._core_api = MagicMock()

        result = client.is_available()

        assert result is True

    def test_is_available_false(self):
        """Test is_available returns False when client is not ready."""
        client._initialized = True
        client._core_api = None

        result = client.is_available()

        assert result is False

    def test_is_available_initializes(self):
        """Test is_available initializes if needed."""
        with patch("src.services.kubernetes.client.initialize_client") as mock_init:
            client._core_api = None
            result = client.is_available()

        mock_init.assert_called_once()
        assert result is False


class TestGetInitializationError:
    """Tests for get_initialization_error function."""

    def test_get_error_returns_message(self):
        """Test get_initialization_error returns the error message."""
        client._init_error = "Test error message"

        result = client.get_initialization_error()

        assert result == "Test error message"

    def test_get_error_returns_none(self):
        """Test get_initialization_error returns None when no error."""
        result = client.get_initialization_error()

        assert result is None


class TestGetCurrentNamespace:
    """Tests for get_current_namespace function."""

    def test_namespace_from_env(self):
        """Test getting namespace from NAMESPACE env var."""
        with patch.dict(os.environ, {"NAMESPACE": "test-namespace"}):
            result = client.get_current_namespace()

        assert result == "test-namespace"

    def test_namespace_from_pod_namespace_env(self):
        """Test getting namespace from POD_NAMESPACE env var."""
        with patch.dict(os.environ, {"POD_NAMESPACE": "pod-namespace"}, clear=True):
            result = client.get_current_namespace()

        assert result == "pod-namespace"

    def test_namespace_from_service_account(self):
        """Test getting namespace from service account file."""
        mock_data = "sa-namespace"
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = mock_data
                result = client.get_current_namespace()

        assert result == "sa-namespace"

    def test_namespace_default_fallback(self):
        """Test getting default namespace when all else fails."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.open", side_effect=FileNotFoundError):
                result = client.get_current_namespace()

        assert result == "default"


class TestKubernetesClientContext:
    """Tests for KubernetesClientContext class."""

    def test_context_enters_successfully(self):
        """Test context manager enters when client is available."""
        mock_core = MagicMock()
        mock_batch = MagicMock()
        client._initialized = True
        client._core_api = mock_core
        client._batch_api = mock_batch

        with client.KubernetesClientContext() as ctx:
            assert ctx.core_api is mock_core
            assert ctx.batch_api is mock_batch

    def test_context_raises_when_not_available(self):
        """Test context manager raises when client is not available."""
        client._initialized = True
        client._core_api = None
        client._init_error = "Test error"

        with pytest.raises(RuntimeError) as exc_info:
            with client.KubernetesClientContext():
                pass

        assert "not available" in str(exc_info.value)
        assert "Test error" in str(exc_info.value)


class TestCreatePodManifest:
    """Tests for create_pod_manifest function."""

    def test_create_pod_manifest_basic(self):
        """Test creating a basic pod manifest."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        assert pod.metadata.name == "test-pod"
        assert pod.metadata.namespace == "test-ns"
        assert pod.metadata.labels["app"] == "test"
        assert len(pod.spec.containers) == 2

    def test_create_pod_manifest_with_annotations(self):
        """Test creating pod manifest with annotations."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            annotations={"custom": "annotation"},
        )

        assert pod.metadata.annotations["custom"] == "annotation"

    def test_create_pod_manifest_containers(self):
        """Test pod manifest has main and sidecar containers."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        container_names = [c.name for c in pod.spec.containers]
        assert "main" in container_names
        assert "sidecar" in container_names

    def test_create_pod_manifest_resource_limits(self):
        """Test pod manifest has correct resource limits."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            cpu_limit="2",
            memory_limit="1Gi",
        )

        main_container = next(c for c in pod.spec.containers if c.name == "main")
        assert main_container.resources.limits["cpu"] == "2"
        assert main_container.resources.limits["memory"] == "1Gi"

    def test_create_pod_manifest_shared_volume(self):
        """Test pod manifest has shared volume."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        volume_names = [v.name for v in pod.spec.volumes]
        assert "shared-data" in volume_names

        # Check both containers mount it
        for container in pod.spec.containers:
            mount_names = [m.name for m in container.volume_mounts]
            assert "shared-data" in mount_names

    def test_create_pod_manifest_security_context(self):
        """Test pod manifest has security context."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            run_as_user=1001,
        )

        main_container = next(c for c in pod.spec.containers if c.name == "main")
        assert main_container.security_context.run_as_user == 1001
        assert main_container.security_context.run_as_non_root is True

    def test_create_pod_manifest_agent_mode_default(self):
        """Test that agent mode is the default execution mode."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        # Agent mode: no shareProcessNamespace
        assert pod.spec.share_process_namespace is False

        # Agent mode: init container copies executor agent to shared volume
        assert pod.spec.init_containers is not None
        assert len(pod.spec.init_containers) == 1
        init_container = pod.spec.init_containers[0]
        assert init_container.name == "agent-init"
        assert init_container.command[0] == "python"
        assert "/opt/executor-agent" in init_container.command[2]
        assert "/mnt/data/.executor-agent" in init_container.command[2]

        # Agent mode: main container runs executor agent
        main_container = next(c for c in pod.spec.containers if c.name == "main")
        assert main_container.args == ["/mnt/data/.executor-agent"]

        # Agent mode: sidecar has EXECUTION_MODE and EXECUTOR_PORT env vars
        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        env_dict = {e.name: e.value for e in sidecar.env}
        assert env_dict["EXECUTION_MODE"] == "agent"
        assert env_dict["EXECUTOR_PORT"] == "9090"

        # Agent mode: no capabilities, no privilege escalation for sidecar
        assert sidecar.security_context.allow_privilege_escalation is False
        assert sidecar.security_context.capabilities.drop == ["ALL"]
        assert sidecar.security_context.capabilities.add is None

        # Agent mode: no capabilities, no privilege escalation for main
        assert main_container.security_context.allow_privilege_escalation is False
        assert main_container.security_context.capabilities.drop == ["ALL"]

        # Agent mode: init container also has minimal security
        assert init_container.security_context.allow_privilege_escalation is False
        assert init_container.security_context.capabilities.drop == ["ALL"]

    def test_create_pod_manifest_nsenter_mode(self):
        """Test nsenter mode has the required capabilities and settings."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            execution_mode="nsenter",
        )

        # nsenter mode: shareProcessNamespace required
        assert pod.spec.share_process_namespace is True

        # nsenter mode: no init containers
        assert pod.spec.init_containers is None

        # nsenter mode: main container uses default CMD (no args override)
        main_container = next(c for c in pod.spec.containers if c.name == "main")
        assert main_container.args is None

        # nsenter mode: sidecar has elevated privileges
        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        assert sidecar.security_context.allow_privilege_escalation is True
        assert set(sidecar.security_context.capabilities.add) == {"SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"}

        # nsenter mode: EXECUTION_MODE is set to nsenter
        env_dict = {e.name: e.value for e in sidecar.env}
        assert env_dict["EXECUTION_MODE"] == "nsenter"
        # nsenter mode: EXECUTOR_PORT is still present (used by both modes)
        assert env_dict["EXECUTOR_PORT"] == "9090"

    def test_create_pod_manifest_agent_mode_executor_port(self):
        """Test that agent mode uses the configured executor port."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            execution_mode="agent",
            executor_port=8888,
        )

        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        env_dict = {e.name: e.value for e in sidecar.env}
        assert env_dict["EXECUTOR_PORT"] == "8888"

    def test_create_pod_manifest_seccomp_profile_default(self):
        """Test pod manifest has RuntimeDefault seccomp profile by default."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        assert pod.spec.security_context.seccomp_profile is not None
        assert pod.spec.security_context.seccomp_profile.type == "RuntimeDefault"

    def test_create_pod_manifest_seccomp_profile_unconfined(self):
        """Test pod manifest accepts Unconfined seccomp profile."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            seccomp_profile_type="Unconfined",
        )

        assert pod.spec.security_context.seccomp_profile.type == "Unconfined"

    def test_create_pod_manifest_seccomp_profile_propagates(self):
        """Test seccomp profile type is propagated to pod security context."""
        for profile_type in ["RuntimeDefault", "Unconfined"]:
            pod = client.create_pod_manifest(
                name="test-pod",
                namespace="test-ns",
                main_image="python:3.12",
                sidecar_image="sidecar:latest",
                language="python",
                labels={"app": "test"},
                seccomp_profile_type=profile_type,
            )

            assert pod.spec.security_context.seccomp_profile.type == profile_type

    def test_create_pod_manifest_network_isolated_false(self):
        """Test pod manifest with network_isolated=False."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            network_isolated=False,
        )

        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        env_dict = {e.name: e.value for e in sidecar.env}
        assert "NETWORK_ISOLATED" in env_dict
        assert env_dict["NETWORK_ISOLATED"] == "false"

    def test_create_pod_manifest_network_isolated_true(self):
        """Test pod manifest with network_isolated=True."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="go:1.22",
            sidecar_image="sidecar:latest",
            language="go",
            labels={"app": "test"},
            network_isolated=True,
        )

        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        env_dict = {e.name: e.value for e in sidecar.env}
        assert "NETWORK_ISOLATED" in env_dict
        assert env_dict["NETWORK_ISOLATED"] == "true"

    def test_create_pod_manifest_network_isolated_default(self):
        """Test pod manifest defaults network_isolated to False."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        env_dict = {e.name: e.value for e in sidecar.env}
        assert "NETWORK_ISOLATED" in env_dict
        assert env_dict["NETWORK_ISOLATED"] == "false"

    def test_create_pod_manifest_gke_sandbox_enabled(self):
        """Test GKE Sandbox adds runtime class, node selector, tolerations, and annotation."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=True,
        )

        # Runtime class
        assert pod.spec.runtime_class_name == "gvisor"

        # Node selector
        assert pod.spec.node_selector is not None
        assert pod.spec.node_selector["sandbox.gke.io/runtime"] == "gvisor"

        # Tolerations
        assert pod.spec.tolerations is not None
        assert len(pod.spec.tolerations) == 1
        tol = pod.spec.tolerations[0]
        assert tol.key == "sandbox.gke.io/runtime"
        assert tol.operator == "Equal"
        assert tol.value == "gvisor"
        assert tol.effect == "NoSchedule"

        # Annotation
        assert pod.metadata.annotations["sandbox.gke.io/runtime"] == "gvisor"

    def test_create_pod_manifest_gke_sandbox_disabled(self):
        """Test GKE Sandbox disabled has no runtime class, node selector, or tolerations."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=False,
        )

        assert pod.spec.runtime_class_name is None
        assert pod.spec.node_selector is None
        assert pod.spec.tolerations is None

    def test_create_pod_manifest_gke_sandbox_custom_runtime_class(self):
        """Test GKE Sandbox with custom runtime class name."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=True,
            runtime_class_name="custom-runtime",
        )

        assert pod.spec.runtime_class_name == "custom-runtime"

    def test_create_pod_manifest_gke_sandbox_custom_node_selector(self):
        """Test GKE Sandbox with additional custom node selector."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=True,
            sandbox_node_selector={"pool": "sandbox"},
        )

        assert pod.spec.node_selector["sandbox.gke.io/runtime"] == "gvisor"
        assert pod.spec.node_selector["pool"] == "sandbox"

    def test_create_pod_manifest_gke_sandbox_custom_tolerations(self):
        """Test GKE Sandbox with additional custom tolerations."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=True,
            custom_tolerations=[{"key": "pool", "value": "sandbox"}],
        )

        # Should have both the GKE default + custom toleration
        assert len(pod.spec.tolerations) == 2
        keys = [t.key for t in pod.spec.tolerations]
        assert "sandbox.gke.io/runtime" in keys
        assert "pool" in keys

    def test_create_pod_manifest_image_pull_secrets(self):
        """Test pod manifest with image pull secrets."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            image_pull_secrets=["my-registry-secret", "other-secret"],
        )

        assert pod.spec.image_pull_secrets is not None
        assert len(pod.spec.image_pull_secrets) == 2
        secret_names = [s.name for s in pod.spec.image_pull_secrets]
        assert "my-registry-secret" in secret_names
        assert "other-secret" in secret_names

    def test_create_pod_manifest_no_image_pull_secrets(self):
        """Test pod manifest without image pull secrets."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
        )

        assert pod.spec.image_pull_secrets is None

    def test_create_pod_manifest_gke_sandbox_with_annotations(self):
        """Test GKE Sandbox merges with existing annotations."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            annotations={"custom": "value"},
            gke_sandbox_enabled=True,
        )

        assert pod.metadata.annotations["custom"] == "value"
        assert pod.metadata.annotations["sandbox.gke.io/runtime"] == "gvisor"

    def test_create_pod_manifest_gke_sandbox_requires_agent_mode(self):
        """Test that GKE Sandbox works with agent mode (default)."""
        pod = client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            gke_sandbox_enabled=True,
            execution_mode="agent",
        )

        # Should have both GKE Sandbox and agent mode features
        assert pod.spec.runtime_class_name == "gvisor"
        assert pod.spec.share_process_namespace is False
        assert pod.spec.init_containers is not None

        # Sidecar should have minimal privileges (agent mode)
        sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
        assert sidecar.security_context.allow_privilege_escalation is False

    def test_create_pod_manifest_gke_sandbox_warns_on_nsenter_mode(self):
        """Test that GKE Sandbox with nsenter mode logs a warning."""
        with patch("src.services.kubernetes.client.logger") as mock_logger:
            pod = client.create_pod_manifest(
                name="test-pod",
                namespace="test-ns",
                main_image="python:3.12",
                sidecar_image="sidecar:latest",
                language="python",
                labels={"app": "test"},
                gke_sandbox_enabled=True,
                execution_mode="nsenter",
            )

            # Should still create the pod (warning, not error)
            assert pod.spec.runtime_class_name == "gvisor"

            # nsenter mode features should be present
            assert pod.spec.share_process_namespace is True
            assert pod.spec.init_containers is None

            # Sidecar should have elevated privileges (nsenter mode)
            sidecar = next(c for c in pod.spec.containers if c.name == "sidecar")
            assert sidecar.security_context.allow_privilege_escalation is True

            # Should have logged a warning about incompatibility
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "gVisor" in warning_msg or "GKE Sandbox" in warning_msg

    def test_create_pod_manifest_annotations_not_mutated(self):
        """Test that the caller's annotations dict is not mutated by GKE Sandbox."""
        original_annotations = {"custom": "value"}
        annotations_copy = dict(original_annotations)

        client.create_pod_manifest(
            name="test-pod",
            namespace="test-ns",
            main_image="python:3.12",
            sidecar_image="sidecar:latest",
            language="python",
            labels={"app": "test"},
            annotations=original_annotations,
            gke_sandbox_enabled=True,
        )

        # Original dict must be unchanged
        assert original_annotations == annotations_copy

    def test_create_pod_manifest_custom_tolerations_missing_key_skipped(self):
        """Test that custom tolerations without a 'key' field are skipped with a warning."""
        with patch("src.services.kubernetes.client.logger") as mock_logger:
            pod = client.create_pod_manifest(
                name="test-pod",
                namespace="test-ns",
                main_image="python:3.12",
                sidecar_image="sidecar:latest",
                language="python",
                labels={"app": "test"},
                gke_sandbox_enabled=True,
                custom_tolerations=[
                    {"key": "pool", "value": "sandbox"},
                    {"operator": "Exists", "effect": "NoSchedule"},  # missing key
                    {"key": "other", "value": "val"},
                ],
            )

            # The GKE default + 2 valid custom (the one missing 'key' is skipped)
            assert len(pod.spec.tolerations) == 3
            keys = [t.key for t in pod.spec.tolerations]
            assert "pool" in keys
            assert "other" in keys
            assert "sandbox.gke.io/runtime" in keys

            # Should warn about the skipped toleration
            mock_logger.warning.assert_called_once()
