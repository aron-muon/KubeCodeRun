#!/usr/bin/env bash
# shellcheck disable=SC2153  # Variables are intentionally sourced from result files
# Build all KubeCodeRun Docker images in parallel
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKER_DIR="$PROJECT_ROOT/docker"

# Defaults
TAG="latest"
PREFIX="kcr"  # Local image prefix to avoid conflicts with official images
REGISTRY=""   # When set, overrides PREFIX (for pushing to registries)
PUSH=false
NO_CACHE=""
SEQUENTIAL=false
SINGLE_IMAGE=""

# Temp directory for build results
RESULTS_DIR=""

# Language Dockerfiles (maps to image names)
# Format: dockerfile_path:image_name:context_dir
# context_dir is relative to PROJECT_ROOT (empty = docker/)
LANGUAGE_IMAGES=(
    "python.Dockerfile:python:"
    "nodejs.Dockerfile:nodejs:"
    "go.Dockerfile:go:"
    "java.Dockerfile:java:"
    "c-cpp.Dockerfile:c-cpp:"
    "rust.Dockerfile:rust:"
    "php.Dockerfile:php:"
    "r.Dockerfile:r:"  # R is really slow to build
    "fortran.Dockerfile:fortran:"
    "d.Dockerfile:d:"
)

# Infrastructure images with custom contexts
# sidecar: context is docker/sidecar/ (contains requirements.txt, main.py)
# api: context is repo root (needs uv.lock, pyproject.toml, src/)
#
# Format: dockerfile_path:image_name:context_dir:docker_target (target is optional)
# The sidecar Dockerfile has two targets:
#   sidecar-agent   → kubecoderun-sidecar-agent (default, no nsenter)
#   sidecar-nsenter → kubecoderun-sidecar-nsenter (legacy, with nsenter+setcap)
INFRA_IMAGES=(
    "sidecar/Dockerfile:sidecar-agent:docker/sidecar:sidecar-agent"
    "sidecar/Dockerfile:sidecar-nsenter:docker/sidecar:sidecar-nsenter"
    "api/Dockerfile:api:."
)

usage() {
    cat <<'EOF'
Usage: ./scripts/build-images.sh [OPTIONS] [IMAGE]

Build Docker images for the KubeCodeRun platform.

When called without arguments, builds ALL images (language runtimes,
sidecar variants, and API) in parallel. Specify IMAGE to build a
single image with full terminal output (useful for debugging).

Arguments:
  IMAGE                   Name of a single image to build (see --list)

Options:
  -t, --tag TAG           Image tag (default: latest)
  -r, --registry REG      Registry prefix (e.g., ghcr.io/org/kubecoderun)
  -p, --push              Push images to the registry after building
      --no-cache          Build without Docker layer cache
      --sequential        Build images one at a time instead of in parallel
  -l, --list              List all available image names and exit
  -h, -?, --help          Show this help message and exit

Environment Variables:
  DHI_USERNAME            Username for dhi.io registry authentication
  DHI_PASSWORD            Password for dhi.io registry authentication

Examples:
  # Build all images in parallel (default)
  ./scripts/build-images.sh

  # Build only the Go language image with full output
  ./scripts/build-images.sh go

  # Build the agent-mode sidecar without cache
  ./scripts/build-images.sh --no-cache sidecar-agent

  # Build and push all images to a private registry
  ./scripts/build-images.sh -r ghcr.io/myorg/kubecoderun -t v2.0.0 --push

  # Build the nsenter-mode sidecar
  ./scripts/build-images.sh sidecar-nsenter

  # List all available image names
  ./scripts/build-images.sh --list
EOF
}

list_images() {
    local all_images=("${LANGUAGE_IMAGES[@]}" "${INFRA_IMAGES[@]}")
    echo "Available images:"
    echo ""
    printf "  %-20s %-35s %-15s\n" "NAME" "DOCKERFILE" "TARGET"
    printf "  %-20s %-35s %-15s\n" "────────────────────" "───────────────────────────────────" "───────────────"
    for entry in "${all_images[@]}"; do
        IFS=':' read -r dockerfile image_name context_dir docker_target <<< "$entry"
        printf "  %-20s %-35s %-15s\n" "$image_name" "$dockerfile" "${docker_target:--}"
    done
    echo ""
    echo "Build a single image:  ./scripts/build-images.sh <NAME>"
    echo "Build all images:      ./scripts/build-images.sh"
}

dhi_login() {
    local docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"

    # Check if already logged in to dhi.io
    if [[ -f "$docker_config" ]] && grep -q '"dhi.io"' "$docker_config" 2>/dev/null; then
        echo "Already logged in to dhi.io"
        return 0
    fi

    echo "Not logged in to dhi.io, logging in..."

    # Try to login with credentials if provided, otherwise interactive
    if [[ -n "${DHI_USERNAME:-}" && -n "${DHI_PASSWORD:-}" ]]; then
        echo "$DHI_PASSWORD" | docker login dhi.io -u "$DHI_USERNAME" --password-stdin
    else
        docker login dhi.io
    fi
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -t|--tag)
                TAG="$2"
                shift 2
                ;;
            -r|--registry)
                REGISTRY="$2"
                shift 2
                ;;
            -p|--push)
                PUSH=true
                shift
                ;;
            --no-cache)
                NO_CACHE="--no-cache"
                shift
                ;;
            --sequential)
                SEQUENTIAL=true
                shift
                ;;
            -h|-\?|--help)
                usage
                exit 0
                ;;
            -l|--list)
                list_images
                exit 0
                ;;
            -*)
                echo "Error: Unknown option '$1'"
                echo ""
                echo "Run './scripts/build-images.sh --help' for usage information."
                exit 1
                ;;
            *)
                # Positional argument: single image name
                SINGLE_IMAGE="$1"
                shift
                ;;
        esac
    done
}

get_full_image_name() {
    local image_name="$1"
    if [[ -n "$REGISTRY" ]]; then
        # Registry mode: ghcr.io/user/kubecoderun-python:tag
        echo "${REGISTRY}-${image_name}:${TAG}"
    elif [[ -n "$PREFIX" ]]; then
        # Local mode with prefix: kcr-python:tag
        echo "${PREFIX}-${image_name}:${TAG}"
    else
        # No prefix (not recommended): python:tag
        echo "${image_name}:${TAG}"
    fi
}

format_duration() {
    local seconds="$1"
    if (( seconds >= 60 )); then
        printf "%dm %ds" $((seconds / 60)) $((seconds % 60))
    else
        printf "%ds" "$seconds"
    fi
}

format_size() {
    local bytes="$1"
    if (( bytes >= 1073741824 )); then
        printf "%.2f GB" "$(echo "scale=2; $bytes / 1073741824" | bc)"
    elif (( bytes >= 1048576 )); then
        printf "%.1f MB" "$(echo "scale=1; $bytes / 1048576" | bc)"
    else
        printf "%.1f KB" "$(echo "scale=1; $bytes / 1024" | bc)"
    fi
}

build_image() {
    local dockerfile="$1"
    local image_name="$2"
    local result_file="$3"
    local context_dir="$4"
    local docker_target="$5"
    local full_name
    full_name=$(get_full_image_name "$image_name")

    # Resolve context directory
    local context_path
    if [[ -z "$context_dir" ]]; then
        context_path="$DOCKER_DIR"
    else
        context_path="$PROJECT_ROOT/$context_dir"
    fi

    local start_time
    start_time=$(date +%s)

    local build_output
    local exit_code=0

    # Build with optional --target
    local target_flag=""
    if [[ -n "$docker_target" ]]; then
        target_flag="--target $docker_target"
    fi

    # shellcheck disable=SC2086
    build_output=$(docker build \
        $NO_CACHE \
        $target_flag \
        --build-arg VERSION="$TAG" \
        -t "$full_name" \
        -f "$DOCKER_DIR/$dockerfile" \
        "$context_path" 2>&1) || exit_code=$?

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    local size_bytes=0
    local size_str="N/A"
    if [[ $exit_code -eq 0 ]]; then
        size_bytes=$(docker image inspect "$full_name" --format='{{.Size}}' 2>/dev/null || echo "0")
        size_str=$(format_size "$size_bytes")

        if [[ "$PUSH" == true ]]; then
            docker push "$full_name" 2>&1 || exit_code=$?
        fi
    fi

    # Write result to file (quote values that may contain spaces)
    {
        echo "IMAGE='$image_name'"
        echo "FULL_NAME='$full_name'"
        echo "EXIT_CODE='$exit_code'"
        echo "DURATION='$duration'"
        echo "SIZE_BYTES='$size_bytes'"
        echo "SIZE_STR='$size_str'"
    } > "$result_file"

    # Also write build output for debugging
    echo "$build_output" > "${result_file}.log"

    return $exit_code
}

build_image_wrapper() {
    local dockerfile="$1"
    local image_name="$2"
    local context_dir="$3"
    local docker_target="$4"
    local result_file="$RESULTS_DIR/${image_name}.result"

    if build_image "$dockerfile" "$image_name" "$result_file" "$context_dir" "$docker_target"; then
        echo "Completed: $image_name"
    else
        echo "Failed: $image_name"
    fi
}

# Build a single image with output directly to terminal (for debugging)
build_single_image() {
    local target_image="$1"
    local all_images=("${LANGUAGE_IMAGES[@]}" "${INFRA_IMAGES[@]}")
    local found=false

    for entry in "${all_images[@]}"; do
        IFS=':' read -r dockerfile image_name context_dir docker_target <<< "$entry"

        if [[ "$image_name" == "$target_image" ]]; then
            found=true

            if [[ ! -f "$DOCKER_DIR/$dockerfile" ]]; then
                echo "Error: Dockerfile not found: $DOCKER_DIR/$dockerfile"
                exit 1
            fi

            local full_name
            full_name=$(get_full_image_name "$image_name")

            # Resolve context directory
            local context_path
            if [[ -z "$context_dir" ]]; then
                context_path="$DOCKER_DIR"
            else
                context_path="$PROJECT_ROOT/$context_dir"
            fi

            echo "Building $image_name -> $full_name"
            echo "  Dockerfile: $DOCKER_DIR/$dockerfile"
            echo "  Context:    $context_path"
            if [[ -n "$docker_target" ]]; then
                echo "  Target:     $docker_target"
            fi
            echo ""

            # Build with output directly to terminal (optional --target)
            local target_flag=""
            if [[ -n "$docker_target" ]]; then
                target_flag="--target $docker_target"
            fi

            # shellcheck disable=SC2086
            docker build \
                $NO_CACHE \
                $target_flag \
                --build-arg VERSION="$TAG" \
                -t "$full_name" \
                -f "$DOCKER_DIR/$dockerfile" \
                "$context_path"

            local size_bytes
            size_bytes=$(docker image inspect "$full_name" --format='{{.Size}}' 2>/dev/null || echo "0")
            echo ""
            echo "Built: $full_name ($(format_size "$size_bytes"))"

            if [[ "$PUSH" == true ]]; then
                echo "Pushing $full_name..."
                docker push "$full_name"
            fi

            return 0
        fi
    done

    if [[ "$found" == false ]]; then
        echo "Error: Unknown image '$target_image'"
        echo ""
        echo "Available images:"
        for entry in "${all_images[@]}"; do
            IFS=':' read -r _ image_name _ _ <<< "$entry"
            echo "  - $image_name"
        done
        exit 1
    fi
}

main() {
    parse_args "$@"

    # Log in to DHI registry if credentials are provided
    dhi_login

    # Single image mode: build one image with full output
    if [[ -n "$SINGLE_IMAGE" ]]; then
        build_single_image "$SINGLE_IMAGE"
        exit 0
    fi

    # Create temp directory for results
    RESULTS_DIR=$(mktemp -d)
    trap 'rm -rf "$RESULTS_DIR"' EXIT

    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║           KubeCodeRun Docker Image Builder               ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    echo "║  Tag:      ${TAG}"
    if [[ -n "$REGISTRY" ]]; then
        echo "║  Registry: ${REGISTRY}"
    fi
    echo "║  Push:     ${PUSH}"
    echo "║  Parallel: $( [[ "$SEQUENTIAL" == true ]] && echo "No" || echo "Yes" )"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""

    local all_images=("${LANGUAGE_IMAGES[@]}" "${INFRA_IMAGES[@]}")
    local pids=()

    local overall_start
    overall_start=$(date +%s)

    echo "Building ${#all_images[@]} images..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    for entry in "${all_images[@]}"; do
        # Parse entry: dockerfile:image_name:context_dir:docker_target (target is optional)
        IFS=':' read -r dockerfile image_name context_dir docker_target <<< "$entry"

        if [[ ! -f "$DOCKER_DIR/$dockerfile" ]]; then
            echo "Warning: Dockerfile not found: $dockerfile"
            continue
        fi

        echo "Starting: $image_name"
        if [[ "$SEQUENTIAL" == true ]]; then
            build_image_wrapper "$dockerfile" "$image_name" "$context_dir" "$docker_target"
        else
            build_image_wrapper "$dockerfile" "$image_name" "$context_dir" "$docker_target" &
            pids+=($!)
        fi
    done

    # Wait for all parallel builds to complete
    if [[ "$SEQUENTIAL" != true ]]; then
        echo ""
        echo "Waiting for builds to complete..."
        for pid in "${pids[@]}"; do
            wait "$pid" 2>/dev/null || true
        done
    fi

    local overall_end
    overall_end=$(date +%s)
    local overall_duration=$((overall_end - overall_start))

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Collect and display results
    local succeeded=()
    local failed=()
    local total_size=0

    # Prepare results table
    printf "%-15s %-10s %-12s %-8s\n" "IMAGE" "STATUS" "SIZE" "TIME"
    printf "%-15s %-10s %-12s %-8s\n" "─────────────" "────────" "──────────" "──────"

    for entry in "${all_images[@]}"; do
        IFS=':' read -r _ image_name _ _ <<< "$entry"
        result_file="$RESULTS_DIR/${image_name}.result"

        if [[ -f "$result_file" ]]; then
            # shellcheck source=/dev/null
            source "$result_file"

            local status
            if [[ "$EXIT_CODE" -eq 0 ]]; then
                status="OK"
                succeeded+=("$image_name")
                total_size=$((total_size + SIZE_BYTES))
            else
                status="FAILED"
                failed+=("$image_name")
            fi

            printf "%-15s %-10s %-12s %-8s\n" \
                "$IMAGE" \
                "$status" \
                "$SIZE_STR" \
                "$(format_duration "$DURATION")"
        else
            printf "%-15s %-10s %-12s %-8s\n" "$image_name" "SKIPPED" "N/A" "N/A"
        fi
    done

    echo ""

    # Summary
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║                     Build Summary                        ║"
    echo "╠══════════════════════════════════════════════════════════╣"
    printf "║  Succeeded:    %-4s                                      ║\n" "${#succeeded[@]}"
    printf "║  Failed:       %-4s                                      ║\n" "${#failed[@]}"
    printf "║  Total Size:   %-12s                              ║\n" "$(format_size "$total_size")"
    printf "║  Total Time:   %-12s                              ║\n" "$(format_duration "$overall_duration")"
    echo "╚══════════════════════════════════════════════════════════╝"

    if [[ ${#failed[@]} -gt 0 ]]; then
        echo ""
        echo "Failed images:"
        for img in "${failed[@]}"; do
            echo ""
            echo "━━━ $img ━━━"
            if [[ -f "$RESULTS_DIR/${img}.result.log" ]]; then
                # Show last 30 lines of build output
                tail -n 30 "$RESULTS_DIR/${img}.result.log"
            else
                echo "  (no log available)"
            fi
        done
        echo ""
        echo "Tip: Run './scripts/build-images.sh <image>' to rebuild with full output"
        exit 1
    fi

    echo ""
    echo "All images built successfully!"
}

main "$@"
