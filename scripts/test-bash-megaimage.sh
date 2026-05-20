#!/usr/bin/env bash
# Validate that every supported language's hello-world compiles and runs
# inside the unified bash image.
#
# For each of the 13 supported languages we:
#   1. Write the language's hello-world source into a temp dir.
#   2. Run the exact compile-and-run command from docker/runner/executor.go
#      (so we exercise the same code path the runner binary would, just
#      without going through HTTP).
#   3. Assert the stdout contains "Hello, World!".
#
# Exit non-zero if any language fails. Failures print the captured output
# so the operator can see *why* (missing package, broken alias, etc.).

set -u

IMAGE="${IMAGE:-kubecoderun-bash-all:test}"
PLATFORM="${PLATFORM:-linux/amd64}"

# Each entry: name|source_filename|hello-world-program|argv-after-sh-c
# argv is the SHELL command we run inside the container. Mirrors the
# `Args` field of each LangSpec in docker/runner/executor.go. {file}
# resolves to the absolute path of source_filename inside /work, and
# {wd} resolves to /work.
LANGUAGES=(
    "bash|code.sh|echo 'Hello, World!'|bash {file}"
    "python|code.py|print('Hello, World!')|python {file}"
    "javascript|code.js|console.log('Hello, World!')|node {file}"
    "typescript|code.ts|const m: string = 'Hello, World!'; console.log(m);|node /opt/scripts/ts-runner.js {file}"
    "go|main.go|package main\nimport \"fmt\"\nfunc main() { fmt.Println(\"Hello, World!\") }|go run {file}"
    "java|Code.java|public class Code { public static void main(String[] a) { System.out.println(\"Hello, World!\"); } }|javac {file} && java -cp {wd} Code"
    "c|code.c|#include <stdio.h>\nint main() { printf(\"Hello, World!\\\\n\"); return 0; }|gcc {file} -o /tmp/code && /tmp/code"
    "cpp|code.cpp|#include <iostream>\nint main() { std::cout << \"Hello, World!\" << std::endl; return 0; }|g++ {file} -o /tmp/code && /tmp/code"
    "php|code.php|<?php echo \"Hello, World!\\\\n\"; ?>|php {file}"
    "rust|main.rs|fn main() { println!(\"Hello, World!\"); }|rustc {file} -o /tmp/main && /tmp/main"
    "r|code.r|cat('Hello, World!\\\\n')|Rscript {file}"
    "fortran|code.f90|program hello\n  print *, 'Hello, World!'\nend program hello|gfortran {file} -o /tmp/code && /tmp/code"
    "d|code.d|import std.stdio; void main() { writeln(\"Hello, World!\"); }|ldc2 {file} -of=/tmp/code && /tmp/code"
)

EXPECTED="Hello, World!"
PASS=0
FAIL=0
FAILED_LANGS=()

# Persistent tmpdir per run so all artefacts are isolated and easy to inspect
# on failure.
TMPROOT="$(mktemp -d -t kubecoderun-bash-test.XXXXXX)"
trap 'rm -rf "$TMPROOT"' EXIT

echo "Image: $IMAGE  (platform $PLATFORM)"
echo "Tmp:   $TMPROOT"
echo

# Right-pad printf for tidy columns.
pad() { printf '%-12s' "$1"; }

for entry in "${LANGUAGES[@]}"; do
    IFS='|' read -r lang fname program shell_cmd <<<"$entry"
    workdir="$TMPROOT/$lang"
    mkdir -p "$workdir"

    # `\n` and `\\` survive a round-trip via printf %b — keeps the heredoc
    # readable in the LANGUAGES table above.
    printf '%b\n' "$program" > "$workdir/$fname"

    # Substitute the placeholders the runner uses: {file} → absolute path
    # inside the container, {wd} → working dir.
    cmd="${shell_cmd//\{file\}/"/work/$fname"}"
    cmd="${cmd//\{wd\}/"/work"}"

    # Compile & run inside the container. We mount /tmp as a tmpfs-on-tmpfs
    # mount inside the container so compilers can write to /tmp without
    # touching the host. (The image already has HOME=/tmp via ENTRYPOINT,
    # but we override the entrypoint here to drive raw shell commands.)
    output=$(docker run --rm \
        --platform "$PLATFORM" \
        --user 65532 \
        --entrypoint /bin/sh \
        -e HOME=/tmp \
        -e GOCACHE=/tmp/go-cache \
        -e GOPATH=/tmp/go \
        -e CARGO_HOME=/tmp/cargo \
        -e RUSTUP_HOME=/tmp/rustup \
        -e JAVA_TOOL_OPTIONS=-Duser.home=/tmp \
        -v "$workdir":/work \
        "$IMAGE" \
        -c "$cmd" 2>&1)
    rc=$?

    if [[ $rc -eq 0 && "$output" == *"$EXPECTED"* ]]; then
        echo "✓ $(pad "$lang") ok"
        PASS=$((PASS + 1))
    else
        echo "✗ $(pad "$lang") FAILED (rc=$rc)"
        echo "  cmd:    $cmd"
        echo "  output: ${output:0:600}"
        echo
        FAIL=$((FAIL + 1))
        FAILED_LANGS+=("$lang")
    fi
done

echo
echo "─────────────────────────────────────────"
echo "  Passed: $PASS / $((PASS + FAIL))"
if [[ $FAIL -gt 0 ]]; then
    echo "  Failed: ${FAILED_LANGS[*]}"
    exit 1
fi
exit 0
