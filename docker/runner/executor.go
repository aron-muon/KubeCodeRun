package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// LangSpec defines how to execute code for a language.
type LangSpec struct {
	File string // Filename for the code, e.g. "code.py", "main.go"
	Run  string // Shell command to run. {file} is replaced with the code file path, {wd} with working dir.
}

// languages is the single source of truth for language execution commands.
var languages = map[string]LangSpec{
	"python": {File: "code.py", Run: "python {file}"},
	"py":     {File: "code.py", Run: "python {file}"},

	"javascript": {File: "code.js", Run: "node {file}"},
	"js":         {File: "code.js", Run: "node {file}"},

	"typescript": {File: "code.ts", Run: "node /opt/scripts/ts-runner.js {file}"},
	"ts":         {File: "code.ts", Run: "node /opt/scripts/ts-runner.js {file}"},

	"go": {File: "main.go", Run: "go run {file}"},

	"java": {File: "Code.java", Run: "javac {file} && java -cp {wd} Code"},

	"c": {File: "code.c", Run: "gcc {file} -o /tmp/code && /tmp/code"},

	"cpp": {File: "code.cpp", Run: "g++ {file} -o /tmp/code && /tmp/code"},

	"php": {File: "code.php", Run: "php {file}"},

	"rust": {File: "main.rs", Run: "rustc {file} -o /tmp/main && /tmp/main"},
	"rs":   {File: "main.rs", Run: "rustc {file} -o /tmp/main && /tmp/main"},

	"r": {File: "code.r", Run: "Rscript {file}"},

	"fortran": {File: "code.f90", Run: "gfortran {file} -o /tmp/code && /tmp/code"},
	"f90":     {File: "code.f90", Run: "gfortran {file} -o /tmp/code && /tmp/code"},

	"d":     {File: "code.d", Run: "ldc2 {file} -of=/tmp/code && /tmp/code"},
	"dlang": {File: "code.d", Run: "ldc2 {file} -of=/tmp/code && /tmp/code"},
}

// ExecuteRequest is the JSON request body for POST /execute.
type ExecuteRequest struct {
	Code       string `json:"code"`
	Timeout    int    `json:"timeout"`
	WorkingDir string `json:"working_dir"`
}

// ExecuteResponse is the JSON response body for POST /execute.
type ExecuteResponse struct {
	ExitCode        int      `json:"exit_code"`
	Stdout          string   `json:"stdout"`
	Stderr          string   `json:"stderr"`
	ExecutionTimeMs int      `json:"execution_time_ms"`
	State           *string  `json:"state,omitempty"`
	StateErrors     []string `json:"state_errors,omitempty"`
}

// Executor handles code execution requests.
type Executor struct {
	cfg Config
}

// NewExecutor creates a new Executor.
func NewExecutor(cfg Config) *Executor {
	return &Executor{cfg: cfg}
}

// HandleExecute processes POST /execute requests.
func (e *Executor) HandleExecute(w http.ResponseWriter, r *http.Request) {
	var req ExecuteRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid request body"})
		return
	}

	if req.Timeout <= 0 || req.Timeout > e.cfg.MaxExecutionTime {
		req.Timeout = e.cfg.MaxExecutionTime
	}
	if req.WorkingDir == "" {
		req.WorkingDir = e.cfg.WorkingDir
	}

	resp := e.execute(req)
	writeJSON(w, http.StatusOK, resp)
}

func (e *Executor) execute(req ExecuteRequest) ExecuteResponse {
	start := time.Now()

	spec, ok := languages[e.cfg.Language]
	if !ok {
		return ExecuteResponse{
			ExitCode:        1,
			Stderr:          fmt.Sprintf("Unsupported language: %s", e.cfg.Language),
			ExecutionTimeMs: int(time.Since(start).Milliseconds()),
		}
	}

	// Write code to file
	codePath := filepath.Join(req.WorkingDir, spec.File)
	if err := os.WriteFile(codePath, []byte(req.Code), 0644); err != nil {
		return ExecuteResponse{
			ExitCode:        1,
			Stderr:          fmt.Sprintf("Failed to write code file: %v", err),
			ExecutionTimeMs: int(time.Since(start).Milliseconds()),
		}
	}

	// Build the shell command with substitutions
	cmd := strings.ReplaceAll(spec.Run, "{file}", codePath)
	cmd = strings.ReplaceAll(cmd, "{wd}", req.WorkingDir)

	log.Printf("[EXECUTE] language=%s, code_file=%s, timeout=%ds", e.cfg.Language, codePath, req.Timeout)

	// Set up execution environment
	env := os.Environ()
	if e.cfg.NetworkIsolated && (e.cfg.Language == "go") {
		env = appendEnv(env, "GOPROXY", "off")
		env = appendEnv(env, "GOSUMDB", "off")
		log.Println("[EXECUTE] Network isolation: GOPROXY=off, GOSUMDB=off")
	}

	// Execute with timeout
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(req.Timeout)*time.Second)
	defer cancel()

	proc := exec.CommandContext(ctx, "sh", "-c", cmd)
	proc.Dir = req.WorkingDir
	proc.Env = env

	stdout, err := proc.Output()

	elapsed := int(time.Since(start).Milliseconds())

	var stderrStr string
	if ee, ok := err.(*exec.ExitError); ok {
		stderrStr = truncate(string(ee.Stderr), e.cfg.MaxOutputSize)
	} else if err != nil && ctx.Err() == context.DeadlineExceeded {
		return ExecuteResponse{
			ExitCode:        124,
			Stderr:          fmt.Sprintf("Execution timed out after %d seconds", req.Timeout),
			ExecutionTimeMs: elapsed,
		}
	} else if err != nil {
		return ExecuteResponse{
			ExitCode:        1,
			Stderr:          fmt.Sprintf("Execution error: %v", err),
			ExecutionTimeMs: elapsed,
		}
	}

	exitCode := proc.ProcessState.ExitCode()

	log.Printf("[EXECUTE] exit_code=%d, stdout_len=%d, stderr_len=%d, time=%dms",
		exitCode, len(stdout), len(stderrStr), elapsed)

	return ExecuteResponse{
		ExitCode:        exitCode,
		Stdout:          truncate(string(stdout), e.cfg.MaxOutputSize),
		Stderr:          stderrStr,
		ExecutionTimeMs: elapsed,
	}
}

// appendEnv sets or overrides an environment variable in the env slice.
func appendEnv(env []string, key, value string) []string {
	prefix := key + "="
	for i, e := range env {
		if strings.HasPrefix(e, prefix) {
			env[i] = prefix + value
			return env
		}
	}
	return append(env, prefix+value)
}

func truncate(s string, max int) string {
	if len(s) > max {
		return s[:max]
	}
	return s
}
