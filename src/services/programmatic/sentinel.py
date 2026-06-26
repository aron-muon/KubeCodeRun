"""Parse the replay sentinel block the PTC harness emits on stdout.

Mirrors the reference ``extractPendingFromStdout``: locate the last line
exactly equal to the execution-scoped start marker, the matching end marker
after it, parse the JSON payload between them, and return the user-visible
stdout with that one sentinel block removed.
"""

import json
from typing import Any

from .constants import build_scoped_sentinel


def extract_pending_from_stdout(
    stdout: str | None,
    execution_id: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Return ``(clean_stdout, pending)``.

    ``pending`` is ``None`` when no sentinel block is present (the run either
    completed or errored). Otherwise it is the list of pending tool calls,
    each ``{"call_id", "tool_name", "input"}``, and ``clean_stdout`` has the
    sentinel block stripped so user prints can surface as partial output.
    """
    if stdout is None:
        return "", None

    start_marker, end_marker = build_scoped_sentinel(execution_id)
    lines = stdout.split("\n")
    # Absolute char offset where each line starts (line i is preceded by i newlines).
    line_start_offsets = [0]
    for i in range(len(lines) - 1):
        line_start_offsets.append(line_start_offsets[i] + len(lines[i]) + 1)

    start_idx = _find_last_line(lines, start_marker, 0)
    if start_idx is None:
        return stdout, None
    end_idx = _find_last_line(lines, end_marker, start_idx + 1)
    if end_idx is None:
        return stdout, None

    raw_payload = "\n".join(lines[start_idx + 1 : end_idx]).strip()
    try:
        parsed = json.loads(raw_payload)
    except (json.JSONDecodeError, ValueError):
        return stdout, None
    if not isinstance(parsed, dict):
        return stdout, None
    pending_field = parsed.get("pending")
    if not isinstance(pending_field, list):
        return stdout, None

    pending: list[dict[str, Any]] = []
    for entry in pending_field:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("call_id"), str)
            and isinstance(entry.get("tool_name"), str)
        ):
            raw_input = entry.get("input")
            pending.append(
                {
                    "call_id": entry["call_id"],
                    "tool_name": entry["tool_name"],
                    "input": raw_input if isinstance(raw_input, dict) else {},
                }
            )

    # Rebuild user stdout byte-accurately: everything before the start marker
    # (dropping the single runtime-inserted "\n" the harness emits right before
    # it) concatenated with everything after the end marker's newline. No other
    # bytes are touched, so exact-format user output is preserved.
    start_offset = line_start_offsets[start_idx]
    raw_head = stdout[:start_offset]
    head = raw_head[:-1] if raw_head.endswith("\n") else raw_head
    if end_idx + 1 < len(line_start_offsets):
        tail_start = line_start_offsets[end_idx + 1]
    else:
        tail_start = len(stdout)
    tail = stdout[tail_start:]
    cleaned = head + tail
    return cleaned, pending


def _find_last_line(lines: list[str], marker: str, search_from: int) -> int | None:
    # Match the sentinel line exactly. The harness emits the marker on its own
    # line, so we tolerate only a trailing carriage return (CRLF-captured
    # stdout). We deliberately do NOT strip surrounding whitespace: that would
    # let user output which merely contains the marker padded by spaces be
    # mistaken for a sentinel and shift the byte-accurate reconstruction.
    for i in range(len(lines) - 1, search_from - 1, -1):
        if lines[i].rstrip("\r") == marker:
            return i
    return None
