package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// startTestServer starts the runner on a random port and returns the base URL.
func startTestServer(t *testing.T, language string) string {
	t.Helper()

	workDir := t.TempDir()

	cfg := Config{
		Language:         language,
		WorkingDir:       workDir,
		MaxExecutionTime: 10,
		MaxOutputSize:    1048576,
		NetworkIsolated:  false,
		Port:             0, // random port
	}

	executor := NewExecutor(cfg)
	fileHandler := NewFileHandler(cfg.WorkingDir)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /execute", executor.HandleExecute)
	mux.HandleFunc("POST /files", fileHandler.HandleUpload)
	mux.HandleFunc("GET /files", fileHandler.HandleList)
	mux.HandleFunc("GET /files/{path...}", fileHandler.HandleDownload)
	mux.HandleFunc("DELETE /files/{path...}", fileHandler.HandleDelete)
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "healthy"})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Failed to start listener: %v", err)
	}

	server := &http.Server{Handler: mux}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })

	return fmt.Sprintf("http://%s", listener.Addr().String())
}

// uploadFile uploads a file to the runner using the same multipart format as pool.py.
func uploadFile(baseURL, filename string, content []byte) (*http.Response, error) {
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)

	// Match the field name used by pool.py: files={"files": (filename, content)}
	part, err := writer.CreateFormFile("files", filename)
	if err != nil {
		return nil, err
	}
	part.Write(content)
	writer.Close()

	return http.Post(baseURL+"/files", writer.FormDataContentType(), &buf)
}

func executeCode(baseURL, code string, timeout int) (*ExecuteResponse, error) {
	reqBody, _ := json.Marshal(ExecuteRequest{Code: code, Timeout: timeout})
	resp, err := http.Post(baseURL+"/execute", "application/json", bytes.NewReader(reqBody))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var result ExecuteResponse
	json.NewDecoder(resp.Body).Decode(&result)
	return &result, nil
}

func TestHealthEndpoint(t *testing.T) {
	baseURL := startTestServer(t, "py")

	resp, err := http.Get(baseURL + "/health")
	if err != nil {
		t.Fatalf("Health check failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("Expected 200, got %d", resp.StatusCode)
	}
}

func TestReadyEndpoint(t *testing.T) {
	baseURL := startTestServer(t, "py")

	resp, err := http.Get(baseURL + "/ready")
	if err != nil {
		t.Fatalf("Ready check failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("Expected 200, got %d", resp.StatusCode)
	}
}

func TestFileUploadAndList(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Upload a file
	resp, err := uploadFile(baseURL, "test.csv", []byte("name,age\nAlice,30\n"))
	if err != nil {
		t.Fatalf("Upload failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("Upload returned %d: %s", resp.StatusCode, body)
	}

	// Verify response
	var uploadResult map[string]any
	json.NewDecoder(resp.Body).Decode(&uploadResult)
	uploaded := uploadResult["uploaded"].([]any)
	if len(uploaded) != 1 {
		t.Fatalf("Expected 1 uploaded file, got %d", len(uploaded))
	}
	file := uploaded[0].(map[string]any)
	if file["name"] != "test.csv" {
		t.Errorf("Expected filename test.csv, got %s", file["name"])
	}

	// List files
	listResp, err := http.Get(baseURL + "/files")
	if err != nil {
		t.Fatalf("List failed: %v", err)
	}
	defer listResp.Body.Close()

	var listResult map[string]any
	json.NewDecoder(listResp.Body).Decode(&listResult)
	files := listResult["files"].([]any)

	found := false
	for _, f := range files {
		fm := f.(map[string]any)
		if fm["name"] == "test.csv" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("test.csv not found in file list: %v", files)
	}
}

func TestFileUploadThenExecute(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Upload a CSV file (simulating what pool.py does)
	csvContent := []byte("name,age\nAlice,30\nBob,25\n")
	resp, err := uploadFile(baseURL, "data.csv", csvContent)
	if err != nil {
		t.Fatalf("Upload failed: %v", err)
	}
	resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("Upload returned %d", resp.StatusCode)
	}

	// Execute Python code that reads the uploaded file
	result, err := executeCode(baseURL,
		"with open('/mnt/data/data.csv') as f: print(f.read().strip())",
		5,
	)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	// The code should find the file at /mnt/data/data.csv
	// Since we're using a temp dir (not /mnt/data), the path won't match.
	// But the file IS in the working dir — let's test with relative path instead.
	result, err = executeCode(baseURL,
		"import os; files = os.listdir('.'); print(sorted(files))",
		5,
	)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	if result.ExitCode != 0 {
		t.Errorf("Expected exit code 0, got %d (stderr: %s)", result.ExitCode, result.Stderr)
	}

	// Should contain both data.csv and code.py
	if result.Stdout == "" {
		t.Errorf("Expected non-empty stdout listing files")
	}
	// stdout should mention data.csv
	if !bytes.Contains([]byte(result.Stdout), []byte("data.csv")) {
		t.Errorf("Expected data.csv in file listing, got: %s", result.Stdout)
	}
}

func TestFileDownload(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Upload a file
	content := []byte("hello world")
	uploadFile(baseURL, "hello.txt", content)

	// Download it
	resp, err := http.Get(baseURL + "/files/hello.txt")
	if err != nil {
		t.Fatalf("Download failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("Expected 200, got %d", resp.StatusCode)
	}

	body, _ := io.ReadAll(resp.Body)
	if string(body) != "hello world" {
		t.Errorf("Expected 'hello world', got %q", string(body))
	}
}

func TestFileDelete(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Upload a file
	uploadFile(baseURL, "todelete.txt", []byte("bye"))

	// Delete it
	req, _ := http.NewRequest("DELETE", baseURL+"/files/todelete.txt", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("Delete failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Errorf("Expected 200, got %d", resp.StatusCode)
	}

	// Verify it's gone
	getResp, _ := http.Get(baseURL + "/files/todelete.txt")
	if getResp.StatusCode != 404 {
		t.Errorf("Expected 404 after delete, got %d", getResp.StatusCode)
	}
}

func TestPathTraversalBlocked(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Try to download outside working dir
	resp, err := http.Get(baseURL + "/files/../../etc/passwd")
	if err != nil {
		t.Fatalf("Request failed: %v", err)
	}
	defer resp.Body.Close()

	// Go's HTTP router may clean the path (resolving ..) before it reaches the handler,
	// resulting in 404 instead of 403. Either status correctly blocks access.
	if resp.StatusCode != 403 && resp.StatusCode != 404 {
		t.Errorf("Expected 403 or 404 for path traversal, got %d", resp.StatusCode)
	}
}

func TestMultipleFileUpload(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Upload multiple files sequentially (like pool.py does)
	files := map[string]string{
		"file1.txt": "content one",
		"file2.txt": "content two",
		"data.json": `{"key": "value"}`,
	}

	for name, content := range files {
		resp, err := uploadFile(baseURL, name, []byte(content))
		if err != nil {
			t.Fatalf("Upload %s failed: %v", name, err)
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Fatalf("Upload %s returned %d", name, resp.StatusCode)
		}
	}

	// List and verify all files are present
	listResp, _ := http.Get(baseURL + "/files")
	defer listResp.Body.Close()
	var listResult map[string]any
	json.NewDecoder(listResp.Body).Decode(&listResult)
	fileList := listResult["files"].([]any)

	foundNames := make(map[string]bool)
	for _, f := range fileList {
		fm := f.(map[string]any)
		foundNames[fm["name"].(string)] = true
	}

	for name := range files {
		if !foundNames[name] {
			t.Errorf("File %s not found after upload", name)
		}
	}
}

func TestExecuteTimeout(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// This test only works if Python is available (skip in CI without Python)
	result, err := executeCode(baseURL, "import time; time.sleep(10)", 2)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	if result.ExitCode != 124 && result.ExitCode != -1 && result.ExitCode != 1 {
		t.Errorf("Expected timeout exit code (124/-1/1), got %d", result.ExitCode)
	}
}

func TestDotFileUploadRejected(t *testing.T) {
	baseURL := startTestServer(t, "py")

	resp, err := uploadFile(baseURL, ".hidden", []byte("secret"))
	if err != nil {
		t.Fatalf("Upload failed: %v", err)
	}
	defer resp.Body.Close()

	var result map[string]any
	json.NewDecoder(resp.Body).Decode(&result)

	// Dot files should be silently skipped
	uploaded := result["uploaded"]
	if uploaded != nil {
		list := uploaded.([]any)
		if len(list) > 0 {
			t.Errorf("Expected dot file to be rejected, but got: %v", list)
		}
	}
}

// TestFileUploadFieldName tests that the runner accepts the exact multipart
// format that pool.py sends: files={"files": (filename, content)}.
// This is the most likely failure point for file mounting issues.
func TestFileUploadFieldName(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Create multipart exactly like httpx does in Python:
	// files={"files": (filename, content)}
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)

	part, _ := writer.CreateFormFile("files", "upload.txt")
	part.Write([]byte("uploaded content"))
	writer.Close()

	resp, err := http.Post(baseURL+"/files", writer.FormDataContentType(), &buf)
	if err != nil {
		t.Fatalf("Upload failed: %v", err)
	}
	defer resp.Body.Close()

	var result map[string]any
	json.NewDecoder(resp.Body).Decode(&result)

	uploaded := result["uploaded"].([]any)
	if len(uploaded) != 1 {
		t.Fatalf("Expected 1 file uploaded, got %d", len(uploaded))
	}

	// Verify the file is actually on disk
	file := uploaded[0].(map[string]any)
	path := file["path"].(string)
	if _, err := os.Stat(path); err != nil {
		t.Errorf("Uploaded file not found on disk at %s: %v", path, err)
	}

	// Verify content
	data, _ := os.ReadFile(path)
	if string(data) != "uploaded content" {
		t.Errorf("File content mismatch: got %q", string(data))
	}
}

// TestGeneratedFileDetection tests that files created by code execution
// are visible via the /files endpoint (used by orchestrator for file retrieval).
func TestGeneratedFileDetection(t *testing.T) {
	baseURL := startTestServer(t, "py")

	// Execute code that creates an output file
	// Use the working directory (which is the temp dir)
	result, err := executeCode(baseURL,
		"with open('output.txt', 'w') as f: f.write('generated')\nprint('done')",
		5,
	)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}

	// Skip if Python isn't available (CI without Python runtime)
	if result.ExitCode != 0 {
		t.Skipf("Python not available: %s", result.Stderr)
	}

	// List files — should include output.txt
	listResp, _ := http.Get(baseURL + "/files")
	defer listResp.Body.Close()
	var listResult map[string]any
	json.NewDecoder(listResp.Body).Decode(&listResult)

	found := false
	for _, f := range listResult["files"].([]any) {
		fm := f.(map[string]any)
		if fm["name"] == "output.txt" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("output.txt not found in file listing after code execution")
	}

	// Download the generated file
	resp, _ := http.Get(baseURL + "/files/output.txt")
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if string(body) != "generated" {
		t.Errorf("Expected 'generated', got %q", string(body))
	}
}

// Unused imports guard
var _ = filepath.Join
var _ = time.Now
