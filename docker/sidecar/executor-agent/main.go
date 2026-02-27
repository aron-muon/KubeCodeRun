// Executor Agent - Lightweight HTTP server for code execution in Kubernetes pods.
//
// This binary runs inside the main (language) container and provides an HTTP API
// that the sidecar uses to execute code. It replaces the nsenter-based approach,
// enabling execution without any Linux capabilities or privilege escalation.
//
// Architecture:
//   - Listens on localhost (pod-internal only) on a configurable port (default: 9090)
//   - Receives execution requests from the sidecar via HTTP
//   - Spawns subprocesses using the container's inherited environment (PATH, HOME, etc.)
//   - Returns stdout, stderr, exit code, and execution time
//
// The agent inherits its environment from the container's ENTRYPOINT (env -i PATH=... HOME=...),
// ensuring subprocesses run with the exact same sanitized environment as the language runtime.

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	defaultPort   = 9090
	maxOutputSize = 1048576  // 1MB - matches sidecar's MAX_OUTPUT_SIZE
	maxBodySize   = 10485760 // 10MB
)

// ExecuteRequest is the JSON request body for /execute.
type ExecuteRequest struct {
	Command    []string          `json:"command"`
	Timeout    int               `json:"timeout"`
	WorkingDir string            `json:"working_dir"`
	Env        map[string]string `json:"env,omitempty"`
}

// ExecuteResponse is the JSON response body for /execute.
type ExecuteResponse struct {
	ExitCode        int    `json:"exit_code"`
	Stdout          string `json:"stdout"`
	Stderr          string `json:"stderr"`
	ExecutionTimeMs int64  `json:"execution_time_ms"`
}

func handleExecute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodySize))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, ExecuteResponse{
			ExitCode: 1, Stderr: "Failed to read request body",
		})
		return
	}

	var req ExecuteRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, ExecuteResponse{
			ExitCode: 1, Stderr: fmt.Sprintf("Invalid JSON: %v", err),
		})
		return
	}

	if len(req.Command) == 0 {
		writeJSON(w, http.StatusBadRequest, ExecuteResponse{
			ExitCode: 1, Stderr: "No command specified",
		})
		return
	}

	timeout := req.Timeout
	if timeout <= 0 {
		timeout = 30
	}

	workingDir := req.WorkingDir
	if workingDir == "" {
		workingDir = "/mnt/data"
	}

	// Validate that working directory is within the safe /mnt/data directory.
	// Use filepath.Clean + exact-prefix check to prevent traversal to e.g. /mnt/data2.
	absDir, err := filepath.Abs(workingDir)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, ExecuteResponse{
			ExitCode: 1, Stderr: fmt.Sprintf("Invalid working directory: %v", err),
		})
		return
	}
	absDir = filepath.Clean(absDir)
	if absDir != "/mnt/data" && !strings.HasPrefix(absDir, "/mnt/data/") {
		writeJSON(w, http.StatusBadRequest, ExecuteResponse{
			ExitCode: 1, Stderr: fmt.Sprintf("Invalid working directory: must be within /mnt/data, got %q", workingDir),
		})
		return
	}
	workingDir = absDir

	fmt.Fprintf(os.Stdout, "[executor-agent] cmd=%v timeout=%ds dir=%s\n",
		req.Command, timeout, workingDir)

	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, req.Command[0], req.Command[1:]...)
	cmd.Dir = workingDir

	// Inherit the current process environment (from container's ENTRYPOINT env -i).
	// Merge request-provided env overrides by replacing existing keys (so the
	// override actually takes effect regardless of runtime first/last-wins semantics).
	if len(req.Env) > 0 {
		env := os.Environ()
		for k, v := range req.Env {
			prefix := k + "="
			found := false
			for i, e := range env {
				if strings.HasPrefix(e, prefix) {
					env[i] = prefix + v
					found = true
					break
				}
			}
			if !found {
				env = append(env, prefix+v)
			}
		}
		cmd.Env = env
	}

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err = cmd.Run()
	elapsed := time.Since(start).Milliseconds()

	exitCode := 0
	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			fmt.Fprintf(os.Stdout, "[executor-agent] TIMEOUT after %ds\n", timeout)
			writeJSON(w, http.StatusOK, ExecuteResponse{
				ExitCode:        124,
				Stdout:          "",
				Stderr:          fmt.Sprintf("Execution timed out after %d seconds", timeout),
				ExecutionTimeMs: elapsed,
			})
			return
		}
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			writeJSON(w, http.StatusOK, ExecuteResponse{
				ExitCode:        1,
				Stdout:          "",
				Stderr:          fmt.Sprintf("Failed to execute command: %v", err),
				ExecutionTimeMs: elapsed,
			})
			return
		}
	}

	stdoutStr := truncate(stdout.String(), maxOutputSize)
	stderrStr := truncate(stderr.String(), maxOutputSize)

	fmt.Fprintf(os.Stdout, "[executor-agent] exit=%d stdout=%d stderr=%d time=%dms\n",
		exitCode, len(stdoutStr), len(stderrStr), elapsed)

	writeJSON(w, http.StatusOK, ExecuteResponse{
		ExitCode:        exitCode,
		Stdout:          stdoutStr,
		Stderr:          stderrStr,
		ExecutionTimeMs: elapsed,
	})
}

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "healthy"})
}

func handleReady(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data) //nolint:errcheck
}

func truncate(s string, maxLen int) string {
	if len(s) > maxLen {
		return s[:maxLen]
	}
	return s
}

func main() {
	port := defaultPort

	// Parse --port flag from CLI args
	for i := 1; i < len(os.Args)-1; i++ {
		if os.Args[i] == "--port" {
			if p, err := strconv.Atoi(os.Args[i+1]); err == nil {
				port = p
			}
		}
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/execute", handleExecute)
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/ready", handleReady)

	server := &http.Server{
		Addr:         fmt.Sprintf("127.0.0.1:%d", port),
		Handler:      mux,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 300 * time.Second,
	}

	// Graceful shutdown on SIGTERM/SIGINT
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
		<-sigCh
		fmt.Fprintln(os.Stdout, "[executor-agent] Shutting down...")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		server.Shutdown(ctx) //nolint:errcheck
	}()

	fmt.Fprintf(os.Stdout, "[executor-agent] Listening on 127.0.0.1:%d\n", port)
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		fmt.Fprintf(os.Stderr, "[executor-agent] Server error: %v\n", err)
		os.Exit(1)
	}
}
