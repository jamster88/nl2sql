#!/usr/bin/env bash
#
# Publish a RAG database as a multi-arch image on Docker Hub.
#
# The data lives in a named volume, and a volume's contents are NOT captured by
# `docker commit` or by a plain build. So this script dumps the store with
# pg_dumpall and builds an image that restores the dump into a cluster
# initialised for each platform -- linux/amd64 and linux/arm64, in one buildx
# build -- then pushes it.
#
# It used to tar a stopped container's PGDATA instead. That was one machine's
# data directory, which Postgres does not promise moves between architectures,
# so every image built that way was arm64 only. A dump is SQL, and restores
# natively anywhere; docker/restore.Dockerfile has the other half.
#
# Usage: ./publish_db_image.sh <chunkdb|vectordb> <repo:tag> [--from IMAGE] [--no-push]
#
#   --from IMAGE  dump a published image instead of the pipeline's store: how
#                 a tag built the old way is republished multi-arch, unchanged
#   --no-push     build for this machine only and load it; push nothing
#
# PUBLISH_PLATFORMS overrides the platforms (default linux/amd64,linux/arm64).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SERVICE="${1:-}"
IMAGE_REF="${2:-}"
PUSH=1
FROM_IMAGE=""

# Checked before the shift, not after. `shift 2` with one argument fails and
# leaves it in place, so the option loop below then reported the service name
# as an unknown option -- for the likeliest mistake there is, forgetting the
# image reference.
[[ -n "$SERVICE" && -n "$IMAGE_REF" ]] || die "usage: ./publish_db_image.sh <chunkdb|vectordb> <repo:tag> [--from IMAGE] [--no-push]"
[[ "$IMAGE_REF" == *:* ]] || die "image reference needs an explicit tag, e.g. user/name:v1"

shift 2
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push) PUSH=0; shift ;;
        --from)
            [[ -n "${2:-}" ]] || die "--from needs an image, e.g. --from mcfaddja/nl2sql-rag-vectordb:v3"
            FROM_IMAGE="$2"; shift 2 ;;
        *) die "unknown option: $1" ;;
    esac
done

case "$SERVICE" in
    chunkdb)  CONTAINER=nl2sql-rag-chunkdb;  VOLUME=nl2sql-rag-chunkdb-data ;;
    vectordb) CONTAINER=nl2sql-rag-vectordb; VOLUME=nl2sql-rag-vectordb-data ;;
    *) die "service must be 'chunkdb' or 'vectordb'" ;;
esac
DB_USER="${RAG_DB_USER:-ragproc}"
PLATFORMS="${PUBLISH_PLATFORMS:-linux/amd64,linux/arm64}"

require_docker
docker buildx version >/dev/null 2>&1 || die "docker buildx is required: it is what builds for more than one platform."

BUILD_DIR="$(mktemp -d)"
SOURCE=""        # the container the dump is taken from
THROWAWAY=0      # started here from --from, so removed here
RESTOP=0         # the pipeline's own container, started here, so stopped again

# On every exit, a failure included: a throwaway container left behind would
# block the next run's name, and a store this script started should be as the
# user left it.
cleanup() {
    if [[ $THROWAWAY -eq 1 ]]; then
        docker rm -f "$SOURCE" >/dev/null 2>&1 || true
    elif [[ $RESTOP -eq 1 ]]; then
        info "stopping $SOURCE again, as it was"
        docker stop "$SOURCE" >/dev/null 2>&1 || true
    fi
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

if [[ -n "$FROM_IMAGE" ]]; then
    SOURCE="nl2sql-rag-publish-$SERVICE"
    step "Starting $FROM_IMAGE to dump it"
    docker rm -f "$SOURCE" >/dev/null 2>&1 || true
    docker run -d --name "$SOURCE" \
        --health-cmd "pg_isready -U $DB_USER -d postgres" --health-interval 2s \
        "$FROM_IMAGE" >/dev/null || die "could not start $FROM_IMAGE -- check the reference, and that it can be pulled."
    THROWAWAY=1
    wait_healthy "$SOURCE"
else
    docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "volume $VOLUME does not exist -- has the pipeline run?"
    SOURCE="$CONTAINER"
    # A running store is dumped where it stands: pg_dumpall reads each
    # database in one snapshot, so nothing has to stop for it.
    if [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]]; then
        step "Starting $CONTAINER to dump it"
        docker start "$CONTAINER" >/dev/null 2>&1 || die "could not start $CONTAINER -- start the service first."
        RESTOP=1
        wait_healthy "$CONTAINER"
    fi
fi

step "Dumping $SOURCE"
docker exec "$SOURCE" pg_dumpall --username="$DB_USER" > "$BUILD_DIR/dump.sql" \
    || die "could not dump $SOURCE"
# initdb in the build makes the bootstrap role, so the dump's CREATE of it
# would fail the restore; its ALTER ROLE, with the password hash, stays.
grep -qx "CREATE ROLE $DB_USER;" "$BUILD_DIR/dump.sql" \
    || die "the dump does not create $DB_USER -- is RAG_DB_USER the store's superuser?"
grep -vx "CREATE ROLE $DB_USER;" "$BUILD_DIR/dump.sql" > "$BUILD_DIR/cluster.sql"
rm "$BUILD_DIR/dump.sql"
docker exec "$SOURCE" sh -c 'cat "$PGDATA/pg_hba.conf"' > "$BUILD_DIR/pg_hba.conf" \
    || die "could not read $SOURCE's pg_hba.conf"
info "$(du -h "$BUILD_DIR/cluster.sql" | cut -f1) of SQL"

# The kind's own Dockerfile, continued by the restore: one source for the base
# image, PGDATA and the labels, whether the image is built empty or populated.
cat "$RAG_DIR/docker/$SERVICE.Dockerfile" "$RAG_DIR/docker/restore.Dockerfile" > "$BUILD_DIR/Dockerfile"

if [[ $PUSH -eq 1 ]]; then
    step "Building $IMAGE_REF for $PLATFORMS and pushing it"
    docker buildx build --platform "$PLATFORMS" --build-arg DB_USER="$DB_USER" \
        -t "$IMAGE_REF" --push "$BUILD_DIR" \
        || die "build or push failed -- is 'docker login' done for this account, and can this builder build $PLATFORMS?"
    info "published $IMAGE_REF ($PLATFORMS)"
    info "Docker Hub creates new repositories as PUBLIC by default; check the"
    info "repository settings if that is not what you intend."
else
    step "Building $IMAGE_REF for this machine"
    docker buildx build --build-arg DB_USER="$DB_USER" -t "$IMAGE_REF" --load "$BUILD_DIR" \
        || die "build failed"
    info "built for this machine only, not pushed (--no-push). Publish with:"
    info "./publish_db_image.sh $SERVICE $IMAGE_REF${FROM_IMAGE:+ --from $FROM_IMAGE}"
fi
