#!/usr/bin/env python3
"""HTTP Sidecar for Kubernetes Pod Execution.

This sidecar runs alongside the main language container and provides
an HTTP API for code execution. It uses nsenter to execute code in
the main container's mount namespace.

Requires: shareProcessNamespace: true in the pod spec.
"""

import asyncio
import os
import shutil
import shlex
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


# Configuration from environment
WORKING_DIR = os.getenv("WORKING_DIR", "/mnt/data")
LANGUAGE = os.getenv("LANGUAGE", "python")
MAX_EXECUTION_TIME = int(os.getenv("MAX_EXECUTION_TIME", "120"))
MAX_OUTPUT_SIZE = int(os.getenv("MAX_OUTPUT_SIZE", "1048576"))  # 1MB
# Process name to identify main container (set via env, defaults based on language)
MAIN_PROCESS_NAME = os.getenv("MAIN_PROCESS_NAME", "")


class ExecuteRequest(BaseModel):
    """Request to execute code."""
    code: str
    timeout: int = Field(default=30, ge=1, le=MAX_EXECUTION_TIME)
    working_dir: str = Field(default=WORKING_DIR)
    initial_state: Optional[str] = None  # Base64-encoded state
    capture_state: bool = False


class ExecuteResponse(BaseModel):
    """Response from code execution."""
    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int
    state: Optional[str] = None  # Base64-encoded state
    state_errors: Optional[list] = None


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
    mime_type: Optional[str] = None


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
    version="1.0.0",
    lifespan=lifespan,
)


def find_main_container_pid() -> Optional[int]:
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


def get_language_command(language: str, code: str, working_dir: str) -> tuple[list[str], Optional[Path]]:
    """Get the command to execute code for a given language.

    Returns (command_list, temp_file_path_or_none).

    All commands are wrapped in 'sh -c' with explicit 'cd' and proper
    environment setup for the target language runtime.

    IMPORTANT: When using nsenter -m (mount namespace only), the shell inherits
    the sidecar's environment, not the target container's. We must explicitly
    set PATH and other env vars to match each language container's ENTRYPOINT.
    """
    # Helper to wrap command with cd to working directory
    def wrap_cmd(cmd: str, env_setup: str = "") -> list[str]:
        # Sanitize working_dir to prevent command injection
        safe_working_dir = shlex.quote(working_dir)
        if env_setup:
            return ["sh", "-c", f"{env_setup} cd {safe_working_dir} && {cmd}"]
        return ["sh", "-c", f"cd {safe_working_dir} && {cmd}"]

    # Environment setup strings for each language runtime
    # These match the ENTRYPOINT environment in each language's Dockerfile

    # Python: standard PATH (python3 is in /usr/local/bin)
    PYTHON_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export PYTHONUNBUFFERED=1 &&"

    # Node.js/TypeScript: needs NODE_PATH for global modules
    NODE_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export NODE_PATH=/usr/local/lib/node_modules && export HOME=/tmp &&"

    # Go: needs /usr/local/go/bin in PATH
    GO_ENV = "export PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export GO111MODULE=on && export GOPROXY=https://proxy.golang.org,direct && export GOCACHE=/mnt/data/go-build &&"

    # Rust: needs /usr/local/cargo/bin in PATH
    RUST_ENV = "export PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export CARGO_HOME=/usr/local/cargo && export RUSTUP_HOME=/usr/local/rustup &&"

    # Java: needs /opt/java/openjdk/bin in PATH
    JAVA_ENV = "export PATH=/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export CLASSPATH=/mnt/data:/opt/java/lib/* &&"

    # C/C++: standard PATH (gcc/g++ are in /usr/bin or /usr/local/bin)
    C_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export CC=gcc && export CXX=g++ &&"

    # PHP: needs composer bin in PATH
    PHP_ENV = "export PATH=/opt/composer/global/vendor/bin:/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export COMPOSER_HOME=/opt/composer/global &&"

    # R: standard PATH with R_LIBS_USER
    R_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export R_LIBS_USER=/usr/local/lib/R/site-library &&"

    # Fortran: gfortran compiler
    FORTRAN_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export HOME=/tmp && export FC=gfortran &&"

    # D: ldc2 compiler
    D_ENV = "export PATH=/usr/local/bin:/usr/bin:/bin && export HOME=/tmp &&"

    if language in ("python", "py"):
        # Write code to file instead of -c for better error messages and multiline support
        code_file = Path(working_dir) / "code.py"
        code_file.write_text(code)
        return wrap_cmd(f"python3 {code_file}", PYTHON_ENV), code_file
    elif language in ("javascript", "js"):
        code_file = Path(working_dir) / "code.js"
        code_file.write_text(code)
        return wrap_cmd(f"node {code_file}", NODE_ENV), code_file
    elif language in ("typescript", "ts"):
        code_file = Path(working_dir) / "code.ts"
        code_file.write_text(code)
        # Use tsc + node instead of ts-node (ts-node has stdout capture issues)
        # Compile to /tmp to avoid polluting the working directory
        return wrap_cmd(f"tsc {code_file} --outDir /tmp && node /tmp/code.js", NODE_ENV), code_file
    elif language in ("go",):
        code_file = Path(working_dir) / "main.go"
        code_file.write_text(code)
        return wrap_cmd(f"go run {code_file}", GO_ENV), code_file
    elif language in ("rust", "rs"):
        code_file = Path(working_dir) / "main.rs"
        code_file.write_text(code)
        return wrap_cmd(f"rustc {code_file} -o /tmp/main && /tmp/main", RUST_ENV), code_file
    elif language in ("java",):
        code_file = Path(working_dir) / "Code.java"
        code_file.write_text(code)
        return wrap_cmd(f"javac {code_file} && java -cp {working_dir} Code", JAVA_ENV), code_file
    elif language in ("c",):
        code_file = Path(working_dir) / "code.c"
        code_file.write_text(code)
        return wrap_cmd(f"gcc {code_file} -o /tmp/code && /tmp/code", C_ENV), code_file
    elif language in ("cpp",):
        code_file = Path(working_dir) / "code.cpp"
        code_file.write_text(code)
        return wrap_cmd(f"g++ {code_file} -o /tmp/code && /tmp/code", C_ENV), code_file
    elif language in ("php",):
        code_file = Path(working_dir) / "code.php"
        code_file.write_text(code)
        return wrap_cmd(f"php {code_file}", PHP_ENV), code_file
    elif language in ("r",):
        code_file = Path(working_dir) / "code.r"
        code_file.write_text(code)
        return wrap_cmd(f"Rscript {code_file}", R_ENV), code_file
    elif language in ("fortran", "f90"):
        code_file = Path(working_dir) / "code.f90"
        code_file.write_text(code)
        return wrap_cmd(f"gfortran {code_file} -o /tmp/code && /tmp/code", FORTRAN_ENV), code_file
    elif language in ("d", "dlang"):
        code_file = Path(working_dir) / "code.d"
        code_file.write_text(code)
        return wrap_cmd(f"ldc2 {code_file} -of=/tmp/code && /tmp/code", D_ENV), code_file
    else:
        return [], None


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

        # Get the command for this language (this writes code to a temp file)
        cmd, temp_file = get_language_command(LANGUAGE, request.code, request.working_dir)
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
    # Note: The spawned process runs in the SIDECAR's cgroup, not the main container's.
    # This means memory-heavy executions count against sidecar's limit.
    # Ensure sidecar has adequate memory for the target language.
    nsenter_cmd = [
        "nsenter",
        "-t", str(main_pid),
        "-m",  # Mount namespace - access main container's filesystem
        "--",
    ] + cmd

    # Debug logging
    print(f"[EXECUTE] main_pid={main_pid}, language={LANGUAGE}")
    print(f"[EXECUTE] nsenter_cmd={nsenter_cmd}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *nsenter_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.working_dir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=request.timeout,
            )
        except asyncio.TimeoutError:
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
        print(f"[EXECUTE] exit_code={proc.returncode}, stdout_len={len(stdout_str)}, stderr_len={len(stderr_str)}")
        if stdout_str:
            print(f"[EXECUTE] stdout preview: {stdout_str[:500]!r}")
        if stderr_str:
            print(f"[EXECUTE] stderr preview: {stderr_str[:500]!r}")

        return ExecuteResponse(
            exit_code=proc.returncode or 0,
            stdout=stdout_str,
            stderr=stderr_str,
            execution_time_ms=execution_time_ms,
        )

    except Exception as e:
        return ExecuteResponse(
            exit_code=1,
            stdout="",
            stderr=f"nsenter execution error: {str(e)}\n{traceback.format_exc()}",
            execution_time_ms=int((time.perf_counter() - start_time) * 1000),
        )


async def execute_via_subprocess_direct(request: ExecuteRequest) -> ExecuteResponse:
    """Execute code directly via subprocess (fallback for when nsenter isn't available)."""
    start_time = time.perf_counter()

    cmd, temp_file = get_language_command(LANGUAGE, request.code, request.working_dir)
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
        except asyncio.TimeoutError:
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
    """Execute code and return results via nsenter."""
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

    # Check if we can find the main container
    main_pid = find_main_container_pid()
    if not main_pid:
        raise HTTPException(status_code=503, detail="Main container not found")

    return {"status": "ready"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SIDECAR_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
