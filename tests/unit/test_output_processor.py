"""Unit tests for the OutputProcessor."""

import pytest

from src.services.execution.output import OutputProcessor


class TestSanitizeFilename:
    """Tests for the sanitize_filename method."""

    def test_spaces_replaced_with_underscores(self):
        """Test that spaces are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("file with spaces.txt")
        assert result == "file_with_spaces.txt"

    def test_parentheses_replaced_with_underscores(self):
        """Test that parentheses are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("manufacturing_analysis (v2).xlsx")
        assert result == "manufacturing_analysis__v2_.xlsx"

    def test_special_characters_replaced(self):
        """Test that special characters are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("special@chars#here!.pdf")
        assert result == "special_chars_here_.pdf"

    def test_already_valid_unchanged(self):
        """Test that already valid filenames are unchanged."""
        result = OutputProcessor.sanitize_filename("already-valid.txt")
        assert result == "already-valid.txt"

    def test_uppercase_preserved(self):
        """Test that uppercase letters are preserved."""
        result = OutputProcessor.sanitize_filename("UPPERCASE.TXT")
        assert result == "UPPERCASE.TXT"

    def test_numbers_preserved(self):
        """Test that numbers are preserved."""
        result = OutputProcessor.sanitize_filename("123numbers.doc")
        assert result == "123numbers.doc"

    def test_hidden_file_prefixed(self):
        """Test that hidden files (starting with dot) get underscore prefix."""
        result = OutputProcessor.sanitize_filename(".hidden")
        assert result == "_.hidden"

    def test_empty_string_returns_underscore(self):
        """Test that empty string returns underscore."""
        result = OutputProcessor.sanitize_filename("")
        assert result == "_"

    def test_none_returns_underscore(self):
        """Test that None returns underscore."""
        result = OutputProcessor.sanitize_filename(None)
        assert result == "_"

    def test_path_traversal_stripped(self):
        """Test that path traversal attempts are stripped."""
        result = OutputProcessor.sanitize_filename("../../../etc/passwd")
        assert result == "passwd"

    def test_absolute_path_stripped(self):
        """Test that absolute paths are stripped to basename."""
        result = OutputProcessor.sanitize_filename("/absolute/path/file.txt")
        assert result == "file.txt"

    def test_unicode_characters_replaced(self):
        """Test that non-ASCII characters are replaced."""
        result = OutputProcessor.sanitize_filename("résumé.docx")
        assert result == "r_sum_.docx"

    def test_brackets_replaced(self):
        """Test that brackets are replaced with underscores."""
        result = OutputProcessor.sanitize_filename("[brackets].txt")
        assert result == "_brackets_.txt"

    def test_leading_parenthesis_prefixed(self):
        """Test that filename starting with parenthesis is handled."""
        result = OutputProcessor.sanitize_filename("(parentheses).txt")
        assert result == "_parentheses_.txt"

    def test_dashes_preserved(self):
        """Test that dashes are preserved."""
        result = OutputProcessor.sanitize_filename("file-name.with-dashes.txt")
        assert result == "file-name.with-dashes.txt"

    def test_dots_preserved(self):
        """Test that dots in filename are preserved."""
        result = OutputProcessor.sanitize_filename("file.name.multiple.dots.txt")
        assert result == "file.name.multiple.dots.txt"

    def test_simple_filename_unchanged(self):
        """Test that simple alphanumeric filename is unchanged."""
        result = OutputProcessor.sanitize_filename("SimpleFile123.pdf")
        assert result == "SimpleFile123.pdf"

    def test_long_filename_truncated(self):
        """Test that filenames over 255 chars are truncated with hash suffix."""
        long_name = "a" * 300 + ".txt"
        result = OutputProcessor.sanitize_filename(long_name)
        # Should be 255 chars or less
        assert len(result) <= 255
        # Should end with .txt
        assert result.endswith(".txt")
        # Should have a random suffix before extension
        assert "-" in result


class TestSanitizeFilepath:
    """Tests for the path-preserving sanitize_filepath method (Gap G)."""

    def test_preserves_safe_nested_path(self):
        """Nested skill paths keep their directory structure."""
        result = OutputProcessor.sanitize_filepath("skillName/SKILL.md")
        assert result == "skillName/SKILL.md"

    def test_preserves_deeply_nested_path(self):
        """Multi-level nesting is preserved."""
        result = OutputProcessor.sanitize_filepath("skillName/sub/dir/file.py")
        assert result == "skillName/sub/dir/file.py"

    def test_sanitizes_each_segment(self):
        """Each path segment is individually sanitized while '/' is kept."""
        result = OutputProcessor.sanitize_filepath("my skill/data (v2).csv")
        assert result == "my_skill/data__v2_.csv"

    def test_backslashes_normalized(self):
        """Windows-style separators normalize to forward slashes."""
        result = OutputProcessor.sanitize_filepath("skillName\\SKILL.md")
        assert result == "skillName/SKILL.md"

    def test_absolute_path_falls_back_to_basename(self):
        """Absolute paths are unsafe and collapse to a sanitized basename."""
        result = OutputProcessor.sanitize_filepath("/etc/passwd")
        assert result == "passwd"

    def test_parent_traversal_falls_back_to_basename(self):
        """'..' segments are unsafe and collapse to a sanitized basename."""
        result = OutputProcessor.sanitize_filepath("../../etc/passwd")
        assert result == "passwd"

    def test_embedded_traversal_falls_back_to_basename(self):
        """A traversal segment anywhere makes the whole path unsafe."""
        result = OutputProcessor.sanitize_filepath("skillName/../../../etc/passwd")
        assert result == "passwd"

    def test_dot_segment_falls_back_to_basename(self):
        """A '.' segment is rejected, collapsing to the basename."""
        result = OutputProcessor.sanitize_filepath("skillName/./file.txt")
        assert result == "file.txt"

    def test_empty_segment_falls_back_to_basename(self):
        """Double slashes create an empty segment and are rejected."""
        result = OutputProcessor.sanitize_filepath("skillName//file.txt")
        assert result == "file.txt"

    def test_flat_filename_unchanged(self):
        """A non-nested name behaves like sanitize_filename."""
        result = OutputProcessor.sanitize_filepath("data.csv")
        assert result == "data.csv"

    def test_empty_returns_underscore(self):
        """Empty input returns a safe placeholder."""
        assert OutputProcessor.sanitize_filepath("") == "_"

    def test_no_segment_can_become_traversal(self):
        """Sanitized output must never contain a usable '..' traversal segment."""
        result = OutputProcessor.sanitize_filepath("ok/normal/file.txt")
        assert ".." not in result.split("/")
        assert not result.startswith("/")


class TestNormalizeFilename:
    """Tests for the deprecated normalize_filename method."""

    def test_delegates_to_sanitize_filename(self):
        """Test that normalize_filename delegates to sanitize_filename."""
        result = OutputProcessor.normalize_filename("file with spaces.txt")
        expected = OutputProcessor.sanitize_filename("file with spaces.txt")
        assert result == expected

    def test_parentheses_now_replaced(self):
        """Test that normalize_filename now also replaces parentheses."""
        result = OutputProcessor.normalize_filename("file (v2).xlsx")
        assert result == "file__v2_.xlsx"


class TestSanitizeOutput:
    """Tests for sanitize_output method."""

    def test_sanitize_normal_output(self):
        """Test sanitizing normal output."""
        result = OutputProcessor.sanitize_output("Hello, World!")
        assert result == "Hello, World!"

    def test_sanitize_with_newlines(self):
        """Test that newlines are preserved."""
        result = OutputProcessor.sanitize_output("line1\nline2\nline3")
        assert result == "line1\nline2\nline3"

    def test_sanitize_with_tabs(self):
        """Test that tabs are preserved."""
        result = OutputProcessor.sanitize_output("col1\tcol2\tcol3")
        assert result == "col1\tcol2\tcol3"

    def test_sanitize_removes_null_byte(self):
        """Test removal of null byte."""
        result = OutputProcessor.sanitize_output("hello\x00world")
        assert result == "helloworld"

    def test_sanitize_removes_bell(self):
        """Test removal of bell character."""
        result = OutputProcessor.sanitize_output("hello\x07world")
        assert result == "helloworld"

    def test_sanitize_truncates_large_output(self):
        """Test that large output is truncated."""
        large_output = "x" * 100000
        result = OutputProcessor.sanitize_output(large_output, max_size=1000)
        assert len(result) < 2000
        assert "truncated" in result.lower()

    def test_sanitize_strips_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        result = OutputProcessor.sanitize_output("  hello  ")
        assert result == "hello"

    def test_sanitize_empty_string(self):
        """Test sanitizing empty string."""
        result = OutputProcessor.sanitize_output("")
        assert result == ""


class TestValidateGeneratedFile:
    """Tests for validate_generated_file method."""

    def test_validate_valid_file(self):
        """Test validation of a valid file."""
        file_info = {
            "path": "/mnt/data/output.txt",
            "size": 1024,
            "mime_type": "text/plain",
        }
        assert OutputProcessor.validate_generated_file(file_info) is True

    def test_validate_file_too_large(self):
        """Test rejection of oversized file."""
        file_info = {
            "path": "/mnt/data/large.bin",
            "size": 100 * 1024 * 1024 * 1024,  # 100GB
            "mime_type": "application/octet-stream",
        }
        assert OutputProcessor.validate_generated_file(file_info) is False

    def test_validate_path_traversal(self):
        """Test rejection of path traversal."""
        file_info = {
            "path": "../../../etc/passwd",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is False

    def test_validate_dangerous_exe_extension(self):
        """Test rejection of .exe extension."""
        file_info = {
            "path": "/mnt/data/malicious.exe",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is False

    def test_validate_dangerous_bat_extension(self):
        """Test rejection of .bat extension."""
        file_info = {
            "path": "/mnt/data/malicious.bat",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is False

    def test_validate_dangerous_sh_extension(self):
        """Test rejection of .sh extension."""
        file_info = {
            "path": "/mnt/data/malicious.sh",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is False

    def test_validate_container_path(self):
        """Test validation of container workspace path."""
        file_info = {
            "path": "/mnt/data/subdir/output.csv",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is True

    def test_validate_relative_path(self):
        """Test validation of relative path."""
        file_info = {
            "path": "output.csv",
            "size": 100,
        }
        assert OutputProcessor.validate_generated_file(file_info) is True


class TestGuessMimeType:
    """Tests for guess_mime_type method."""

    def test_guess_text_file(self):
        """Test guessing MIME type for text file."""
        assert OutputProcessor.guess_mime_type("file.txt") == "text/plain"

    def test_guess_csv_file(self):
        """Test guessing MIME type for CSV file."""
        assert OutputProcessor.guess_mime_type("data.csv") == "text/csv"

    def test_guess_json_file(self):
        """Test guessing MIME type for JSON file."""
        assert OutputProcessor.guess_mime_type("config.json") == "application/json"

    def test_guess_xml_file(self):
        """Test guessing MIME type for XML file."""
        assert OutputProcessor.guess_mime_type("data.xml") == "application/xml"

    def test_guess_html_file(self):
        """Test guessing MIME type for HTML file."""
        assert OutputProcessor.guess_mime_type("page.html") == "text/html"

    def test_guess_png_file(self):
        """Test guessing MIME type for PNG file."""
        assert OutputProcessor.guess_mime_type("image.png") == "image/png"

    def test_guess_jpg_file(self):
        """Test guessing MIME type for JPG file."""
        assert OutputProcessor.guess_mime_type("photo.jpg") == "image/jpeg"

    def test_guess_jpeg_file(self):
        """Test guessing MIME type for JPEG file."""
        assert OutputProcessor.guess_mime_type("photo.jpeg") == "image/jpeg"

    def test_guess_gif_file(self):
        """Test guessing MIME type for GIF file."""
        assert OutputProcessor.guess_mime_type("animation.gif") == "image/gif"

    def test_guess_pdf_file(self):
        """Test guessing MIME type for PDF file."""
        assert OutputProcessor.guess_mime_type("document.pdf") == "application/pdf"

    def test_guess_zip_file(self):
        """Test guessing MIME type for ZIP file."""
        assert OutputProcessor.guess_mime_type("archive.zip") == "application/zip"

    def test_guess_unknown_extension(self):
        """Test default MIME type for unknown extension."""
        assert OutputProcessor.guess_mime_type("file.xyz") == "application/octet-stream"

    def test_guess_no_extension(self):
        """Test MIME type for file without extension."""
        assert OutputProcessor.guess_mime_type("noextension") == "application/octet-stream"

    def test_guess_uppercase_extension(self):
        """Test that extension matching is case-insensitive."""
        assert OutputProcessor.guess_mime_type("FILE.TXT") == "text/plain"
        assert OutputProcessor.guess_mime_type("IMAGE.PNG") == "image/png"


class TestDetermineExecutionStatus:
    """Tests for determine_execution_status method."""

    from src.models import ExecutionStatus

    def test_status_completed(self):
        """Test completed status for exit code 0."""
        result = OutputProcessor.determine_execution_status(0, "", 100)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.COMPLETED

    def test_status_timeout_exit_code(self):
        """Test timeout status for exit code 124."""
        result = OutputProcessor.determine_execution_status(124, "", 30000)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.TIMEOUT

    def test_status_failed_nonzero(self):
        """Test failed status for non-zero exit code."""
        result = OutputProcessor.determine_execution_status(1, "", 100)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.FAILED

    def test_status_failed_memory_error(self):
        """Test failed status for memory error."""
        result = OutputProcessor.determine_execution_status(1, "Out of memory", 100)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.FAILED

    def test_status_failed_segfault(self):
        """Test failed status for segmentation fault."""
        result = OutputProcessor.determine_execution_status(139, "Segmentation fault", 100)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.FAILED

    def test_status_failed_permission_denied(self):
        """Test failed status for permission denied."""
        result = OutputProcessor.determine_execution_status(1, "Permission denied", 100)
        from src.models import ExecutionStatus

        assert result == ExecutionStatus.FAILED


class TestFormatErrorMessage:
    """Tests for format_error_message method."""

    def test_format_timeout(self):
        """Test formatting timeout error."""
        result = OutputProcessor.format_error_message(124, "")
        assert "timed out" in result.lower()

    def test_format_no_stderr(self):
        """Test formatting error without stderr."""
        result = OutputProcessor.format_error_message(1, "")
        assert "exit code 1" in result

    def test_format_with_stderr(self):
        """Test formatting error with stderr."""
        result = OutputProcessor.format_error_message(1, "NameError: undefined")
        assert "NameError" in result

    def test_format_permission_denied(self):
        """Test formatting permission denied error."""
        result = OutputProcessor.format_error_message(1, "Permission denied: /etc/passwd")
        assert "permission" in result.lower()

    def test_format_network_error(self):
        """Test formatting network error."""
        result = OutputProcessor.format_error_message(1, "Network unreachable")
        assert "network" in result.lower()

    def test_format_connection_refused(self):
        """Test formatting connection refused error."""
        result = OutputProcessor.format_error_message(1, "Connection refused")
        assert "network" in result.lower()

    def test_format_truncates_long_stderr(self):
        """Test that long stderr is truncated."""
        long_stderr = "x" * 1000
        result = OutputProcessor.format_error_message(1, long_stderr)
        assert "truncated" in result.lower()

    def test_format_java_compilation_not_found(self):
        """Test formatting Java compilation error."""
        result = OutputProcessor.format_error_message(1, "javac: not found")
        assert "java" in result.lower()

    def test_format_memory_error(self):
        """Test formatting memory error."""
        result = OutputProcessor.format_error_message(1, "Out of memory allocating buffer")
        assert "memory" in result.lower()
