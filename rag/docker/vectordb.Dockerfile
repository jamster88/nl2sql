ARG PGVECTOR_IMAGE=pgvector/pgvector:pg18
FROM ${PGVECTOR_IMAGE}

# Same reasoning as the chunk store: keep PGDATA out of the base image's
# declared VOLUME so the populated cluster can be snapshotted into an image.
ENV PGDATA=/var/lib/pgdata

RUN mkdir -p "$PGDATA" \
 && chown -R postgres:postgres "$PGDATA" \
 && chmod 700 "$PGDATA"

LABEL org.opencontainers.image.title="nl2sql RAG vector store" \
      org.opencontainers.image.description="pgvector holding bge-m3 embeddings of the knowledge documents"
