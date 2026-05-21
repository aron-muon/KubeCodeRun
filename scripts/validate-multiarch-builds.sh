#!/usr/bin/env bash
# Validate multi-arch Docker builds for modified Dockerfiles.
# Uses the multiarch-builder (BuildKit) — no push, no local load.
# Cleans up any locally loaded test images after validation.
#
# Usage: ./scripts/validate-multiarch-builds.sh [branch]
#   branch: feat-shell-languages | feat-csharp-support | chore-dockerfiles-upgrade
#           (defaults to current branch)

set -euo pipefail

BUILDER="multiarch-builder"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
TARGET_BRANCH="${1:-$CURRENT_BRANCH}"

# Colours
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ PASS${NC}  $*"; }
fail() { echo -e "${RED}✗ FAIL${NC}  $*"; FAILED+=("$*"); }
info() { echo -e "${YELLOW}→${NC} $*"; }

FAILED=()

build_test() {
  local dockerfile="$1"
  local platform="$2"
  local label="$3"

  info "Building $label ($platform) …"
  if docker buildx build \
      --builder "$BUILDER" \
      --platform "$platform" \
      --file "$REPO_ROOT/docker/${dockerfile}" \
      --output "type=image,push=false" \
      "$REPO_ROOT/docker" 2>&1; then
    pass "$label ($platform)"
  else
    fail "$label ($platform)"
  fi
}

echo "========================================"
echo " KubeCodeRun multi-arch build validator"
echo " Branch: $TARGET_BRANCH"
echo "========================================"
echo

# ── feat-shell-languages ────────────────────────────────────────────────────
if [[ "$TARGET_BRANCH" == "feat-shell-languages" ]]; then
  info "Checking out $TARGET_BRANCH …"
  git -C "$REPO_ROOT" checkout "$TARGET_BRANCH"

  build_test "shell.Dockerfile" "linux/amd64"  "shell"
  build_test "shell.Dockerfile" "linux/arm64"  "shell"
fi

# ── feat-csharp-support ─────────────────────────────────────────────────────
if [[ "$TARGET_BRANCH" == "feat-csharp-support" ]]; then
  info "Checking out $TARGET_BRANCH …"
  git -C "$REPO_ROOT" checkout "$TARGET_BRANCH"

  build_test "csharp.Dockerfile" "linux/amd64" "csharp"
  build_test "csharp.Dockerfile" "linux/arm64" "csharp"
fi

# ── chore-dockerfiles-upgrade ───────────────────────────────────────────────
if [[ "$TARGET_BRANCH" == "chore-dockerfiles-upgrade" ]]; then
  info "Checking out $TARGET_BRANCH …"
  git -C "$REPO_ROOT" checkout "$TARGET_BRANCH"

  build_test "nodejs.Dockerfile" "linux/amd64" "nodejs"
  build_test "php.Dockerfile"    "linux/amd64" "php"
  build_test "rust.Dockerfile"   "linux/amd64" "rust"
fi

# ── summary ─────────────────────────────────────────────────────────────────
echo
echo "========================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo -e "${GREEN}All builds passed.${NC}"
else
  echo -e "${RED}${#FAILED[@]} build(s) failed:${NC}"
  for f in "${FAILED[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
