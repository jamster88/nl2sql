#!/usr/bin/env bash
#
# One-time setup for the NL2SQL RAG agent (v2).
#
# Brings up the whole stack in one call:
#
#   nl2sql-postgres   the retail dataset, already inside the image
#   nl2sql-vectordb   pgvector holding the embedded knowledge base
#   agent             the v2 agent image, run on demand
#
# It pulls each image, starts both databases, writes a .env so plain compose
# commands pick all of that up, checks that the chat and embedding models are
# reachable, and finally proves the agent container can actually retrieve from
# the knowledge base. When it finishes you can just run:
#
#     docker compose run --rm agent "your question"
#
set -euo pipefail

cd "$(dirname "$0")"

POSTGRES_IMAGE="mcfaddja/nl2sql-retail-postgres"
POSTGRES_TAG="v1"
AGENT_IMAGE="mcfaddja/nl2sql-agent"
AGENT_TAG="v4"
VECTOR_IMAGE="mcfaddja/nl2sql-rag-vectordb"
VECTOR_TAG="v3"
CONTEXT_IMAGE="mcfaddja/nl2sql-rag-chunkdb"
CONTEXT_TAG="v3"
OLLAMA_URL=""
OLLAMA_MODEL=""
EMBED_URL=""
EMBED_MODEL_NAME=""
POSTGRES_PORT=""
BUILD_POSTGRES=0
BUILD_AGENT=0
WITH_RAG=1
VERIFY=1
RESET=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

  -t, --tag TAG          Postgres image tag to pull (default: v1)
  -i, --image NAME       Postgres image repository
                         (default: mcfaddja/nl2sql-retail-postgres)
  -u, --ollama-url URL   Ollama host serving the chat model
  -m, --model NAME       Ollama model the agent should use
  -p, --port PORT        Host port to publish Postgres on (default: 5432)
      --agent-image NAME Agent image repository
                         (default: mcfaddja/nl2sql-agent)
      --agent-tag TAG    Agent image tag to pull (default: v3)
      --build-agent      Build the agent image from source instead of pulling
      --vector-image N   Vector store image (default: mcfaddja/nl2sql-rag-vectordb)
      --vector-tag TAG   Vector store image tag (default: v3)
      --context-image N  Context store image (default: mcfaddja/nl2sql-rag-chunkdb)
      --context-tag TAG  Context store image tag (default: v3)
      --embed-url URL    Ollama host serving the embedding model
                         (default: http://host.docker.internal:11434, i.e. the
                         Ollama on this machine)
      --embed-model NAME Embedding model for retrieval (default: bge-m3)
      --no-rag           Skip the knowledge base; the agent answers from the
                         schema alone, like v1
      --no-verify        Skip the end-of-setup retrieval check
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
        --agent-image) AGENT_IMAGE="$2"; shift 2 ;;
        --agent-tag) AGENT_TAG="$2"; shift 2 ;;
        --build-agent) BUILD_AGENT=1; shift ;;
        --vector-image) VECTOR_IMAGE="$2"; shift 2 ;;
        --vector-tag) VECTOR_TAG="$2"; shift 2 ;;
        --context-image) CONTEXT_IMAGE="$2"; shift 2 ;;
        --context-tag) CONTEXT_TAG="$2"; shift 2 ;;
        --embed-url) EMBED_URL="$2"; shift 2 ;;
        --embed-model) EMBED_MODEL_NAME="$2"; shift 2 ;;
        --no-rag) WITH_RAG=0; shift ;;
        --no-verify) VERIFY=0; shift ;;
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

# --- Read-only role --------------------------------------------------------
# The agent connects as a role that can SELECT and nothing else, not as the
# owner that loaded the data. A volume created from an image that predates
# that role keeps whatever roles it had, so the role is (re)created on every
# start; docker/reader_role.sql is idempotent. Local connections inside the
# container are trusted, which is why no superuser password is needed here.
compose_env() {  # compose_env KEY DEFAULT -- what compose hands the agent: shell, then .env
    local value="${!1:-}"
    if [[ -z "$value" && -f .env ]]; then
        value=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2-)
    fi
    printf '%s' "${value:-$2}"
}

ensure_extensions() {
    docker compose exec -T postgres psql -U postgres -q \
        -d "$(compose_env POSTGRES_DB nl2sql_retail)" \
        -v ON_ERROR_STOP=1 \
        -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" >/dev/null
}

ensure_reader_role() {
    docker compose exec -T postgres psql -U postgres -q \
        -d "$(compose_env POSTGRES_DB nl2sql_retail)" \
        -v ON_ERROR_STOP=1 \
        -v reader="$(compose_env POSTGRES_READER_USER nl2sql_reader)" \
        -v reader_password="$(compose_env POSTGRES_READER_PASSWORD nl2sql_reader)" \
        -v owner="$(compose_env POSTGRES_USER nl2sql)" \
        -f - < docker/reader_role.sql >/dev/null
}

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

# --- Retrieval images ------------------------------------------------------
if [[ $WITH_RAG -eq 1 ]]; then
    step "Pulling $CONTEXT_IMAGE:$CONTEXT_TAG (context store: golden pairs and BM25 statistics)"
    if ! docker pull "$CONTEXT_IMAGE:$CONTEXT_TAG"; then
        die "could not pull $CONTEXT_IMAGE:$CONTEXT_TAG.
       If the repository is private, run 'docker login' first, or make it
       public on Docker Hub. To set up without retrieval, re-run with --no-rag."
    fi

    # Same story as the vector store below: the RAG pipeline may have left a
    # standalone container on this port and volume.
    if [[ -n "$(docker ps -q --filter name='^nl2sql-rag-chunkdb$')" ]]; then
        info "stopping the standalone nl2sql-rag-chunkdb container (compose takes over; data is kept)"
        docker stop nl2sql-rag-chunkdb >/dev/null
    fi

    step "Pulling $VECTOR_IMAGE:$VECTOR_TAG (pgvector with the knowledge base and golden-pair vectors)"
    if ! docker pull "$VECTOR_IMAGE:$VECTOR_TAG"; then
        die "could not pull $VECTOR_IMAGE:$VECTOR_TAG.
       If the repository is private, run 'docker login' first, or make it
       public on Docker Hub. To set up without retrieval, re-run with --no-rag."
    fi

    # The RAG pipeline may have left a standalone container bound to the same
    # port and data volume; compose manages it from here on.
    if [[ -n "$(docker ps -q --filter name='^nl2sql-rag-vectordb$')" ]]; then
        info "stopping the standalone nl2sql-rag-vectordb container (compose takes over; data is kept)"
        docker stop nl2sql-rag-vectordb >/dev/null
    fi
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
    echo "AGENT_IMAGE_NAME=$AGENT_IMAGE"
    echo "AGENT_IMAGE_TAG=$AGENT_TAG"
    echo "VECTOR_IMAGE_NAME=$VECTOR_IMAGE"
    echo "VECTOR_IMAGE_TAG=$VECTOR_TAG"
    echo "CONTEXT_IMAGE_NAME=$CONTEXT_IMAGE"
    echo "CONTEXT_IMAGE_TAG=$CONTEXT_TAG"
    echo "RAG_ENABLED=$([[ $WITH_RAG -eq 1 ]] && echo true || echo false)"
    if [[ -n "$OLLAMA_URL" ]]; then echo "OLLAMA_BASE_URL=$OLLAMA_URL"; fi
    if [[ -n "$OLLAMA_MODEL" ]]; then echo "OLLAMA_MODEL=$OLLAMA_MODEL"; fi
    if [[ -n "$EMBED_URL" ]]; then echo "EMBED_BASE_URL=$EMBED_URL"; fi
    if [[ -n "$EMBED_MODEL_NAME" ]]; then echo "EMBED_MODEL=$EMBED_MODEL_NAME"; fi
    if [[ -n "$POSTGRES_PORT" ]]; then echo "POSTGRES_PORT=$POSTGRES_PORT"; fi
} > .env
info "compose will use $POSTGRES_IMAGE:$POSTGRES_TAG"

# --- Agent image -----------------------------------------------------------
if [[ $BUILD_AGENT -eq 1 ]]; then
    step "Building the agent image from source"
    docker compose build agent
else
    step "Pulling $AGENT_IMAGE:$AGENT_TAG (the RAG agent)"
    if ! docker pull "$AGENT_IMAGE:$AGENT_TAG"; then
        warn "could not pull $AGENT_IMAGE:$AGENT_TAG (private repo, or not logged in);"
        warn "building it from source instead, which needs no registry access."
        docker compose build agent
    fi
fi

# --- Start the databases ---------------------------------------------------
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

ensure_extensions || warn "could not create pg_trgm; literal matching falls back to difflib."
ensure_reader_role || die "could not create the agent's read-only role. Check 'docker compose logs postgres'."
info "read-only role $(compose_env POSTGRES_READER_USER nl2sql_reader) ready for the agent"

# --- Retrieval stores ------------------------------------------------------
if [[ $WITH_RAG -eq 1 ]]; then
    step "Starting the knowledge base"
    docker compose up -d vectordb

    info "waiting for pgvector to become healthy..."
    for _ in $(seq 1 60); do
        vstatus=$(docker inspect --format '{{.State.Health.Status}}' nl2sql-vectordb 2>/dev/null || echo starting)
        [[ "$vstatus" == "healthy" ]] && break
        sleep 2
    done
    [[ "${vstatus:-}" == "healthy" ]] || die "pgvector did not become healthy. Check 'docker compose logs vectordb'."

    chunks=$(docker compose exec -T vectordb psql -U "${VECTOR_DB_USER:-ragproc}" \
        -d "${VECTOR_DB_NAME:-nl2sql_vectors}" -tAc "
        SELECT sum(n) FROM (
            SELECT (xpath('/row/c/text()',
                    query_to_xml('SELECT count(*) AS c FROM ' || quote_ident(tablename),
                                 false, true, '')))[1]::text::bigint AS n
            FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%\_embeddings'
        ) t" 2>/dev/null | tr -d '[:space:]' || echo "")
    if [[ -n "$chunks" && "$chunks" != "0" ]]; then
        info "knowledge base ready with $chunks embedded chunks"
    else
        warn "the knowledge base is up but has no embedded chunks in it."
        warn "Retrieval will be skipped until it is populated."
    fi

    step "Starting the context store"
    docker compose up -d chunkdb

    info "waiting for the context store to become healthy..."
    for _ in $(seq 1 60); do
        cstatus=$(docker inspect --format '{{.State.Health.Status}}' nl2sql-chunkdb 2>/dev/null || echo starting)
        [[ "$cstatus" == "healthy" ]] && break
        sleep 2
    done
    [[ "${cstatus:-}" == "healthy" ]] || die "the context store did not become healthy. Check 'docker compose logs chunkdb'."

    pairs=$(docker compose exec -T chunkdb psql -U "${CONTEXT_DB_USER:-ragproc}" \
        -d "${CONTEXT_DB_NAME:-nl2sql_chunks}" -tAc \
        "SELECT count(*) FROM golden_pairs" 2>/dev/null | tr -d '[:space:]' || echo "")
    if [[ -n "$pairs" && "$pairs" != "0" ]]; then
        info "context store ready with $pairs golden question/SQL pairs"
    else
        warn "the context store is up but holds no golden pairs."
        warn "Worked examples will be skipped until it is populated."
    fi
fi

# --- Ollama ----------------------------------------------------------------
step "Checking Ollama"

# Read back whatever compose will actually hand the agent. awk consumes the
# whole stream rather than exiting on the first match: under `set -o pipefail`
# an early exit can take the pipeline down with SIGPIPE (141) if the producer
# is still writing.
compose_value() {
    docker compose config 2>/dev/null |
        awk -v key="$1:" '$1 == key && !seen { print $2; seen = 1 }'
}

effective_url=$(compose_value OLLAMA_BASE_URL)
effective_model=$(compose_value OLLAMA_MODEL)
effective_url=${effective_url:-http://192.168.10.82:11434}
effective_model=${effective_model:-qwen3.8-256k}

if tags=$(curl -sf --max-time 5 "$effective_url/api/tags" 2>/dev/null); then
# Ollama reports a model the user asked for as `name:latest` when they gave no
# tag, so an exact match on the configured name reports a model that is
# present and working as missing. The embedding check below has always matched
# on the prefix for this reason; this one did not, and warned that "every
# question will fail" about a host the benchmark had just scored 15/15 against.
    if printf '%s' "$tags" | grep -q "\"$effective_model\(\"\|:\)"; then
        info "$effective_model is available at $effective_url"
    else
        warn "$effective_url is reachable but does not have $effective_model."
        warn "Pull it there, or re-run with --model <name>."
    fi
else
    warn "could not reach Ollama at $effective_url from this machine."
    warn "The agent will fail until it is reachable; re-run with --ollama-url URL."
fi

# --- Embedding model -------------------------------------------------------
if [[ $WITH_RAG -eq 1 ]]; then
    step "Checking the embedding model"
    effective_embed_url=$(compose_value EMBED_BASE_URL)
    effective_embed_model=$(compose_value EMBED_MODEL)
    effective_embed_url=${effective_embed_url:-http://host.docker.internal:11434}
    effective_embed_model=${effective_embed_model:-bge-m3}

    # host.docker.internal is how the container reaches this machine; from the
    # shell running setup.sh that same Ollama is on localhost.
    probe_url=${effective_embed_url/host.docker.internal/localhost}
    if tags=$(curl -sf --max-time 5 "$probe_url/api/tags" 2>/dev/null); then
        if printf '%s' "$tags" | grep -q "\"$effective_embed_model"; then
            info "$effective_embed_model is available at $effective_embed_url"
        else
            warn "$probe_url is reachable but does not have $effective_embed_model."
            warn "Run 'ollama pull $effective_embed_model', or re-run with --embed-model NAME."
            warn "Without it the agent still answers, but without knowledge retrieval."
        fi
    else
        warn "could not reach an Ollama at $probe_url to serve $effective_embed_model."
        warn "Retrieval will be skipped; re-run with --embed-url URL to point elsewhere."
    fi
fi

# --- End-to-end check ------------------------------------------------------
# Everything above proves each piece is up; this proves they are wired to each
# other, which is what the next command the user runs actually depends on:
# the agent container resolving the vectordb service and reaching the
# embedding host. Failing here is a warning, not an error -- the agent still
# answers without retrieval.
if [[ $VERIFY -eq 1 && $WITH_RAG -eq 1 ]]; then
    step "Checking the agent can reach the knowledge base"
    probe='
import json
from nl2sql_agent.config import Settings
from nl2sql_agent.retrieval import KnowledgeBase, build_embedder
s = Settings.from_env()
kb = KnowledgeBase(s.vector_db_url, build_embedder(s), top_k=1)
print("PROBE " + json.dumps({"chunks": len(kb.search("market share")),
                             "collections": len(kb.collections())}))
'
    probe_out=$(docker compose run --rm --entrypoint python agent -c "$probe" 2>&1 || true)
    probe_line=$(printf '%s' "$probe_out" | grep '^PROBE ' || true)
    if [[ -n "$probe_line" ]]; then
        found=$(printf '%s' "$probe_line" | sed 's/.*"chunks": *\([0-9]*\).*/\1/')
        collections=$(printf '%s' "$probe_line" | sed 's/.*"collections": *\([0-9]*\).*/\1/')
        if [[ "${found:-0}" -gt 0 ]]; then
            info "retrieval works end to end ($collections collections searched)"
        else
            warn "the agent reached the knowledge base but it returned nothing."
            warn "Queries will still run, just without retrieved context."
        fi
    else
        warn "the agent container could not retrieve from the knowledge base:"
        printf '%s\n' "$probe_out" | tail -3 >&2
        warn "The agent will still answer, but without knowledge context."
    fi
fi

# --- Done ------------------------------------------------------------------
cat <<EOF

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-vectordb    the embedded knowledge base

    The agent runs on demand, as a third container:

    docker compose run --rm agent "How many stores are there?"

    docker compose run --rm agent            # interactive session
    docker compose down                      # stop both databases (data kept)

    A question that needs the knowledge base to get right:

    docker compose run --rm agent "What is our overall market share in fiscal year 2024?"

EOF
