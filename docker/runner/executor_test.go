package main

import (
	"testing"
)

func TestLanguageSpecsExist(t *testing.T) {
	expected := []string{
		"py", "python", "js", "javascript", "ts", "typescript",
		"go", "java", "c", "cpp", "php", "rs", "rust",
		"r", "f90", "fortran", "d", "dlang",
	}
	for _, lang := range expected {
		if _, ok := languages[lang]; !ok {
			t.Errorf("missing language spec for %q", lang)
		}
	}
}

func TestLanguageAliasesMatch(t *testing.T) {
	aliases := map[string]string{
		"python":     "py",
		"javascript": "js",
		"typescript": "ts",
		"rust":       "rs",
		"fortran":    "f90",
		"dlang":      "d",
	}
	for long, short := range aliases {
		l := languages[long]
		s := languages[short]
		if l.File != s.File || l.Run != s.Run {
			t.Errorf("alias mismatch: %s != %s", long, short)
		}
	}
}

func TestTruncate(t *testing.T) {
	if truncate("hello", 3) != "hel" {
		t.Error("truncate should cut to max length")
	}
	if truncate("hi", 10) != "hi" {
		t.Error("truncate should not modify short strings")
	}
}

func TestAppendEnv(t *testing.T) {
	env := []string{"FOO=bar", "BAZ=qux"}

	// Override existing
	env = appendEnv(env, "FOO", "new")
	if env[0] != "FOO=new" {
		t.Errorf("expected FOO=new, got %s", env[0])
	}

	// Append new
	env = appendEnv(env, "NEW", "val")
	if env[len(env)-1] != "NEW=val" {
		t.Errorf("expected NEW=val appended")
	}
}
