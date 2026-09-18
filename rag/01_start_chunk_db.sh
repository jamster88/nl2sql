#!/usr/bin/env bash
#
# Step 1: start the Postgres instance that stores the chunked text.
#
# Listens on 5433 so it never collides with the retail testing database on
# 5432. Data lives in the named volume nl2sql-rag-chunkdb-data and survives
# stop, start and `docker compose down`.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PULL_IMAGE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--image) PULL_IMAGE="$2"; shift 2 ;;
        -h|--help)
            cat <<'EOF'
Usage: ./01_start_chunk_db.sh [--image REPO:TAG]

  --image REPO:TAG   Pull a published chunk-store image instead of building
                     locally (its data comes with it).
EOF
            exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

require_docker

if [[ -n "$PULL_IMAGE" ]]; then
    step "Pulling $PULL_IMAGE"
    docker pull "$PULL_IMAGE" || die "could not pull $PULL_IMAGE"
    export CHUNKDB_IMAGE="${PULL_IMAGE%:*}"
    export CHUNKDB_TAG="${PULL_IMAGE##*:}"
    step "Starting chunk store from the published image"
    compose up -d --no-build chunkdb
else
    step "Starting the chunk store (building on first run)"
    compose up -d chunkdb
fi

wait_healthy nl2sql-rag-chunkdb

info "chunk store ready on localhost:${CHUNK_DB_PORT:-5433}"
info "connect: postgresql://${RAG_DB_USER:-ragproc}:${RAG_DB_PASSWORD:-ragproc}@localhost:${CHUNK_DB_PORT:-5433}/${CHUNK_DB_NAME:-nl2sql_chunks}"
