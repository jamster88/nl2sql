#!/usr/bin/env bash
#
# Full pipeline: start both databases, chunk every knowledge document, embed
# every chunk. Safe to re-run -- every step is incremental.
#
# Usage: ./run_all.sh [--docs DIR] [--model NAME] [--ollama-url URL]
#                     [--publish DOCKERHUB_USER] [--tag TAG]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOCS_DIR="$REPO_DIR/knowledge"
MODEL="${EMBED_MODEL:-bge-m3}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
PUBLISH_USER=""
TAG="v1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --docs) DOCS_DIR="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --ollama-url) OLLAMA_URL="$2"; shift 2 ;;
        --publish) PUBLISH_USER="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        -h|--help)
            cat <<'EOF'
Usage: ./run_all.sh [options]

  --docs DIR              Directory of markdown documents (default: ../knowledge)
  --model NAME            Embedding model (default: bge-m3)
  --ollama-url URL        Ollama host (default: http://localhost:11434)
  --publish USER          Also snapshot both databases and push them to Docker
                          Hub under USER/ (requires `docker login`)
  --tag TAG               Tag for published images (default: v1)
EOF
            exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[[ -d "$DOCS_DIR" ]] || die "documents directory not found: $DOCS_DIR"
require_docker

step "Step 1/4: chunk store"
"$RAG_DIR/01_start_chunk_db.sh"

step "Step 2/4: chunking documents from $DOCS_DIR"
py 02_chunk_document.py --all "$DOCS_DIR" --ollama-url "$OLLAMA_URL"

step "Step 3/4: vector store"
"$RAG_DIR/03_start_vector_db.sh"

step "Step 4/4: embedding chunks with $MODEL"
py 04_embed_document.py --all --model "$MODEL" --ollama-url "$OLLAMA_URL"

if [[ -n "$PUBLISH_USER" ]]; then
    step "Publishing both databases to Docker Hub as $PUBLISH_USER/*:$TAG"
    "$RAG_DIR/publish_db_image.sh" chunkdb  "$PUBLISH_USER/nl2sql-rag-chunkdb:$TAG"
    "$RAG_DIR/publish_db_image.sh" vectordb "$PUBLISH_USER/nl2sql-rag-vectordb:$TAG"
fi

cat <<EOF

==> Pipeline complete.

    chunk store:  postgresql://${RAG_DB_USER:-ragproc}:${RAG_DB_PASSWORD:-ragproc}@localhost:${CHUNK_DB_PORT:-5433}/${CHUNK_DB_NAME:-nl2sql_chunks}
    vector store: postgresql://${RAG_DB_USER:-ragproc}:${RAG_DB_PASSWORD:-ragproc}@localhost:${VECTOR_DB_PORT:-5434}/${VECTOR_DB_NAME:-nl2sql_vectors}

    After editing a document, run ./run_update.sh
    To bring the vector store up for the RAG, run ./start_rag_db.sh

EOF
