"""Unit tests for the Programmatic Tool Calling harness, sentinel, constants."""

import json

import pytest

from src.services.programmatic.constants import (
    PTC_HISTORY_FILENAME,
    build_scoped_sentinel,
    is_reserved_ptc_filename,
)
from src.services.programmatic.harness import (
    build_python_code,
    build_replay_preamble,
    normalize_python_function_name,
    wrap_user_code_in_async,
)
from src.services.programmatic.sentinel import extract_pending_from_stdout

WEATHER_TOOL = {
    "name": "get-weather",
    "description": "Returns dict of current weather",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
            "units": {"type": "string", "description": "metric or imperial"},
        },
        "required": ["city"],
    },
}


class TestHarnessGeneration:
    def test_generated_code_is_valid_python(self):
        code = build_python_code("exec1", [WEATHER_TOOL], "print(await get_weather(city='SF'))")
        compile(code, "main.py", "exec")  # raises SyntaxError if invalid

    def test_tool_stub_async_with_required_then_optional_params(self):
        preamble = build_replay_preamble("exec1", [WEATHER_TOOL])
        # required param first, optional with default after
        assert "async def get_weather(city: str, units: Optional[str] = None)" in preamble
        assert 'return await _execute_tool_internal_async("get-weather", _input)' in preamble

    def test_original_tool_name_preserved_in_dispatch(self):
        # hyphenated name normalizes to identifier but dispatch keeps original
        preamble = build_replay_preamble("exec1", [WEATHER_TOOL])
        assert "async def get_weather(" in preamble
        assert "# Original tool name: get-weather" in preamble

    def test_async_wrap_runs_user_main(self):
        wrapped = wrap_user_code_in_async("x = 1\nprint(x)")
        assert "async def __user_main__():" in wrapped
        assert "asyncio.run(__user_main__())" in wrapped
        # user code is embedded verbatim as a literal, not re-indented
        assert json.dumps("x = 1\nprint(x)") in wrapped

    def _run_wrapped(self, user_code: str) -> None:
        """Execute wrapped user code exactly like the sandbox would."""
        wrapped = wrap_user_code_in_async(user_code)
        compile(wrapped, "main.py", "exec")
        exec(wrapped, {"__name__": "__main__"})  # noqa: S102

    def test_wrap_preserves_multiline_string_content(self, capsys):
        # A naive re-indent injects 4 spaces into the *content* of multi-line
        # string literals; the literal-embedding wrap must not.
        self._run_wrapped('report = """line1\nline2"""\nprint(report)')
        assert capsys.readouterr().out == "line1\nline2\n"

    def test_wrap_supports_top_level_await(self, capsys):
        self._run_wrapped("import asyncio\nawait asyncio.sleep(0)\nprint('after-await')")
        assert capsys.readouterr().out == "after-await\n"

    def test_wrap_keeps_module_level_scoping(self, capsys):
        # Top-level defs and `global` must behave as in a real module.
        self._run_wrapped("counter = 0\ndef bump():\n    global counter\n    counter += 1\nbump()\nprint(counter)")
        assert capsys.readouterr().out == "1\n"

    def test_param_name_with_hyphen_generates_valid_python(self):
        tool = {
            "name": "search",
            "description": "x",
            "parameters": {
                "type": "object",
                "properties": {"user-name": {"type": "string"}},
                "required": ["user-name"],
            },
        }
        code = build_python_code("exec1", [tool], "pass")
        compile(code, "main.py", "exec")
        # stub takes the normalized identifier but dispatches the original name
        assert "async def search(user_name: str)" in code
        assert '"user-name": user_name' in code

    def test_param_name_with_quote_generates_valid_python(self):
        tool = {
            "name": "t",
            "description": "x",
            "parameters": {"type": "object", "properties": {'a"b': {"type": "string"}}},
        }
        code = build_python_code("exec1", [tool], "pass")
        compile(code, "main.py", "exec")

    @pytest.mark.parametrize(
        "description",
        [
            'ends with a quote"',
            'contains """triple quotes"""',
            "ends with a backslash\\",
        ],
    )
    def test_hostile_docstrings_generate_valid_python(self, description):
        tool = {"name": "t", "description": description, "parameters": None}
        code = build_python_code("exec1", [tool], "pass")
        compile(code, "main.py", "exec")

    def test_preamble_embeds_scoped_sentinel_and_history_path(self):
        preamble = build_replay_preamble("execABC", [WEATHER_TOOL])
        start, end = build_scoped_sentinel("execABC")
        assert f'_PTC_SENTINEL_START = "{start}"' in preamble
        assert f'_PTC_SENTINEL_END = "{end}"' in preamble
        assert f"/mnt/data/{PTC_HISTORY_FILENAME}" in preamble

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("get-weather", "get_weather"),
            ("my tool", "my_tool"),
            ("class", "class_tool"),  # python keyword gets suffix
            ("123tool", "_123tool"),  # leading digit
            ("a.b/c", "abc"),  # strip invalid chars
        ],
    )
    def test_normalize_python_function_name(self, raw, expected):
        assert normalize_python_function_name(raw) == expected


class TestSentinelExtraction:
    def test_no_sentinel_returns_none(self):
        clean, pending = extract_pending_from_stdout("plain output\n", "e1")
        assert pending is None
        assert clean == "plain output\n"

    def test_extracts_single_pending_call(self):
        start, end = build_scoped_sentinel("e1")
        body = json.dumps({"pending": [{"call_id": "call_001", "tool_name": "get-weather", "input": {"city": "SF"}}]})
        stdout = "before\n" + "\n" + start + "\n" + body + "\n" + end + "\n"
        clean, pending = extract_pending_from_stdout(stdout, "e1")
        assert clean == "before\n"
        assert pending == [{"call_id": "call_001", "tool_name": "get-weather", "input": {"city": "SF"}}]

    def test_user_output_without_trailing_newline_preserved(self):
        start, end = build_scoped_sentinel("e1")
        body = json.dumps({"pending": [{"call_id": "call_001", "tool_name": "t", "input": {}}]})
        stdout = "hello" + "\n" + start + "\n" + body + "\n" + end + "\n"
        clean, _ = extract_pending_from_stdout(stdout, "e1")
        assert clean == "hello"

    def test_scoped_to_execution_id(self):
        start, end = build_scoped_sentinel("e1")
        body = json.dumps({"pending": [{"call_id": "call_001", "tool_name": "t", "input": {}}]})
        stdout = start + "\n" + body + "\n" + end + "\n"
        # parsing under a different execution id must not match
        clean, pending = extract_pending_from_stdout(stdout, "OTHER")
        assert pending is None
        assert clean == stdout

    def test_malformed_json_payload_returns_none(self):
        start, end = build_scoped_sentinel("e1")
        stdout = start + "\nnot-json\n" + end + "\n"
        clean, pending = extract_pending_from_stdout(stdout, "e1")
        assert pending is None

    def test_whitespace_padded_marker_not_matched(self):
        # User output that merely contains the marker surrounded by whitespace
        # must NOT be treated as a sentinel (exact line match required).
        start, end = build_scoped_sentinel("e1")
        body = json.dumps({"pending": [{"call_id": "call_001", "tool_name": "t", "input": {}}]})
        stdout = "  " + start + "  \n" + body + "\n" + end + "\n"
        clean, pending = extract_pending_from_stdout(stdout, "e1")
        assert pending is None
        assert clean == stdout

    def test_trailing_carriage_return_tolerated(self):
        # CRLF-captured stdout: the marker line ends with "\r"; still matches.
        start, end = build_scoped_sentinel("e1")
        body = json.dumps({"pending": [{"call_id": "call_001", "tool_name": "t", "input": {}}]})
        stdout = "out\r\n" + start + "\r\n" + body + "\r\n" + end + "\r\n"
        clean, pending = extract_pending_from_stdout(stdout, "e1")
        assert pending == [{"call_id": "call_001", "tool_name": "t", "input": {}}]


class TestReservedFilenames:
    @pytest.mark.parametrize(
        "name",
        ["_ptc_history.json", "sub/../_ptc_history.json", "../escape.txt", "..\\etc\\passwd"],
    )
    def test_reserved_true(self, name):
        assert is_reserved_ptc_filename(name) is True

    @pytest.mark.parametrize("name", ["data.csv", "_ptc_data.csv", "notes_ptc_history.json", ""])
    def test_reserved_false(self, name):
        assert is_reserved_ptc_filename(name) is False

    def test_invalid_execution_id_rejected(self):
        with pytest.raises(ValueError):
            build_scoped_sentinel("bad id with spaces")
