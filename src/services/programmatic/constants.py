"""Shared constants for Programmatic Tool Calling (PTC).

PTC lets the model write Python/Bash code that calls LibreChat tools as
async functions. Because KubeCodeRun execution pods have **no network
egress** (NetworkPolicy ``egress: []``), the live-callback bridge used by
hosted code interpreters is impossible here. Instead PTC uses the
**stateless replay model**: each round the sandbox re-runs the user code
from scratch, replaying prior tool results from an injected history file
(``_ptc_history.json``); on the first un-cached tool call it prints a
scoped sentinel block and exits, and the API relays that call to LibreChat
which executes it and continues the loop.

This mirrors the reference ``code-interpreter`` service so the
``@librechat/agents`` ``ProgrammaticToolCalling`` client (which POSTs to
``/exec/programmatic``) works unchanged.
"""

import re

# Relative filename of the replay history fixture injected into the sandbox.
PTC_HISTORY_FILENAME = "_ptc_history.json"

# Absolute path inside the sandbox (mounted files land under /mnt/data).
PTC_HISTORY_SANDBOX_PATH = f"/mnt/data/{PTC_HISTORY_FILENAME}"

# Sentinel prefixes the replay preamble emits on stdout to surface pending
# tool calls. The runtime markers embed the execution id so user code cannot
# forge a well-formed sentinel block by printing the raw literals.
PTC_SENTINEL_START_PREFIX = "__PTC_PENDING_V1_START__"
PTC_SENTINEL_END_PREFIX = "__PTC_PENDING_V1_END__"

# Execution-id charset, enforced before interpolation into generated code.
_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Guard rails (mirror the reference service caps).
MAX_TOOLS_PER_REQUEST = 128
MAX_REPLAY_CALLS = 200
EXECUTION_STATE_TTL_SECONDS = 600


def build_scoped_sentinel(execution_id: str) -> tuple[str, str]:
    """Return execution-scoped (start, end) sentinel markers.

    Raises:
        ValueError: if ``execution_id`` contains characters outside the
            allowed charset (defense against code injection through the
            interpolated preamble).
    """
    if not _EXECUTION_ID_RE.match(execution_id):
        raise ValueError(f'executionId "{execution_id}" contains invalid characters')
    return (
        f"{PTC_SENTINEL_START_PREFIX}__{execution_id}",
        f"{PTC_SENTINEL_END_PREFIX}__{execution_id}",
    )


def is_reserved_ptc_filename(name: str) -> bool:
    """True for any filename the caller must not supply.

    A name is reserved when its post-normalization basename is
    ``_ptc_history.json`` (it would shadow the injected replay fixture and
    silently corrupt replay correctness) or when it escapes the submission
    directory via leading ``..``. Normalization folds backslashes to ``/``,
    drops empty/``.`` segments and resolves ``..`` by popping.
    """
    if not isinstance(name, str) or name == "":
        return False
    unified = name.replace("\\", "/")
    segments: list[str] = []
    escapes = False
    for seg in unified.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not segments:
                escapes = True
            else:
                segments.pop()
            continue
        segments.append(seg)
    if escapes:
        return True
    basename = segments[-1] if segments else ""
    return basename == PTC_HISTORY_FILENAME
