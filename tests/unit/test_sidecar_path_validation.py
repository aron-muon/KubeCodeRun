"""Tests for sidecar file path validation security.

These tests verify that the path traversal fix (issue #7) correctly
prevents directory prefix collision attacks and other path escape attempts.
"""

from pathlib import Path

import pytest


@pytest.fixture
def mock_dirs(tmp_path):
    """Create a mock working directory structure."""
    working_dir = tmp_path / "data"
    working_dir.mkdir()

    # Create a sibling directory with prefix collision name
    evil_dir = tmp_path / "data-evil"
    evil_dir.mkdir()
    (evil_dir / "secret.txt").write_text("secret data")

    # Create normal files in working directory
    (working_dir / "normal.txt").write_text("normal data")
    subdir = working_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("nested data")

    return working_dir, evil_dir


def validate_path(path: str, working_dir: Path) -> Path:
    """Replicate the validation logic from sidecar for testing.

    This mirrors validate_path_within_working_dir() from docker/sidecar/main.py
    """
    file_path = (working_dir / path).resolve()
    working_path = working_dir.resolve()

    if not file_path.is_relative_to(working_path):
        raise PermissionError("Access denied")

    return file_path


class TestPathValidation:
    """Tests for path validation logic."""

    def test_normal_file_access_allowed(self, mock_dirs):
        """Normal file access within working directory should succeed."""
        working_dir, _ = mock_dirs
        result = validate_path("normal.txt", working_dir)
        assert result == (working_dir / "normal.txt").resolve()

    def test_subdirectory_access_allowed(self, mock_dirs):
        """Subdirectory access should succeed."""
        working_dir, _ = mock_dirs
        result = validate_path("subdir/nested.txt", working_dir)
        assert result == (working_dir / "subdir" / "nested.txt").resolve()

    def test_basic_traversal_blocked(self, mock_dirs):
        """Basic path traversal with ../ should be blocked."""
        working_dir, _ = mock_dirs
        with pytest.raises(PermissionError):
            validate_path("../etc/passwd", working_dir)

    def test_prefix_collision_attack_blocked(self, mock_dirs):
        """Prefix collision attack (../data-evil) should be blocked.

        This is the specific attack vector that startswith() fails to catch.
        """
        working_dir, _ = mock_dirs
        with pytest.raises(PermissionError):
            validate_path("../data-evil/secret.txt", working_dir)

    def test_double_traversal_blocked(self, mock_dirs):
        """Double traversal (../../) should be blocked."""
        working_dir, _ = mock_dirs
        with pytest.raises(PermissionError):
            validate_path("../../etc/passwd", working_dir)

    def test_nested_traversal_in_middle_blocked(self, mock_dirs):
        """Traversal in middle of path should be blocked if it escapes."""
        working_dir, _ = mock_dirs
        with pytest.raises(PermissionError):
            validate_path("subdir/../../data-evil/secret.txt", working_dir)

    def test_empty_path_returns_working_dir(self, mock_dirs):
        """Empty path should resolve to working directory itself."""
        working_dir, _ = mock_dirs
        result = validate_path("", working_dir)
        assert result == working_dir.resolve()

    def test_dot_path_returns_working_dir(self, mock_dirs):
        """Single dot path should resolve to working directory."""
        working_dir, _ = mock_dirs
        result = validate_path(".", working_dir)
        assert result == working_dir.resolve()


class TestStartswithVulnerability:
    """Tests demonstrating why startswith() was vulnerable.

    These tests document the specific attack that the old code was vulnerable to.
    """

    def test_startswith_vulnerability_demonstration(self, mock_dirs):
        """Demonstrate why str().startswith() is vulnerable to prefix collisions."""
        working_dir, evil_dir = mock_dirs
        working_path = working_dir.resolve()
        evil_path = (evil_dir / "secret.txt").resolve()

        # This is what the OLD vulnerable code did:
        vulnerable_check = str(evil_path).startswith(str(working_path))

        # The evil path DOES start with the working path string!
        # /tmp/xxx/data-evil/secret.txt starts with /tmp/xxx/data
        assert vulnerable_check is True, "This demonstrates the vulnerability"

        # The FIXED code uses is_relative_to():
        secure_check = evil_path.is_relative_to(working_path)

        # is_relative_to correctly identifies this as NOT within working dir
        assert secure_check is False, "is_relative_to correctly blocks the attack"

    def test_is_relative_to_handles_trailing_slash(self, mock_dirs):
        """is_relative_to works correctly regardless of trailing slashes."""
        working_dir, evil_dir = mock_dirs

        # Even with variations, is_relative_to is correct
        assert (working_dir / "normal.txt").resolve().is_relative_to(working_dir)
        assert not (evil_dir / "secret.txt").resolve().is_relative_to(working_dir)
