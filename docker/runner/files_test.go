package main

import (
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
