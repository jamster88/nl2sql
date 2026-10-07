ARG POSTGRES_IMAGE=postgres:18@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722
FROM ${POSTGRES_IMAGE}

# PGDATA must sit outside /var/lib/postgresql: the base image declares that
# path as a VOLUME, and anything written to a volume path is invisible to
# `docker commit` and to an image built from a snapshot of the cluster.
ENV PGDATA=/var/lib/pgdata

RUN mkdir -p "$PGDATA" \
 && chown -R postgres:postgres "$PGDATA" \
 && chmod 700 "$PGDATA"

LABEL org.opencontainers.image.title="nl2sql RAG chunk store" \
      org.opencontainers.image.description="Postgres holding semantically chunked knowledge documents, one table per document"
