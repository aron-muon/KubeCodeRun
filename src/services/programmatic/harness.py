"""Generate the Python replay harness for Programmatic Tool Calling.

This is a faithful port of the reference ``code-interpreter`` service's
``generateReplayPreamble`` + ``wrapUserCodeInAsync``. The generated
``main.py`` is:

    <replay preamble>          # tool stubs + history replay + sentinel
    <async-wrapped user code>

On each round the sandbox loads ``/mnt/data/_ptc_history.json`` (a map of
``call_001`` -> prior tool result). A tool call that is already in history
returns immediately; the first un-cached call is appended to a pending list,
printed inside execution-scoped sentinel markers, and the process exits 0.
The API parses the sentinel, relays the call to LibreChat, merges the result
into history and re-runs — until the code completes without a new call.
"""

from typing import Any

from .constants import PTC_HISTORY_SANDBOX_PATH, build_scoped_sentinel

_PYTHON_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
    "try", "while", "with", "yield",
}  # fmt: skip


def normalize_python_function_name(name: str) -> str:
    """Normalize a tool name into a valid, non-keyword Python identifier.

    Returns an empty string when ``name`` contains no identifier-safe
    characters (callers should reject such tool names before generation).
    """
    normalized = "".join("_" if ch in "- \t" else ch for ch in name)
    normalized = "".join(ch for ch in normalized if ch.isalnum() or ch == "_")
    if normalized and normalized[0].isdigit():
        normalized = "_" + normalized
    if normalized in _PYTHON_KEYWORDS:
        normalized = normalized + "_tool"
    return normalized


def _json_schema_to_python_type(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if not schema_type:
        return "Any"
    if schema_type == "string":
        return "str"
    if schema_type == "integer":
        return "int"
    if schema_type in ("number", "float"):
        return "float"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "array":
        items = schema.get("items")
        item_type = _json_schema_to_python_type(items) if isinstance(items, dict) else "Any"
        return f"List[{item_type}]"
    if schema_type == "object":
        return "Dict[str, Any]"
    if schema_type == "null":
        return "None"
    return "Any"


def _sorted_property_names(names: list[str], required: list[str]) -> list[str]:
    required_set = set(required)
    return [n for n in names if n in required_set] + [n for n in names if n not in required_set]


def _schema_to_params(schema: dict[str, Any] | None) -> str:
    properties = (schema or {}).get("properties")
    if not properties:
        return ""
    required = (schema or {}).get("required") or []
    required_set = set(required)
    params: list[str] = []
    for name in _sorted_property_names(list(properties.keys()), required):
        py_type = _json_schema_to_python_type(properties[name])
        if name in required_set:
            params.append(f"{name}: {py_type}")
        else:
            params.append(f"{name}: Optional[{py_type}] = None")
    return ", ".join(params)


def _generate_input_dict(schema: dict[str, Any] | None) -> str:
    properties = (schema or {}).get("properties")
    if not properties:
        return ""
    return ", ".join(f'"{name}": {name}' for name in properties)


def _infer_return_type(description: str | None) -> str:
    if not description:
        return "Any"
    desc = description.lower()
    if "returns list" in desc or "returns array" in desc or "list of" in desc:
        return "List[Dict[str, Any]]"
    if "returns dict" in desc or "returns object" in desc:
        return "Dict[str, Any]"
    return "Any"


def _escape_docstring(text: str) -> str:
    # Keep the generated triple-quoted string literal valid.
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def _generate_docstring(tool: dict[str, Any]) -> str:
    doc = tool.get("description") or "No description available."
    parameters = tool.get("parameters") or {}
    properties = parameters.get("properties")
    if properties:
        doc += "\n\n    Parameters:"
        required = parameters.get("required") or []
        required_set = set(required)
        for name in _sorted_property_names(list(properties.keys()), required):
            prop = properties[name]
            kind = "required" if name in required_set else "optional"
            desc = prop.get("description") or "No description"
            doc += f"\n        {name} ({kind}): {desc}"
    return _escape_docstring(doc)


def _generate_tool_stub(tool: dict[str, Any]) -> str:
    name = tool["name"]
    params = _schema_to_params(tool.get("parameters"))
    return_type = _infer_return_type(tool.get("description"))
    docstring = _generate_docstring(tool)
    input_dict = _generate_input_dict(tool.get("parameters"))
    py_name = normalize_python_function_name(name)
    name_comment = f"    # Original tool name: {name}\n" if py_name != name else ""
    return (
        f"async def {py_name}({params}) -> {return_type}:\n"
        f'    """{docstring}"""\n'
        f"{name_comment}"
        f"    _input = {{{input_dict}}}\n"
        "    _input = {k: v for k, v in _input.items() if v is not None}\n"
        f'    return await _execute_tool_internal_async("{name}", _input)\n'
    )


# Literal body of the replay preamble (no interpolation — contains braces).
_REPLAY_PREAMBLE_BODY = '''
def _ptc_load_history() -> Dict[str, Any]:
    try:
        with open(_PTC_HISTORY_PATH, "r", encoding="utf-8") as _hf:
            data = json.load(_hf)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}

_PTC_HISTORY = _ptc_load_history()

_TOOL_CALL_COUNTER = 0
_PTC_PENDING: List[Dict[str, Any]] = []

class ToolExecutionError(Exception):
    """Raised when a cached tool result is marked as an error."""
    pass

def _ptc_emit_pending_and_exit() -> None:
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        payload = json.dumps({"pending": _PTC_PENDING})
    except Exception as e:
        payload = json.dumps({"pending": [], "error": f"serialize_failed: {e}"})
    sys.stdout.write("\\n" + _PTC_SENTINEL_START + "\\n")
    sys.stdout.write(payload + "\\n")
    sys.stdout.write(_PTC_SENTINEL_END + "\\n")
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)

async def _execute_tool_internal_async(tool_name: str, tool_input: Dict[str, Any]) -> Any:
    """Replay-aware tool dispatch: consult history, else emit pending and exit."""
    global _TOOL_CALL_COUNTER
    _TOOL_CALL_COUNTER += 1
    call_id = f"call_{_TOOL_CALL_COUNTER:03d}"

    entry = _PTC_HISTORY.get(call_id)
    if entry is not None:
        if isinstance(entry, dict) and entry.get("is_error"):
            raise ToolExecutionError(entry.get("error_message") or "tool execution failed")
        if isinstance(entry, dict):
            return entry.get("result")
        return entry

    _PTC_PENDING.append({
        "call_id": call_id,
        "tool_name": tool_name,
        "input": tool_input,
    })
    _ptc_emit_pending_and_exit()
'''


def build_replay_preamble(execution_id: str, tools: list[dict[str, Any]]) -> str:
    """Build the replay-mode Python preamble (infrastructure + tool stubs)."""
    start, end = build_scoped_sentinel(execution_id)
    header = (
        "# ============================================================================\n"
        "# PROGRAMMATIC TOOL CALLING INFRASTRUCTURE (replay mode)\n"
        "# Auto-generated - do not modify\n"
        "# ============================================================================\n\n"
        "import json\n"
        "import sys\n"
        "import os\n"
        "import asyncio\n"
        "from typing import Any, Dict, List, Optional, Union\n\n"
        f'_EXECUTION_ID = "{execution_id}"\n'
        f'_PTC_SENTINEL_START = "{start}"\n'
        f'_PTC_SENTINEL_END = "{end}"\n'
        f'_PTC_HISTORY_PATH = os.environ.get("PTC_HISTORY_PATH") or "{PTC_HISTORY_SANDBOX_PATH}"\n'
    )
    stubs = "\n# ============================================================================\n"
    stubs += "# TOOL DEFINITIONS\n"
    stubs += "# ============================================================================\n\n"
    for tool in tools:
        stubs += _generate_tool_stub(tool) + "\n\n"
    return header + _REPLAY_PREAMBLE_BODY + "\n" + stubs


def wrap_user_code_in_async(user_code: str) -> str:
    """Wrap user code in an async main so top-level await works."""
    wrapped = (
        "# ============================================================================\n"
        "# USER CODE BEGINS BELOW\n"
        "# ============================================================================\n\n"
        "async def __user_main__():\n"
        '    """Auto-generated wrapper for user code to support top-level await"""\n'
    )
    for line in user_code.split("\n"):
        wrapped += "\n" if line.strip() == "" else "    " + line + "\n"
    wrapped += '\nif __name__ == "__main__":\n'
    wrapped += "    import asyncio\n"
    wrapped += "    asyncio.run(__user_main__())\n"
    return wrapped


def build_python_code(execution_id: str, tools: list[dict[str, Any]], user_code: str) -> str:
    """Assemble the full ``main.py`` (preamble + async-wrapped user code)."""
    return build_replay_preamble(execution_id, tools) + wrap_user_code_in_async(user_code)
