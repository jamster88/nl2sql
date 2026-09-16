#!/usr/bin/env bash
# Shared helpers for the RAG pipeline scripts. Sourced, not executed.

RAG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$RAG_DIR/.." && pwd)"

step() { printf '\n==> %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1" >&2; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

require_docker() {
    command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
    docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker and retry."
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."
}

compose() { (cd "$RAG_DIR" && docker compose "$@"); }

wait_healthy() {
    local container="$1" tries="${2:-60}" status=""
    info "waiting for $container to become healthy..."
    for _ in $(seq 1 "$tries"); do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && return 0
        sleep 2
    done
    die "$container did not become healthy (last status: ${status:-unknown}). Check: docker logs $container"
}

# Python runner: prefers the repo venv, falls back to python3.
py() {
    local venv="$REPO_DIR/.venv/bin/python"
    if [[ -x "$venv" ]]; then
        (cd "$RAG_DIR" && "$venv" "$@")
    else
        (cd "$RAG_DIR" && python3 "$@")
    fi
}
