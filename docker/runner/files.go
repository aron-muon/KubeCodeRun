package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
)

// FileInfo represents a file in the working directory.
type FileInfo struct {
	Name    string  `json:"name"`
	Path    string  `json:"path"`
	Size    int64   `json:"size"`
	ModTime int64   `json:"mod_time"`
	MimeType *string `json:"mime_type,omitempty"`
}

// FileHandler manages file operations in the working directory.
type FileHandler struct {
	workingDir string
}

// NewFileHandler creates a new FileHandler.
func NewFileHandler(workingDir string) *FileHandler {
	return &FileHandler{workingDir: workingDir}
}

// validatePath ensures the resolved path is within the working directory.
func (h *FileHandler) validatePath(reqPath string) (string, error) {
	resolved := filepath.Join(h.workingDir, reqPath)
	resolved, err := filepath.Abs(resolved)
	if err != nil {
		return "", err
	}

	absWD, _ := filepath.Abs(h.workingDir)

	rel, err := filepath.Rel(absWD, resolved)
	if err != nil || rel == ".." || len(rel) > 1 && rel[:2] == ".." {
		return "", os.ErrPermission
	}

	return resolved, nil
}

// HandleUpload processes POST /files (multipart file upload).
func (h *FileHandler) HandleUpload(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid multipart form"})
		return
	}

	var uploaded []FileInfo

	for _, fileHeaders := range r.MultipartForm.File {
		for _, fh := range fileHeaders {
			safeName := filepath.Base(fh.Filename)
			if safeName == "" || safeName[0] == '.' {
				continue
			}

			src, err := fh.Open()
			if err != nil {
				continue
			}

			destPath := filepath.Join(h.workingDir, safeName)
			dst, err := os.Create(destPath)
			if err != nil {
				src.Close()
				continue
			}

			n, _ := io.Copy(dst, src)
			src.Close()
			dst.Close()

			// Get mod_time of the newly created file
			fi, _ := os.Stat(destPath)
			var modTime int64
			if fi != nil {
				modTime = fi.ModTime().Unix()
			}

			uploaded = append(uploaded, FileInfo{
				Name:    safeName,
				Path:    destPath,
				Size:    n,
				ModTime: modTime,
			})
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{"uploaded": uploaded})
}

// HandleList processes GET /files (list working directory).
func (h *FileHandler) HandleList(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(h.workingDir)
	if err != nil {
		http.Error(w, `{"detail":"Working directory not found"}`, http.StatusNotFound)
		return
	}

	files := make([]FileInfo, 0, len(entries))
	for _, e := range entries {
		info, err := e.Info()
		if err != nil {
			continue
		}
		size := info.Size()
		if e.IsDir() {
			size = 0
		}
		files = append(files, FileInfo{
			Name:    e.Name(),
			Path:    e.Name(),
			Size:    size,
			ModTime: info.ModTime().Unix(),
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{"files": files})
}

// HandleDownload processes GET /files/{path...}.
func (h *FileHandler) HandleDownload(w http.ResponseWriter, r *http.Request) {
	reqPath := r.PathValue("path")
	resolved, err := h.validatePath(reqPath)
	if err != nil {
		http.Error(w, `{"detail":"Access denied"}`, http.StatusForbidden)
		return
	}

	info, err := os.Stat(resolved)
	if err != nil {
		http.Error(w, `{"detail":"File not found"}`, http.StatusNotFound)
		return
	}

	if info.IsDir() {
		entries, _ := os.ReadDir(resolved)
		absWD, _ := filepath.Abs(h.workingDir)
		files := make([]FileInfo, 0, len(entries))
		for _, e := range entries {
			ei, err := e.Info()
			if err != nil {
				continue
			}
			size := ei.Size()
			if e.IsDir() {
				size = 0
			}
			rel, _ := filepath.Rel(absWD, filepath.Join(resolved, e.Name()))
			files = append(files, FileInfo{
				Name: e.Name(),
				Path: rel,
				Size: size,
			})
		}
		writeJSON(w, http.StatusOK, map[string]any{"files": files})
		return
	}

	http.ServeFile(w, r, resolved)
}

// HandleDelete processes DELETE /files/{path...}.
func (h *FileHandler) HandleDelete(w http.ResponseWriter, r *http.Request) {
	reqPath := r.PathValue("path")
	resolved, err := h.validatePath(reqPath)
	if err != nil {
		http.Error(w, `{"detail":"Access denied"}`, http.StatusForbidden)
		return
	}

	absWD, _ := filepath.Abs(h.workingDir)
	if resolved == absWD {
		http.Error(w, `{"detail":"Cannot delete working directory"}`, http.StatusForbidden)
		return
	}

	info, err := os.Stat(resolved)
	if err != nil {
		http.Error(w, `{"detail":"File not found"}`, http.StatusNotFound)
		return
	}

	if info.IsDir() {
		os.RemoveAll(resolved)
	} else {
		os.Remove(resolved)
	}

	writeJSON(w, http.StatusOK, map[string]string{"deleted": reqPath})
}

// writeJSON encodes a value as JSON and writes it to the response.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
