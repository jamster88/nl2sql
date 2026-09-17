ARG POSTGRES_IMAGE=postgres:18
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
