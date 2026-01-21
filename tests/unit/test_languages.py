"""Unit tests for language configuration module."""

import pytest

from src.config.languages import (
    LANGUAGES,
    LanguageConfig,
    get_execution_command,
    get_file_extension,
    get_image_for_language,
    get_language,
    get_supported_languages,
    get_user_id_for_language,
    is_supported_language,
    uses_stdin,
)


class TestLanguageConfig:
    """Tests for LanguageConfig dataclass."""

    def test_language_config_defaults(self):
        """Test default values for optional fields."""
        config = LanguageConfig(
            code="test",
            name="Test",
            image="test:latest",
            user_id=65532,
            file_extension="test",
            execution_command="test",
        )

        assert config.uses_stdin is False
        assert config.timeout_multiplier == 1.0
        assert config.memory_multiplier == 1.0
        assert config.environment == {}

    def test_language_config_is_frozen(self):
        """Test that LanguageConfig is immutable."""
        config = LanguageConfig(
            code="test",
            name="Test",
            image="test:latest",
            user_id=65532,
            file_extension="test",
            execution_command="test",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            config.code = "modified"


class TestLanguagesRegistry:
    """Tests for LANGUAGES registry."""

    def test_all_languages_present(self):
        """Test all 12 languages are registered."""
        expected_codes = ["py", "js", "ts", "go", "java", "c", "cpp", "php", "rs", "r", "f90", "d"]
        assert len(LANGUAGES) == 12
        for code in expected_codes:
            assert code in LANGUAGES

    def test_python_config(self):
        """Test Python configuration."""
        py = LANGUAGES["py"]
        assert py.name == "Python"
        assert py.uses_stdin is True
        assert py.user_id == 65532

    def test_typescript_has_compilation(self):
        """Test TypeScript has compilation in command."""
        ts = LANGUAGES["ts"]
        assert "tsc" in ts.execution_command
        assert ts.uses_stdin is False
        assert ts.timeout_multiplier == 2.0

    def test_compiled_languages_not_stdin(self):
        """Test compiled languages don't use stdin."""
        compiled = ["go", "java", "c", "cpp", "rs", "f90", "d", "ts"]
        for code in compiled:
            assert LANGUAGES[code].uses_stdin is False


class TestGetLanguage:
    """Tests for get_language function."""

    def test_get_language_exists(self):
        """Test getting an existing language."""
        lang = get_language("py")
        assert lang is not None
        assert lang.name == "Python"

    def test_get_language_uppercase(self):
        """Test case insensitivity."""
        lang = get_language("PY")
        assert lang is not None
        assert lang.code == "py"

    def test_get_language_not_exists(self):
        """Test getting a non-existent language."""
        lang = get_language("unknown")
        assert lang is None


class TestGetSupportedLanguages:
    """Tests for get_supported_languages function."""

    def test_returns_list(self):
        """Test returns list of language codes."""
        languages = get_supported_languages()
        assert isinstance(languages, list)
        assert len(languages) == 12

    def test_contains_common_languages(self):
        """Test common languages are included."""
        languages = get_supported_languages()
        assert "py" in languages
        assert "js" in languages
        assert "go" in languages


class TestIsSupportedLanguage:
    """Tests for is_supported_language function."""

    def test_supported_language(self):
        """Test with supported language."""
        assert is_supported_language("py") is True
        assert is_supported_language("js") is True

    def test_unsupported_language(self):
        """Test with unsupported language."""
        assert is_supported_language("ruby") is False
        assert is_supported_language("unknown") is False

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert is_supported_language("PY") is True
        assert is_supported_language("Js") is True


class TestGetImageForLanguage:
    """Tests for get_image_for_language function."""

    def test_get_image_no_registry(self):
        """Test getting image without custom registry."""
        image = get_image_for_language("py")
        assert image == "python:latest"

    def test_get_image_with_registry(self):
        """Test getting image with custom registry."""
        image = get_image_for_language("py", registry="myregistry")
        assert image == "myregistry-python:latest"

    def test_get_image_with_tag(self):
        """Test getting image with custom tag."""
        image = get_image_for_language("py", tag="v1.0")
        assert image == "python:v1.0"

    def test_get_image_with_registry_and_tag(self):
        """Test getting image with registry and tag."""
        image = get_image_for_language("py", registry="myregistry", tag="v1.0")
        assert image == "myregistry-python:v1.0"

    def test_get_image_unsupported_language(self):
        """Test error for unsupported language."""
        with pytest.raises(ValueError) as exc:
            get_image_for_language("unknown")
        assert "Unsupported language" in str(exc.value)


class TestGetUserIdForLanguage:
    """Tests for get_user_id_for_language function."""

    def test_get_user_id_python(self):
        """Test getting user ID for Python."""
        user_id = get_user_id_for_language("py")
        assert user_id == 65532

    def test_get_user_id_javascript(self):
        """Test getting user ID for JavaScript."""
        user_id = get_user_id_for_language("js")
        assert user_id == 65532

    def test_get_user_id_d(self):
        """Test D language UID."""
        user_id = get_user_id_for_language("d")
        assert user_id == 65532

    def test_get_user_id_unsupported_language(self):
        """Test error for unsupported language."""
        with pytest.raises(ValueError) as exc:
            get_user_id_for_language("unknown")
        assert "Unsupported language" in str(exc.value)


class TestGetExecutionCommand:
    """Tests for get_execution_command function."""

    def test_get_command_python(self):
        """Test getting command for Python."""
        cmd = get_execution_command("py")
        assert cmd == "python3 -"

    def test_get_command_typescript(self):
        """Test getting command for TypeScript."""
        cmd = get_execution_command("ts")
        assert "tsc" in cmd
        assert "node" in cmd

    def test_get_command_go(self):
        """Test getting command for Go."""
        cmd = get_execution_command("go")
        assert "go build" in cmd

    def test_get_command_unsupported_language(self):
        """Test error for unsupported language."""
        with pytest.raises(ValueError) as exc:
            get_execution_command("unknown")
        assert "Unsupported language" in str(exc.value)


class TestUsesStdin:
    """Tests for uses_stdin function."""

    def test_stdin_languages(self):
        """Test languages that use stdin."""
        assert uses_stdin("py") is True
        assert uses_stdin("js") is True
        assert uses_stdin("php") is True
        assert uses_stdin("r") is True

    def test_non_stdin_languages(self):
        """Test languages that don't use stdin."""
        assert uses_stdin("go") is False
        assert uses_stdin("java") is False
        assert uses_stdin("rs") is False

    def test_unknown_language_returns_false(self):
        """Test unknown language returns False."""
        assert uses_stdin("unknown") is False


class TestGetFileExtension:
    """Tests for get_file_extension function."""

    def test_get_extension_python(self):
        """Test getting extension for Python."""
        ext = get_file_extension("py")
        assert ext == "py"

    def test_get_extension_typescript(self):
        """Test getting extension for TypeScript."""
        ext = get_file_extension("ts")
        assert ext == "ts"

    def test_get_extension_fortran(self):
        """Test getting extension for Fortran."""
        ext = get_file_extension("f90")
        assert ext == "f90"

    def test_get_extension_unsupported_language(self):
        """Test error for unsupported language."""
        with pytest.raises(ValueError) as exc:
            get_file_extension("unknown")
        assert "Unsupported language" in str(exc.value)
