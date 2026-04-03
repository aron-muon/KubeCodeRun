package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

// Config holds runner configuration from environment variables.
type Config struct {
	Language         string
	WorkingDir       string
	MaxExecutionTime int
	MaxOutputSize    int
	NetworkIsolated  bool
	Port             int
}

func loadConfig() Config {
	maxExec, _ := strconv.Atoi(getenv("MAX_EXECUTION_TIME", "120"))
	maxOutput, _ := strconv.Atoi(getenv("MAX_OUTPUT_SIZE", "1048576"))
	port, _ := strconv.Atoi(getenv("RUNNER_PORT", "8080"))

	return Config{
		Language:         getenv("LANGUAGE", "python"),
		WorkingDir:       getenv("WORKING_DIR", "/mnt/data"),
		MaxExecutionTime: maxExec,
		MaxOutputSize:    maxOutput,
		NetworkIsolated:  getenv("NETWORK_ISOLATED", "false") == "true",
		Port:             port,
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	cfg := loadConfig()

	executor := NewExecutor(cfg)
	fileHandler := NewFileHandler(cfg.WorkingDir)

	mux := http.NewServeMux()

	// Execution
	mux.HandleFunc("POST /execute", executor.HandleExecute)

	// Files
	mux.HandleFunc("POST /files", fileHandler.HandleUpload)
	mux.HandleFunc("GET /files", fileHandler.HandleList)
	mux.HandleFunc("GET /files/{path...}", fileHandler.HandleDownload)
	mux.HandleFunc("DELETE /files/{path...}", fileHandler.HandleDelete)

	// Health
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":      "healthy",
			"language":    cfg.Language,
			"working_dir": cfg.WorkingDir,
			"timestamp":   time.Now().UTC().Format(time.RFC3339),
		})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		if info, err := os.Stat(cfg.WorkingDir); err != nil || !info.IsDir() {
			http.Error(w, `{"detail":"Working directory not ready"}`, http.StatusServiceUnavailable)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})

	addr := fmt.Sprintf("0.0.0.0:%d", cfg.Port)
	server := &http.Server{Addr: addr, Handler: mux}

	// Graceful shutdown on SIGTERM/SIGINT
	done := make(chan os.Signal, 1)
	signal.Notify(done, syscall.SIGTERM, syscall.SIGINT)

	go func() {
		log.Printf("[RUNNER] Starting on %s (language=%s, working_dir=%s)", addr, cfg.Language, cfg.WorkingDir)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[RUNNER] Server error: %v", err)
		}
	}()

	<-done
	log.Println("[RUNNER] Shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	server.Shutdown(ctx)
}
