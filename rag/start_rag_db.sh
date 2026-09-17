#!/usr/bin/env bash
#
# Bring up the pgvector store so a RAG retriever can query it.
#
# This is the only script the RAG needs: it starts the vector database, waits
# for it to be healthy, and prints what is inside so you can see the
# embeddings are actually there.
#
# Usage: ./start_rag_db.sh [--image REPO:TAG]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

"$RAG_DIR/03_start_vector_db.sh" "$@"

DB_USER="${RAG_DB_USER:-ragproc}"
DB_NAME="${VECTOR_DB_NAME:-nl2sql_vectors}"

step "Vector store contents"
# Exact counts: reltuples is -1 until a table has been analyzed, and these
# tables are small enough that counting them is free.
docker exec nl2sql-rag-vectordb psql -U "$DB_USER" -d "$DB_NAME" -P pager=off -c "
SELECT c.relname AS embedding_table,
       (xpath('/row/cnt/text()',
              query_to_xml('SELECT count(*) AS cnt FROM ' || quote_ident(c.relname),
                           false, true, '')))[1]::text::bigint AS chunks
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname LIKE '%\_embeddings'
ORDER BY c.relname;" 2>/dev/null || warn "could not list embedding tables"

cat <<EOF

==> Ready for retrieval.

    postgresql://${DB_USER}:${RAG_DB_PASSWORD:-ragproc}@localhost:${VECTOR_DB_PORT:-5434}/${DB_NAME}

    Nearest-neighbour query shape (cosine distance):

      SELECT chunk_id, heading_path, content, embedding <=> %s AS distance
      FROM <document>_embeddings
      ORDER BY embedding <=> %s
      LIMIT 5;

    ragproc.vector_store.search() implements exactly that.

EOF
