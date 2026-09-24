#!/usr/bin/env bash
#
# Abbreviated pipeline: push document edits through to the vector store.
#
# Re-chunks the documents and re-embeds only what actually changed. Chunk ids
# are content hashes, so an edit to one section leaves every other chunk (and
# its embedding) untouched.
#
# Usage: ./run_update.sh [--docs DIR] [--model NAME] [--ollama-url URL]
#                        [--publish DOCKERHUB_USER] [--tag TAG]
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
        # 2,10 is the header comment and nothing after it. The range
        # has to stop before `set -euo pipefail`, or --help prints code.
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[[ -d "$DOCS_DIR" ]] || die "documents directory not found: $DOCS_DIR"
require_docker

# Start only what is not already up; never rebuilds.
for pair in "nl2sql-rag-chunkdb chunkdb" "nl2sql-rag-vectordb vectordb"; do
    set -- $pair
    if [[ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" != "true" ]]; then
        info "$1 is not running; starting it"
        compose up -d "$2"
        wait_healthy "$1"
    fi
done

step "Re-chunking documents from $DOCS_DIR"
py 02_chunk_document.py --all "$DOCS_DIR" --ollama-url "$OLLAMA_URL"

step "Embedding new and changed chunks with $MODEL"
py 04_embed_document.py --all --model "$MODEL" --ollama-url "$OLLAMA_URL"

if [[ -n "$PUBLISH_USER" ]]; then
    step "Re-publishing both databases as $PUBLISH_USER/*:$TAG"
    "$RAG_DIR/publish_db_image.sh" chunkdb  "$PUBLISH_USER/nl2sql-rag-chunkdb:$TAG"
    "$RAG_DIR/publish_db_image.sh" vectordb "$PUBLISH_USER/nl2sql-rag-vectordb:$TAG"
fi

step "Update complete"
