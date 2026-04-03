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
