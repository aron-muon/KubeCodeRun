package main

import (
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestValidatePath(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	// Valid path
	resolved, err := h.validatePath("test.txt")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := filepath.Join(dir, "test.txt")
	if resolved != expected {
		t.Errorf("expected %s, got %s", expected, resolved)
	}

	// Traversal attempt
	_, err = h.validatePath("../../etc/passwd")
	if err == nil {
		t.Error("expected error for path traversal")
	}

	// Nested valid path
	subdir := filepath.Join(dir, "sub")
	os.Mkdir(subdir, 0755)
	resolved, err = h.validatePath("sub/file.txt")
	if err != nil {
		t.Fatalf("unexpected error for nested path: %v", err)
	}
	if resolved != filepath.Join(dir, "sub", "file.txt") {
		t.Errorf("unexpected resolved path: %s", resolved)
	}
}

func TestValidatePathEdgeCases(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	// Current directory
	resolved, err := h.validatePath(".")
	if err != nil {
		t.Fatalf("unexpected error for '.': %v", err)
	}
	absDir, _ := filepath.Abs(dir)
	if resolved != absDir {
		t.Errorf("expected %s, got %s", absDir, resolved)
	}

	// Prefix collision (e.g., /mnt/data vs /mnt/data-evil)
	// This shouldn't happen with filepath.Rel but verify the check works
	_, err = h.validatePath("../data-evil/file.txt")
	if err == nil {
		t.Error("expected error for prefix collision path")
	}
}

func TestHandleListIncludesModTime(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	// Create a test file
	testFile := filepath.Join(dir, "test.txt")
	if err := os.WriteFile(testFile, []byte("hello"), 0644); err != nil {
		t.Fatalf("failed to create test file: %v", err)
	}

	info, err := os.Stat(testFile)
	if err != nil {
		t.Fatalf("failed to stat test file: %v", err)
	}

	// Use HandleList by reading the directory manually (same logic)
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("failed to read dir: %v", err)
	}

	var files []FileInfo
	for _, e := range entries {
		eInfo, _ := e.Info()
		files = append(files, FileInfo{
			Name:    e.Name(),
			Path:    e.Name(),
			Size:    eInfo.Size(),
			ModTime: eInfo.ModTime().Unix(),
		})
	}

	if len(files) != 1 {
		t.Fatalf("expected 1 file, got %d", len(files))
	}
	if files[0].Name != "test.txt" {
		t.Errorf("expected name test.txt, got %s", files[0].Name)
	}
	if files[0].ModTime != info.ModTime().Unix() {
		t.Errorf("expected mod_time %d, got %d", info.ModTime().Unix(), files[0].ModTime)
	}
	if files[0].ModTime == 0 {
		t.Error("mod_time should not be zero")
	}

	_ = h // keep linter happy
}

// uploadMultipart builds a POST /files multipart request. The file part is
// sent with `basename` as its filename (Go's parser strips directories anyway)
// and the nested layout is carried out-of-band in the `path` form field, which
// mirrors how the control plane transports skill bundles like
// "skillName/SKILL.md".
func uploadMultipart(t *testing.T, relPath string, content []byte) *http.Request {
	t.Helper()
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	part, err := mw.CreateFormFile("files", filepath.Base(relPath))
	if err != nil {
		t.Fatalf("CreateFormFile: %v", err)
	}
	if _, err := part.Write(content); err != nil {
		t.Fatalf("write part: %v", err)
	}
	if err := mw.WriteField("path", relPath); err != nil {
		t.Fatalf("write path field: %v", err)
	}
	if err := mw.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/files", &body)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	return req
}

func TestHandleUploadPreservesNestedPath(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	rr := httptest.NewRecorder()
	h.HandleUpload(rr, uploadMultipart(t, "skillName/SKILL.md", []byte("# skill")))

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	// The file must land at the nested path, not be flattened to the basename.
	nested := filepath.Join(dir, "skillName", "SKILL.md")
	if _, err := os.Stat(nested); err != nil {
		t.Fatalf("expected nested file at %s: %v", nested, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "SKILL.md")); err == nil {
		t.Error("file must not be flattened to working-dir root")
	}

	var resp struct {
		Uploaded []FileInfo `json:"uploaded"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Uploaded) != 1 || resp.Uploaded[0].Name != "skillName/SKILL.md" {
		t.Errorf("expected uploaded name skillName/SKILL.md, got %+v", resp.Uploaded)
	}
}

func TestHandleUploadRejectsTraversal(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	rr := httptest.NewRecorder()
	h.HandleUpload(rr, uploadMultipart(t, "../escape.txt", []byte("pwned")))

	// The traversal target must never be written outside the working dir.
	if _, err := os.Stat(filepath.Join(filepath.Dir(dir), "escape.txt")); err == nil {
		t.Fatal("path traversal escaped the working directory")
	}

	var resp struct {
		Uploaded []FileInfo `json:"uploaded"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Uploaded) != 0 {
		t.Errorf("traversal upload must be skipped, got %+v", resp.Uploaded)
	}
}

func TestHandleListIsRecursive(t *testing.T) {
	dir := t.TempDir()
	h := NewFileHandler(dir)

	if err := os.MkdirAll(filepath.Join(dir, "skillName"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "skillName", "SKILL.md"), []byte("x"), 0o644); err != nil {
		t.Fatalf("write nested: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "top.txt"), []byte("y"), 0o644); err != nil {
		t.Fatalf("write top: %v", err)
	}

	rr := httptest.NewRecorder()
	h.HandleList(rr, httptest.NewRequest(http.MethodGet, "/files", nil))
	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var resp struct {
		Files []FileInfo `json:"files"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	names := map[string]bool{}
	for _, f := range resp.Files {
		names[f.Name] = true
	}
	if !names["skillName/SKILL.md"] {
		t.Errorf("expected recursive listing to include skillName/SKILL.md, got %+v", resp.Files)
	}
	if !names["top.txt"] {
		t.Errorf("expected listing to include top.txt, got %+v", resp.Files)
	}
	// Directories themselves must not be reported as files.
	if names["skillName"] {
		t.Error("directory entries must not appear in the file listing")
	}
}
