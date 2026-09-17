#!/usr/bin/env bash
#
# Step 3: start the pgvector instance that stores the embedded text.
#
# Listens on 5434. Data lives in the named volume nl2sql-rag-vectordb-data and
# survives stop, start and `docker compose down`.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PULL_IMAGE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--image) PULL_IMAGE="$2"; shift 2 ;;
        -h|--help)
            cat <<'EOF'
Usage: ./03_start_vector_db.sh [--image REPO:TAG]

  --image REPO:TAG   Pull a published vector-store image instead of building
                     locally (its embeddings come with it).
EOF
            exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

require_docker

if [[ -n "$PULL_IMAGE" ]]; then
    step "Pulling $PULL_IMAGE"
    docker pull "$PULL_IMAGE" || die "could not pull $PULL_IMAGE"
    export VECTORDB_IMAGE="${PULL_IMAGE%:*}"
    export VECTORDB_TAG="${PULL_IMAGE##*:}"
    step "Starting vector store from the published image"
    compose up -d --no-build vectordb
else
    step "Starting the vector store (building on first run)"
    compose up -d vectordb
fi

wait_healthy nl2sql-rag-vectordb

docker exec nl2sql-rag-vectordb psql -U "${RAG_DB_USER:-ragproc}" \
    -d "${VECTOR_DB_NAME:-nl2sql_vectors}" -qtAc \
    "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
info "pgvector extension ready"
info "vector store ready on localhost:${VECTOR_DB_PORT:-5434}"
info "connect: postgresql://${RAG_DB_USER:-ragproc}:${RAG_DB_PASSWORD:-ragproc}@localhost:${VECTOR_DB_PORT:-5434}/${VECTOR_DB_NAME:-nl2sql_vectors}"
