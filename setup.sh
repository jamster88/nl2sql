#!/usr/bin/env bash
#
# One-time setup for the NL2SQL agent.
#
# Pulls the published Postgres image (dataset already inside it), builds the
# agent image, starts the database, and writes a .env so plain compose commands
# pick all of that up. When it finishes you can just run:
#
#     docker compose run --rm agent "your question"
#
set -euo pipefail

cd "$(dirname "$0")"

POSTGRES_IMAGE="mcfaddja/nl2sql-retail-postgres"
POSTGRES_TAG="v1"
OLLAMA_URL=""
OLLAMA_MODEL=""
POSTGRES_PORT=""
BUILD_POSTGRES=0
RESET=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

  -t, --tag TAG          Postgres image tag to pull (default: v1)
  -i, --image NAME       Postgres image repository
                         (default: mcfaddja/nl2sql-retail-postgres)
  -u, --ollama-url URL   Ollama host the agent should use
  -m, --model NAME       Ollama model the agent should use
  -p, --port PORT        Host port to publish Postgres on (default: 5432)
      --build            Build the Postgres image locally instead of pulling
                         it (regenerates the dataset; takes a few minutes)
      --reset            Delete the existing database volume first, so the
                         dataset comes from the image. DESTROYS local changes.
  -h, --help             Show this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--tag) POSTGRES_TAG="$2"; shift 2 ;;
        -i|--image) POSTGRES_IMAGE="$2"; shift 2 ;;
        -u|--ollama-url) OLLAMA_URL="$2"; shift 2 ;;
        -m|--model) OLLAMA_MODEL="$2"; shift 2 ;;
        -p|--port) POSTGRES_PORT="$2"; shift 2 ;;
        --build) BUILD_POSTGRES=1; shift ;;
        --reset) RESET=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1" >&2; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# --- Prerequisites ---------------------------------------------------------
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker and retry."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."
info "Docker $(docker version --format '{{.Server.Version}}') with Compose $(docker compose version --short)"

# --- Existing data ---------------------------------------------------------
VOLUME_NAME="nl2sql-pgdata"
if [[ $RESET -eq 1 ]]; then
    step "Removing the existing database volume"
    docker compose down -v >/dev/null 2>&1 || true
    info "volume $VOLUME_NAME deleted; the dataset will come from the image"
elif docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    step "Existing database volume found"
    warn "volume $VOLUME_NAME already exists and takes precedence over the image,"
    warn "so its current contents are what you will query. Re-run with --reset"
    warn "to discard it and start from the image's dataset."
fi

# --- Postgres image --------------------------------------------------------
if [[ $BUILD_POSTGRES -eq 1 ]]; then
    step "Building the Postgres image locally (this regenerates the dataset)"
    POSTGRES_IMAGE="nl2sql-retail-postgres"
    POSTGRES_TAG="latest"
    IMAGE_NAME="$POSTGRES_IMAGE" IMAGE_TAG="$POSTGRES_TAG" docker compose build postgres
else
    step "Pulling $POSTGRES_IMAGE:$POSTGRES_TAG (Postgres with the test dataset)"
    info "about 290 MB to download, roughly 1.4 GB on disk"
    docker pull "$POSTGRES_IMAGE:$POSTGRES_TAG" ||
        die "could not pull $POSTGRES_IMAGE:$POSTGRES_TAG. Check the tag and your network."
fi

# --- Environment file ------------------------------------------------------
step "Writing .env"
if [[ -f .env ]]; then
    mv .env .env.bak
    info "existing .env moved to .env.bak"
fi
{
    echo "# Written by setup.sh -- delete this file to go back to building locally."
    echo "IMAGE_NAME=$POSTGRES_IMAGE"
    echo "IMAGE_TAG=$POSTGRES_TAG"
    if [[ -n "$OLLAMA_URL" ]]; then echo "OLLAMA_BASE_URL=$OLLAMA_URL"; fi
    if [[ -n "$OLLAMA_MODEL" ]]; then echo "OLLAMA_MODEL=$OLLAMA_MODEL"; fi
    if [[ -n "$POSTGRES_PORT" ]]; then echo "POSTGRES_PORT=$POSTGRES_PORT"; fi
} > .env
info "compose will use $POSTGRES_IMAGE:$POSTGRES_TAG"

# --- Agent image -----------------------------------------------------------
step "Building the agent image"
docker compose build agent

# --- Start the database ----------------------------------------------------
step "Starting Postgres"
docker compose up -d postgres

info "waiting for the database to become healthy..."
for _ in $(seq 1 60); do
    status=$(docker inspect --format '{{.State.Health.Status}}' nl2sql-postgres 2>/dev/null || echo starting)
    [[ "$status" == "healthy" ]] && break
    sleep 2
done
[[ "${status:-}" == "healthy" ]] || die "Postgres did not become healthy. Check 'docker compose logs postgres'."

rows=$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-nl2sql}" \
    -d "${POSTGRES_DB:-nl2sql_retail}" -tAc \
    "SELECT count(*) FROM fact_pos_retail_sales" 2>/dev/null || echo "")
[[ -n "$rows" ]] || die "the database is up but the dataset is missing. Try --reset."
info "database ready with $rows sales rows"

# --- Ollama ----------------------------------------------------------------
step "Checking Ollama"
# Read back whatever compose will actually hand the agent.
effective_url=$(docker compose config 2>/dev/null |
    awk '/OLLAMA_BASE_URL:/ {print $2; exit}')
effective_model=$(docker compose config 2>/dev/null |
    awk '/OLLAMA_MODEL:/ {print $2; exit}')
effective_url=${effective_url:-http://192.168.44.129:11434}
effective_model=${effective_model:-qwen3.8:latest}

if tags=$(curl -sf --max-time 5 "$effective_url/api/tags" 2>/dev/null); then
    if printf '%s' "$tags" | grep -q "\"$effective_model\""; then
        info "$effective_model is available at $effective_url"
    else
        warn "$effective_url is reachable but does not have $effective_model."
        warn "Pull it there, or re-run with --model <name>."
    fi
else
    warn "could not reach Ollama at $effective_url from this machine."
    warn "The agent will fail until it is reachable; re-run with --ollama-url URL."
fi

# --- Done ------------------------------------------------------------------
cat <<EOF

==> Setup complete. Ask a question with:

    docker compose run --rm agent "How many stores are there?"

    docker compose run --rm agent            # interactive session
    docker compose down                      # stop the database (data kept)

EOF
