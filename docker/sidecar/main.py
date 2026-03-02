#!/usr/bin/env python3
"""HTTP Sidecar for Kubernetes Pod Execution.

This sidecar runs alongside the main language container and provides
an HTTP API for code execution. It supports two execution modes:

1. Agent mode (default): Forwards execution requests to an executor agent
   HTTP server running inside the main container. No nsenter, no capabilities,
   no privilege escalation needed. Compatible with GKE Sandbox (gVisor).

2. nsenter mode (legacy): Uses nsenter to execute code in the main container's
   mount namespace. Requires shareProcessNamespace, SYS_PTRACE, SYS_ADMIN,
   SYS_CHROOT capabilities, and allowPrivilegeEscalation: true.

The mode is controlled by the EXECUTION_MODE environment variable.
"""

import asyncio
import os
import shlex
import shutil
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Configuration from environment
WORKING_DIR = os.getenv("WORKING_DIR", "/mnt/data")
LANGUAGE = os.getenv("LANGUAGE", "python")
MAX_EXECUTION_TIME = int(os.getenv("MAX_EXECUTION_TIME", "120"))
MAX_OUTPUT_SIZE = int(os.getenv("MAX_OUTPUT_SIZE", "1048576"))  # 1MB
# Process name to identify main container (set via env, defaults based on language)
MAIN_PROCESS_NAME = os.getenv("MAIN_PROCESS_NAME", "")
# Version from build arg (set via Dockerfile ARG -> ENV)
VERSION = os.getenv("VERSION", "0.0.0-dev")
# Network isolation mode - when true, disables network-dependent features (e.g., Go module proxy)
NETWORK_ISOLATED = os.getenv("NETWORK_ISOLATED", "false").lower() in ("true", "1", "yes")

# Execution mode: "agent" (default, no nsenter) or "nsenter" (legacy)
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "agent")
# Executor port (used in agent mode for the executor agent HTTP server)
EXECUTOR_PORT = int(os.getenv("EXECUTOR_PORT", "9090"))

class ExecuteRequest(BaseModel):
    """Request to execute code."""
    code: str
    timeout: int = Field(default=30, ge=1, le=MAX_EXECUTION_TIME)
    working_dir: str = Field(default=WORKING_DIR)
    initial_state: str | None = None  # Base64-encoded state
    capture_state: bool = False


class ExecuteResponse(BaseModel):
    """Response from code execution."""
    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int
    state: str | None = None  # Base64-encoded state
    state_errors: list | None = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    language: str
    working_dir: str
    timestamp: str


class FileInfo(BaseModel):
    """File information."""
    name: str
    path: str
    size: int
    mime_type: str | None = None


def validate_path_within_working_dir(path: str) -> Path:
    """Validate and resolve a path, ensuring it's within the working directory.

    Uses Path.is_relative_to() for proper path containment validation,
    which correctly handles prefix collision attacks (e.g., /mnt/data vs /mnt/data-evil).

    Args:
        path: The user-provided path to validate

    Returns:
        The resolved Path object if valid

    Raises:
        HTTPException: 403 if path escapes working directory
        HTTPException: 400 if path is invalid
    """
    try:
        file_path = (Path(WORKING_DIR) / path).resolve()
        working_path = Path(WORKING_DIR).resolve()

        # Use is_relative_to() for proper path containment check
        # This correctly handles prefix collisions like /mnt/data vs /mnt/data-evil
        if not file_path.is_relative_to(working_path):
            raise HTTPException(status_code=403, detail="Access denied")

        return file_path
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    os.makedirs(WORKING_DIR, exist_ok=True)
    yield
    # Shutdown - nothing to clean up


app = FastAPI(
    title="KubeCodeRun Sidecar",
    description="HTTP API for code execution in Kubernetes pods",
    version=VERSION,
    lifespan=lifespan,
)


def find_main_container_pid() -> int | None:
    """Find the PID of the main container's process.

    With shareProcessNamespace: true, we can see all processes in /proc.
    The main container runs "sleep infinity", so we look for that process.
    We then use nsenter to enter its mount namespace and run commands.
    """
    my_pid = os.getpid()

    # If MAIN_PROCESS_NAME is set, use that
    if MAIN_PROCESS_NAME:
        targets = [MAIN_PROCESS_NAME]
    else:
        # Main container runs "sleep infinity"
        targets = ["sleep"]

    # Scan /proc for matching processes
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue

        pid = int(pid_dir.name)
        if pid == my_pid or pid == 1:  # Skip self and pause container
            continue

        try:
            # Read the command line
            cmdline_path = pid_dir / "cmdline"
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text().replace("\x00", " ").strip()
                # Check if any target process name is in the cmdline
                for target in targets:
                    if target in cmdline and "sidecar" not in cmdline.lower() and "python" not in cmdline.lower():
                        return pid
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue

    return None


def get_container_env(pid: int) -> dict[str, str]:
    """Read environment variables from a container's process.

    Reads /proc/<pid>/environ which contains the environment variables
    that the process was started with. This ensures the sidecar uses
    the exact same environment as defined in the container's Dockerfile,
    eliminating config drift between Dockerfiles and sidecar code.

    Args:
        pid: The process ID to read environment from

    Returns:
        Dictionary of environment variables
    """
    environ_path = Path(f"/proc/{pid}/environ")
    try:
        content = environ_path.read_bytes().decode("utf-8", errors="replace")
        env = {}
        for item in content.split("\x00"):
            if "=" in item:
                key, value = item.split("=", 1)
                env[key] = value
        return env
    except (FileNotFoundError, PermissionError) as e:
        print(f"[WARN] Failed to read container env from {environ_path}: {e}")
        return {}


def apply_network_isolation_overrides(env: dict[str, str], language: str) -> dict[str, str]:
    """Apply environment overrides when network isolation is enabled.

    When pods are network-isolated (egress blocked), certain language runtimes
    need configuration changes to work offline. This function modifies the
    environment to enable offline/air-gapped operation.

    Args:
        env: The container environment dictionary (will be modified in place)
        language: The language being executed

    Returns:
        The modified environment dictionary
    """
    if not NETWORK_ISOLATED:
        return env

    # Go: Disable module proxy and checksum database for offline operation
    if language in ("go",):
        env["GOPROXY"] = "off"
        env["GOSUMDB"] = "off"
        print(f"[EXECUTE] Network isolation: overriding GOPROXY=off, GOSUMDB=off", flush=True)

    # Future: Add overrides for other languages as needed
    # - Rust: CARGO_NET_OFFLINE=true
    # - npm/Node: npm_config_offline=true
    # - pip/Python: PIP_NO_INDEX=1

    return env


def _write_code_file(language: str, code: str, working_dir: str) -> tuple[list[str], Path | None]:
    """Write code to a temp file and return the bare command to execute it.

    This is the core (DRY) logic shared by both execution modes.
    Returns (command_list, temp_file_path_or_none).
    """
    safe_wd = shlex.quote(working_dir)

    if language in ("python", "py"):
        code_file = Path(working_dir) / "code.py"
        code_file.write_text(code)
        return ["python", str(code_file)], code_file
    elif language in ("javascript", "js"):
        code_file = Path(working_dir) / "code.js"
        code_file.write_text(code)
        return ["node", str(code_file)], code_file
    elif language in ("typescript", "ts"):
        code_file = Path(working_dir) / "code.ts"
        code_file.write_text(code)
        return ["node", "/opt/scripts/ts-runner.js", str(code_file)], code_file
    elif language in ("go",):
        code_file = Path(working_dir) / "main.go"
        code_file.write_text(code)
        return ["go", "run", str(code_file)], code_file
    elif language in ("rust", "rs"):
        code_file = Path(working_dir) / "main.rs"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && rustc {code_file} -o /tmp/main && /tmp/main"], code_file
    elif language in ("java",):
        code_file = Path(working_dir) / "Code.java"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && javac {code_file} && java -cp {working_dir} Code"], code_file
    elif language in ("c",):
        code_file = Path(working_dir) / "code.c"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && gcc {code_file} -o /tmp/code && /tmp/code"], code_file
    elif language in ("cpp",):
        code_file = Path(working_dir) / "code.cpp"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && g++ {code_file} -o /tmp/code && /tmp/code"], code_file
    elif language in ("php",):
        code_file = Path(working_dir) / "code.php"
        code_file.write_text(code)
        return ["php", str(code_file)], code_file
    elif language in ("r",):
        code_file = Path(working_dir) / "code.r"
        code_file.write_text(code)
        return ["Rscript", str(code_file)], code_file
    elif language in ("fortran", "f90"):
        code_file = Path(working_dir) / "code.f90"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && gfortran {code_file} -o /tmp/code && /tmp/code"], code_file
    elif language in ("d", "dlang"):
        code_file = Path(working_dir) / "code.d"
        code_file.write_text(code)
        return ["sh", "-c", f"cd {safe_wd} && ldc2 {code_file} -of=/tmp/code && /tmp/code"], code_file
    else:
        return [], None


def get_language_command(
    language: str, code: str, working_dir: str, container_env: dict[str, str]
) -> tuple[list[str], Path | None]:
    """Get the command to execute code for a given language (nsenter mode).

    Wraps the bare command with `/usr/bin/env -i` and the container's environment
    variables to ensure a clean, reproducible execution context.

    Returns (command_list, temp_file_path_or_none).

    Environment is always read from the container at runtime via /proc/<pid>/environ.
    This eliminates config drift between Dockerfiles and sidecar code.
    """
    cmd, temp_file = _write_code_file(language, code, working_dir)
    if not cmd:
        return [], None

    # Use container env, fall back to minimal defaults if not available
    env = container_env if container_env else {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"}

    # Wrap with /usr/bin/env -i for a clean environment
    env_args = [f"{k}={v}" for k, v in env.items()]
    return ["/usr/bin/env", "-i"] + env_args + cmd, temp_file


def get_language_command_bare(
    language: str, code: str, working_dir: str,
) -> tuple[list[str], Path | None]:
    """Get the bare command to execute code for a given language (agent mode).

    Used in agent mode where the executor agent already inherits the correct
    environment from the container's ENTRYPOINT. No env -i wrapper needed.

    Returns (command_list, temp_file_path_or_none).
    """
    return _write_code_file(language, code, working_dir)


def get_network_isolation_overrides(language: str) -> dict[str, str]:
    """Get environment variable overrides for network-isolated execution.

    Returns a dict of env vars to override in the executor agent's subprocess.
    """
    if not NETWORK_ISOLATED:
        return {}

    overrides = {}
    if language in ("go",):
        overrides["GOPROXY"] = "off"
        overrides["GOSUMDB"] = "off"
        print(f"[EXECUTE] Network isolation: overriding GOPROXY=off, GOSUMDB=off", flush=True)
    return overrides


async def execute_via_agent(request: ExecuteRequest) -> ExecuteResponse:
    """Execute code via the executor agent running in the main container.

    The executor agent is a lightweight HTTP server that runs inside the main
    container, receiving commands over localhost (shared pod network namespace).
    No nsenter, capabilities, or privilege escalation needed.
    """
    start_time = time.perf_counter()

    try:
        # Write code to a temp file and get the bare command (no env -i wrapper)
        cmd, temp_file = get_language_command_bare(
            LANGUAGE, request.code, request.working_dir
        )
        if not cmd:
            return ExecuteResponse(
                exit_code=1,
                stdout="",
                stderr=f"Unsupported language: {LANGUAGE}",
                execution_time_ms=0,
            )
    except Exception as e:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Failed to prepare execution: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )

    # Build env overrides for network isolation
    env_overrides = get_network_isolation_overrides(LANGUAGE)

    print(f"[EXECUTE] agent mode, cmd={cmd}, timeout={request.timeout}s", flush=True)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{EXECUTOR_PORT}/execute",
                json={
                    "command": cmd,
                    "timeout": request.timeout,
                    "working_dir": request.working_dir,
                    "env": env_overrides if env_overrides else None,
                },
                timeout=request.timeout + 10,  # Extra margin for HTTP overhead
            )

            if resp.status_code != 200:
                return ExecuteResponse(
                    exit_code=1,
                    stdout="",
                    stderr=f"Executor agent returned HTTP {resp.status_code}: {resp.text}",
                    execution_time_ms=int((time.perf_counter() - start_time) * 1000),
                )

            data = resp.json()
            return ExecuteResponse(
                exit_code=data.get("exit_code", 1),
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                execution_time_ms=data.get("execution_time_ms", 0),
            )

    except httpx.TimeoutException:
        return ExecuteResponse(
            exit_code=124,
            stdout="",
            stderr=f"Execution timed out after {request.timeout} seconds",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )
    except httpx.ConnectError:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Cannot connect to executor agent at 127.0.0.1:{EXECUTOR_PORT}. "
                   f"Ensure the main container is running the executor agent.",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )
    except Exception as e:
        print(f"[EXECUTE] AGENT EXCEPTION: {type(e).__name__}: {e}", flush=True)
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Agent execution error: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )


async def execute_via_nsenter(request: ExecuteRequest) -> ExecuteResponse:
    """Execute code in the main container using nsenter.

    This requires shareProcessNamespace: true in the pod spec.
    """
    start_time = time.perf_counter()

    try:
        # Find the main container's PID
        main_pid = find_main_container_pid()
        if not main_pid:
            # Fallback: try to execute directly (might work if runtime is in sidecar)
            return await execute_via_subprocess_direct(request)

        # Read the container's environment from /proc/<pid>/environ
        # This ensures we use the exact environment from the Dockerfile,
        # eliminating config drift between Dockerfiles and sidecar code
        container_env = get_container_env(main_pid)

        # Apply network isolation overrides if enabled
        container_env = apply_network_isolation_overrides(container_env, LANGUAGE)

        # Get the command for this language (this writes code to a temp file)
        cmd, temp_file = get_language_command(
            LANGUAGE, request.code, request.working_dir, container_env
        )
        if not cmd:
            return ExecuteResponse(
                exit_code=1,
                stdout="",
                stderr=f"Unsupported language: {LANGUAGE}",
                execution_time_ms=0,
            )
    except Exception as e:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Failed to prepare execution: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )

    # Build nsenter command to enter the main container's mount namespace
    # -t: target PID
    # -m: mount namespace (for filesystem access)
    # --wdns=<dir>: open cwd after setns (avoids getcwd() ENOENT in Node/Go)
    # Note: The spawned process runs in the SIDECAR's cgroup, not the main container's.
    # This means memory-heavy executions count against sidecar's limit.
    # Ensure sidecar has adequate memory for the target language.
    wd_args = [f"--wdns={request.working_dir}"]
    nsenter_cmd = [
        "nsenter",
        "-t", str(main_pid),
        "-m",  # Mount namespace - access main container's filesystem
        *wd_args,
        "--",
    ] + cmd

    # Debug logging - use flush=True to ensure output before container termination
    print(f"[EXECUTE] main_pid={main_pid}, language={LANGUAGE}", flush=True)
    print(f"[EXECUTE] container_env PATH={container_env.get('PATH', 'NOT SET')}", flush=True)
    print(f"[EXECUTE] nsenter_cmd={nsenter_cmd}", flush=True)
    if temp_file:
        print(f"[EXECUTE] code_file={temp_file}, exists={temp_file.exists()}, size={temp_file.stat().st_size if temp_file.exists() else 0}", flush=True)

    try:
        print(f"[EXECUTE] Creating subprocess...", flush=True)
        proc = await asyncio.create_subprocess_exec(
            *nsenter_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.working_dir,
        )
        print(f"[EXECUTE] Subprocess created, pid={proc.pid}, waiting for completion (timeout={request.timeout}s)...", flush=True)

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=request.timeout,
            )
        except TimeoutError:
            print(f"[EXECUTE] TIMEOUT after {request.timeout}s, killing process pid={proc.pid}", flush=True)
            proc.kill()
            await proc.wait()
            return ExecuteResponse(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {request.timeout} seconds",
                execution_time_ms=int((time.perf_counter() - start_time) * 1000),
            )

        execution_time_ms = int((time.perf_counter() - start_time) * 1000)

        stdout_str = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]
        stderr_str = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]

        # Debug logging
        print(f"[EXECUTE] exit_code={proc.returncode}, stdout_len={len(stdout_str)}, stderr_len={len(stderr_str)}", flush=True)
        if stdout_str:
            print(f"[EXECUTE] stdout preview: {stdout_str[:500]!r}", flush=True)
        if stderr_str:
            print(f"[EXECUTE] stderr preview: {stderr_str[:500]!r}", flush=True)

        return ExecuteResponse(
            exit_code=proc.returncode or 0,
            stdout=stdout_str,
            stderr=stderr_str,
            execution_time_ms=execution_time_ms,
        )

    except Exception as e:
        print(f"[EXECUTE] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        print(f"[EXECUTE] Traceback: {traceback.format_exc()}", flush=True)
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"nsenter execution error: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )


async def execute_via_subprocess_direct(request: ExecuteRequest) -> ExecuteResponse:
    """Execute code directly via subprocess (fallback for when nsenter isn't available)."""
    start_time = time.perf_counter()

    # No container env available in fallback mode - use empty dict for defaults
    cmd, temp_file = get_language_command(LANGUAGE, request.code, request.working_dir, {})
    if not cmd:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Unsupported language: {LANGUAGE}",
            execution_time_ms=0,
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.working_dir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=request.timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecuteResponse(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {request.timeout} seconds",
                execution_time_ms=int((time.perf_counter() - start_time) * 1000),
            )

        execution_time_ms = int((time.perf_counter() - start_time) * 1000)

        return ExecuteResponse(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE],
            stderr=stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE],
            execution_time_ms=execution_time_ms,
        )

    except Exception as e:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"Execution error: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )


@app.post("/execute", response_model=ExecuteResponse)
async def execute_code(request: ExecuteRequest) -> ExecuteResponse:
    """Execute code using the configured execution mode (agent or nsenter)."""
    if EXECUTION_MODE == "agent":
        return await execute_via_agent(request)
    else:
        return await execute_via_nsenter(request)


@app.post("/files")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files to the working directory."""
    uploaded = []

    for file in files:
        if not file.filename:
            continue

        # Sanitize filename
        safe_name = Path(file.filename).name
        if not safe_name or safe_name.startswith("."):
            continue

        dest_path = Path(WORKING_DIR) / safe_name

        with open(dest_path, "wb") as f:
            content = await file.read()
            f.write(content)

        uploaded.append(FileInfo(
            name=safe_name,
            path=str(dest_path),
            size=len(content),
        ))

    return {"uploaded": [f.model_dump() for f in uploaded]}


@app.get("/files")
async def list_files():
    """List files in the working directory root."""
    working_path = Path(WORKING_DIR)
    if not working_path.is_dir():
        raise HTTPException(status_code=404, detail="Working directory not found")

    files = []
    for item in working_path.iterdir():
        files.append(FileInfo(
            name=item.name,
            path=item.name,
            size=item.stat().st_size if item.is_file() else 0,
        ))
    return {"files": [f.model_dump() for f in files]}


@app.get("/files/{path:path}")
async def download_file(path: str):
    """Download a file from the working directory."""
    file_path = validate_path_within_working_dir(path)
    working_path = Path(WORKING_DIR).resolve()

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if file_path.is_dir():
        # List directory contents
        files = []
        for item in file_path.iterdir():
            files.append(FileInfo(
                name=item.name,
                path=str(item.relative_to(working_path)),
                size=item.stat().st_size if item.is_file() else 0,
            ))
        return {"files": [f.model_dump() for f in files]}

    return FileResponse(file_path)


@app.delete("/files/{path:path}")
async def delete_file(path: str):
    """Delete a file from the working directory."""
    file_path = validate_path_within_working_dir(path)
    working_path = Path(WORKING_DIR).resolve()

    # Prevent deletion of the working directory itself
    if file_path == working_path:
        raise HTTPException(status_code=403, detail="Cannot delete working directory")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if file_path.is_dir():
        shutil.rmtree(file_path)
    else:
        file_path.unlink()

    return {"deleted": path}


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        language=LANGUAGE,
        working_dir=WORKING_DIR,
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/ready")
async def readiness_check():
    """Readiness check for Kubernetes."""
    # Check if working directory is accessible
    if not os.path.isdir(WORKING_DIR):
        raise HTTPException(status_code=503, detail="Working directory not ready")

    if EXECUTION_MODE == "agent":
        # In agent mode, check if the executor agent is reachable
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{EXECUTOR_PORT}/health",
                    timeout=2,
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=503, detail="Executor agent not healthy")
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Executor agent not reachable")
        except Exception:
            raise HTTPException(status_code=503, detail="Executor agent health check failed")
    else:
        # In nsenter mode, check if we can find the main container
        main_pid = find_main_container_pid()
        if not main_pid:
            raise HTTPException(status_code=503, detail="Main container not found")

    return {"status": "ready"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SIDECAR_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
