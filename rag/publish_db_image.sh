#!/usr/bin/env bash
#
# Snapshot a running RAG database into an image and push it to Docker Hub.
#
# The data lives in a named volume, and a volume's contents are NOT captured by
# `docker commit` or by a plain build. So this script stops the container for a
# clean shutdown checkpoint, tars the volume, bakes the tar into a new image
# layer, restarts the container, and pushes.
#
# Usage: ./publish_db_image.sh <chunkdb|vectordb> <repo:tag> [--no-push]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SERVICE="${1:-}"
IMAGE_REF="${2:-}"
PUSH=1
shift 2 2>/dev/null || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push) PUSH=0; shift ;;
        *) die "unknown option: $1" ;;
    esac
done

[[ -n "$SERVICE" && -n "$IMAGE_REF" ]] || die "usage: ./publish_db_image.sh <chunkdb|vectordb> <repo:tag> [--no-push]"
[[ "$IMAGE_REF" == *:* ]] || die "image reference needs an explicit tag, e.g. user/name:v1"

case "$SERVICE" in
    chunkdb)  CONTAINER=nl2sql-rag-chunkdb;  VOLUME=nl2sql-rag-chunkdb-data;  BASE="${CHUNKDB_IMAGE:-nl2sql-rag-chunkdb}:${CHUNKDB_TAG:-latest}" ;;
    vectordb) CONTAINER=nl2sql-rag-vectordb; VOLUME=nl2sql-rag-vectordb-data; BASE="${VECTORDB_IMAGE:-nl2sql-rag-vectordb}:${VECTORDB_TAG:-latest}" ;;
    *) die "service must be 'chunkdb' or 'vectordb'" ;;
esac

require_docker
docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "volume $VOLUME does not exist -- has the pipeline run?"
docker image inspect "$BASE" >/dev/null 2>&1 || die "base image $BASE not found -- start the service first"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

WAS_RUNNING=0
if [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == "true" ]]; then
    WAS_RUNNING=1
    step "Stopping $CONTAINER for a clean shutdown checkpoint"
    docker stop "$CONTAINER" >/dev/null
fi

step "Snapshotting volume $VOLUME"
docker run --rm -v "$VOLUME":/src:ro -v "$BUILD_DIR":/out alpine \
    tar -C /src -cf /out/pgdata.tar . || die "could not read volume $VOLUME"
info "$(du -h "$BUILD_DIR/pgdata.tar" | cut -f1) of cluster data"

if [[ $WAS_RUNNING -eq 1 ]]; then
    step "Restarting $CONTAINER"
    docker start "$CONTAINER" >/dev/null
    wait_healthy "$CONTAINER"
fi

step "Building $IMAGE_REF"
cp "$RAG_DIR/docker/seeded.Dockerfile" "$BUILD_DIR/Dockerfile"
docker build --build-arg BASE_IMAGE="$BASE" -t "$IMAGE_REF" "$BUILD_DIR"

if [[ $PUSH -eq 1 ]]; then
    step "Pushing $IMAGE_REF"
    docker push "$IMAGE_REF" || die "push failed -- is 'docker login' done for this account?"
    info "published $IMAGE_REF"
    info "Docker Hub creates new repositories as PUBLIC by default; check the"
    info "repository settings if that is not what you intend."
else
    info "built but not pushed (--no-push). Push with: docker push $IMAGE_REF"
fi
