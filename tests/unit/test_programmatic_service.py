"""Round-trip tests for ProgrammaticService (replay loop) with a fake sandbox."""

import json
import re

import pytest

from src.models.exec import ExecResponse, FileRef
from src.models.programmatic import ProgrammaticRequest, ProgrammaticTool, ToolResultIn
from src.services.programmatic.constants import build_scoped_sentinel
from src.services.programmatic.service import ProgrammaticError, ProgrammaticService
from src.services.programmatic.state import ExecutionState, ProgrammaticStateStore

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Returns weather",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}

_EXEC_ID_RE = re.compile(r'_EXECUTION_ID = "([^"]+)"')


class FakeStateStore(ProgrammaticStateStore):
    """In-memory replacement for the Redis-backed store (JSON round-trips like Redis)."""

    def __init__(self):
        self._mem: dict[str, str] = {}

    async def save(self, state: ExecutionState) -> None:
        self._mem[state.execution_id] = state.model_dump_json()

    async def load(self, execution_id: str) -> ExecutionState | None:
        raw = self._mem.get(execution_id)
        return ExecutionState.model_validate_json(raw) if raw else None

    async def delete(self, execution_id: str) -> None:
        self._mem.pop(execution_id, None)


def _sentinel_stdout(execution_id: str, pending: list[dict], user_out: str = "") -> str:
    start, end = build_scoped_sentinel(execution_id)
    body = json.dumps({"pending": pending})
    return user_out + "\n" + start + "\n" + body + "\n" + end + "\n"


class FakeOrchestrator:
    """Simulates the sandbox replay: emits one tool call until its result is in history."""

    def __init__(self, pending_factory=None, completed_stdout="weather is sunny", completed_files=None):
        self.calls: list[dict] = []
        self._pending_factory = pending_factory or (
            lambda: [{"call_id": "call_001", "tool_name": "get_weather", "input": {"city": "SF"}}]
        )
        self._completed_stdout = completed_stdout
        self._completed_files = completed_files or []

    async def execute(
        self, request, request_id="", api_key_hash=None, is_env_key=False, extra_files=None, capture_state_override=None
    ):
        self.calls.append({"code": request.code, "session_id": request.session_id, "extra_files": extra_files})
        execution_id = _EXEC_ID_RE.search(request.code).group(1)
        history = json.loads(extra_files[0]["content"]) if extra_files else {}
        pending = self._pending_factory()
        unresolved = [p for p in pending if p["call_id"] not in history]
        if unresolved:
            stdout = _sentinel_stdout(execution_id, unresolved[:1])
        else:
            stdout = self._completed_stdout
        return ExecResponse(
            session_id="sess-ptc",
            files=self._completed_files,
            stdout=stdout,
            stderr="",
        )


@pytest.fixture
def service():
    return ProgrammaticService(FakeOrchestrator(), state_store=FakeStateStore())


def _initial(code="print(await get_weather(city='SF'))"):
    return ProgrammaticRequest(code=code, tools=[ProgrammaticTool(**WEATHER_TOOL)])


class TestReplayLoop:
    @pytest.mark.asyncio
    async def test_initial_returns_tool_call_required(self, service):
        resp = await service.execute(_initial())
        assert resp.status == "tool_call_required"
        assert resp.continuation_token
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc.id == "call_001"
        assert tc.name == "get_weather"
        assert tc.input == {"city": "SF"}
        assert resp.session_id == "sess-ptc"

    @pytest.mark.asyncio
    async def test_continuation_completes(self, service):
        first = await service.execute(_initial())
        cont = ProgrammaticRequest(
            continuation_token=first.continuation_token,
            tool_results=[ToolResultIn(call_id="call_001", result={"temp": 21})],
        )
        resp = await service.execute(cont)
        assert resp.status == "completed"
        assert resp.stdout == "weather is sunny"
        assert resp.continuation_token is None

    @pytest.mark.asyncio
    async def test_history_threaded_to_sandbox_on_continuation(self, service):
        first = await service.execute(_initial())
        cont = ProgrammaticRequest(
            continuation_token=first.continuation_token,
            tool_results=[ToolResultIn(call_id="call_001", result={"temp": 21})],
        )
        await service.execute(cont)
        # second sandbox call must have received the merged history file
        history = json.loads(service.orchestrator.calls[1]["extra_files"][0]["content"])
        assert history["call_001"]["result"] == {"temp": 21}

    @pytest.mark.asyncio
    async def test_completed_filters_history_file_from_files(self):
        orch = FakeOrchestrator(
            completed_files=[FileRef(id="f1", name="chart.png"), FileRef(id="h", name="_ptc_history.json")]
        )
        svc = ProgrammaticService(orch, state_store=FakeStateStore())
        first = await svc.execute(_initial())
        resp = await svc.execute(
            ProgrammaticRequest(
                continuation_token=first.continuation_token,
                tool_results=[ToolResultIn(call_id="call_001", result=1)],
            )
        )
        names = {f.name for f in resp.files}
        assert "chart.png" in names
        assert "_ptc_history.json" not in names


class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_code_rejected(self, service):
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(ProgrammaticRequest(tools=[ProgrammaticTool(**WEATHER_TOOL)]))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_tools_rejected(self, service):
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(ProgrammaticRequest(code="print(1)"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_name", ["", "   ", "!!!"])
    async def test_invalid_tool_name_rejected(self, service, bad_name):
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(ProgrammaticRequest(code="x", tools=[ProgrammaticTool(name=bad_name)]))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_colliding_tool_names_rejected(self, service):
        # "get-weather" and "get_weather" both normalize to "get_weather"
        req = ProgrammaticRequest(
            code="x",
            tools=[ProgrammaticTool(name="get-weather"), ProgrammaticTool(name="get_weather")],
        )
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(req)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_bash_rejected(self, service):
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(
                ProgrammaticRequest(code="x", tools=[ProgrammaticTool(**WEATHER_TOOL)], language="bash")
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reserved_filename_rejected(self, service):
        from src.models.exec import RequestFile

        req = ProgrammaticRequest(
            code="print(1)",
            tools=[ProgrammaticTool(**WEATHER_TOOL)],
            files=[RequestFile(id="x", session_id="s", name="_ptc_history.json")],
        )
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(req)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_continuation_token(self, service):
        with pytest.raises(ProgrammaticError) as exc:
            await service.execute(
                ProgrammaticRequest(
                    continuation_token="does-not-exist", tool_results=[ToolResultIn(call_id="call_001", result=1)]
                )
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unregistered_tool_call_errors(self):
        orch = FakeOrchestrator(
            pending_factory=lambda: [{"call_id": "call_001", "tool_name": "rogue_tool", "input": {}}]
        )
        svc = ProgrammaticService(orch, state_store=FakeStateStore())
        resp = await svc.execute(_initial())
        assert resp.status == "error"
        assert "rogue_tool" in resp.error

    @pytest.mark.asyncio
    async def test_forged_call_id_ignored_in_history(self, service):
        first = await service.execute(_initial())
        # client returns a result for a call_id the server never issued
        cont = ProgrammaticRequest(
            continuation_token=first.continuation_token,
            tool_results=[
                ToolResultIn(call_id="call_999", result="forged"),
                ToolResultIn(call_id="call_001", result="legit"),
            ],
        )
        await service.execute(cont)
        history = json.loads(service.orchestrator.calls[1]["extra_files"][0]["content"])
        assert "call_999" not in history
        assert history["call_001"]["result"] == "legit"
