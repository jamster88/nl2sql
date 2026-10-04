# RAG Data Pipeline

Loads the documents in [`knowledge/`](../knowledge), semantically chunks them,
stores the chunks in one Postgres instance, embeds them with BGE-M3, and stores
the vectors in a separate pgvector instance.

It also loads the golden question/SQL pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md)
-- as a relational table rather than as chunked prose -- and embeds two of their
columns separately, which is what the agent's ensemble retriever searches.

And it loads the SQL snippets in
[`context_questions/sql_snippets.md`](../context_questions/sql_snippets.md)
-- joins, filters, measures and dimensions, each beside what it means --
into a store of their own (step 7, since 5.6).

```bash
./run_all.sh          # everything, from nothing to a queryable vector store
./run_update.sh       # after editing a document: re-chunk and re-embed only what changed
./start_rag_db.sh     # bring up just the vector store, for the RAG to query

python 05_load_golden_pairs.py     # golden pairs -> context store, with a BM25 index
python 06_embed_golden_pairs.py    # their question and reasoning columns -> pgvector
python 07_load_snippets.py         # the SQL snippets -> their own store, with their meanings embedded
```

The databases stay off port 5432 so they never collide with the retail
testing database: the chunk store is on **5433**, the vector store on
**5434**, and the snippet store on **5438**.

## The seven steps

| Step | File | What it does |
|---|---|---|
| 1 | `01_start_chunk_db.sh` | Starts the Postgres instance that holds chunked text |
| 2 | `02_chunk_document.py` | Loads, parses, chunks a document; writes it to that instance |
| 3 | `03_start_vector_db.sh` | Starts the pgvector instance |
| 4 | `04_embed_document.py` | Reads chunks, embeds them, writes vectors to pgvector |
| 5 | `05_load_golden_pairs.py` | Parses the golden pairs into a table and builds a BM25 index over their keywords |
| 6 | `06_embed_golden_pairs.py` | Embeds the `question` and `reasoning_target` columns into two separate pgvector tables |
| 7 | `07_load_snippets.py` | Loads the SQL snippets into their store, embeds what each means, and creates the agent's read-only role |
| -- | `run_all.sh`, `run_update.sh`, `start_rag_db.sh` | Orchestration |

Steps 2 and 4 take document names as arguments and work on any markdown file,
not just the three in `knowledge/`:

```bash
python 02_chunk_document.py ../knowledge/business_index.md
python 02_chunk_document.py --all ../knowledge
python 04_embed_document.py business_index ddl_index
python 04_embed_document.py --all --model nomic-embed-text
```

Each document gets its own pair of tables, named from the file:
`business_index.md` → `business_index_chunks` → `business_index_embeddings`.


## The golden pairs (steps 5 and 6)

The pairs are **not** chunked the way the `knowledge/` documents are. Every
pair already has exactly the same eight fields and the `##` heading is the record
boundary, so semantic chunking has nothing to decide and would only lose
structure. They get a real relational table instead, one column per field:

```bash
python 05_load_golden_pairs.py
python 05_load_golden_pairs.py --probe "gross margin for the produce department"
python 06_embed_golden_pairs.py --probe "gross margin for the produce department"
```

The parser is strict: a pair missing any labelled field does not match the entry
pattern at all, and the loader compares the number of `## Qnn` headings against
the number of pairs that parsed cleanly, so a malformed pair is a loud failure
rather than a quietly short load.

### BM25, in the database

The ensemble weights a keyword search at 0.35, and specifies BM25. Postgres
ships `ts_rank`, which is a length-normalised tf-idf and **not** BM25, so the
ranking is built here rather than borrowed:

| Table | Holds |
|---|---|
| `golden_pairs` | one row per pair, plus a generated `keywords_tsv` column |
| `golden_pair_keyword_terms` | term frequency per pair, from the tsvector's positions |
| `golden_pair_keyword_docs` | each pair's keyword length |
| `golden_pair_keyword_stats` | document frequency and IDF per term |
| `golden_pair_bm25_params` | N, average length, `k1`, `b`, and the text-search config |

`golden_pairs_bm25(query_text)` joins those and returns `(chunk_id, score)`:

```sql
SELECT g.pair_id, round(b.score::numeric, 3), g.title
FROM golden_pairs_bm25('how do I compute gross margin for produce') b
JOIN golden_pairs g USING (chunk_id)
ORDER BY b.score DESC LIMIT 5;
```

IDF is the Robertson/Sparck-Jones form with `+1` smoothing, which keeps a term
that appears in every pair from scoring negative. Indexing and querying both go
through the **same** text-search configuration (`english`), which is why a
question about "monthly costs" reaches a pair keyworded "monthly cost" -- change
one and the other has to change with it, so it lives as a single constant.

Statistics are rebuilt from scratch on every load rather than updated
incrementally. At 45 documents that costs nothing, and a full rebuild cannot
leave the statistics disagreeing with the rows.

### Two embeddings per pair

Step 6 writes two tables, not one:

| Table | Embeds |
|---|---|
| `golden_pair_question_vectors` | `golden_pairs.question` |
| `golden_pair_reasoning_vectors` | `golden_pairs.reasoning_target` |

Separately, because the agent scores them separately and weights them
differently -- 0.50 against 0.15. Concatenating the two into one vector would
average what the user is asking for together with what the query has to get
right, and lose the ability to weight one above the other.

Neither is named `<doc>_embeddings`. The agent's **knowledge** retriever
discovers its collections by globbing for that suffix, so a pair table named
that way would be searched as if it were prose and fail on the missing columns.
The `_vectors` suffix keeps the two stores apart, and
[`tests/agent/test_examples_live.py`](../tests/agent/test_examples_live.py)
asserts the knowledge retriever never picks them up.

Re-embedding is incremental against a content hash covering all eight loaded
fields, so editing one pair's SQL re-embeds that pair and nothing else:

```
45 golden pairs in the context store
embedding with ollama:bge-m3 (1024 dimensions)
  question         -> golden_pair_question_vectors: 1 embedded, 44 already current, 0 removed
  reasoning_target -> golden_pair_reasoning_vectors: 1 embedded, 44 already current, 0 removed
```


## The SQL snippets (step 7)

```bash
python 07_load_snippets.py
python 07_load_snippets.py --dry-run
python 07_load_snippets.py --probe "net sales by department in fiscal 2024"
```

A snippet is one piece of SQL -- a join, a filter, a measure or a
dimension -- with the phrases a question says it with, what it means, and
the `FROM` clause it is written over. Like the golden pairs, each is a
record with fixed fields, so it gets a table rather than chunks; unlike them,
it lives in a store of its own, `nl2sql_snippets` on 5438, which is stock
`pgvector/pgvector:pg18` rather than a published image. It is built from the
document on start, so there is nothing to publish.

One script rather than two, because it is one store:

| Table | Holds |
|---|---|
| `sql_snippets` | One row per snippet: its id, kind, name, tables, keywords, meaning, `FROM` clause, SQL, note and content hash |
| `sql_snippet_vectors` | An embedding of each snippet's name, meaning and keywords, with an HNSW index; deleted with its snippet |
| `sql_snippet_source` | The document's hash and how many snippets were embedded with which model, written only after a complete load |

and `sql_snippets_keyword_match(question)`, a SQL function that matches each
keyword and the name as a phrase -- every one of its words in the question,
by the `english` text search configuration -- and weighs a match by the IDF
of its words. The agent calls it; it is in the database so the ranking can be
read and tested with `psql` alone.

The parser is strict in the golden pairs' way: a snippet missing a field, of
an unknown kind, or with an id used twice fails the load rather than being
skipped, and the count of `## Snn` headings is checked against what parsed.
Re-running is safe: rows are upserted, snippets no longer in the document are
removed with their vectors, and only a snippet whose content or model changed
is embedded again, so a load with nothing new needs no embedding host. The
source record is written last, and only when every snippet has a current
vector, so a load the embedding host interrupted is one the next start
finishes. `launch.sh` compares that record's hash with the document's own,
both taken inside the store's container, to decide whether to load at all.

The loader also (re)creates the role the agent reads the store as --
`snippets_reader` unless `SNIPPETS_READER_USER` says otherwise -- with
`CONNECT`, `USAGE` on the schema and `SELECT` on its tables, now and as they
are created, and nothing else; the matcher runs as any function does, with
the caller's rights. What the role can and cannot do is asked of a live store
by `tests/rag/test_snippets_store.py`, a write included.

## How the chunking works

`ragproc/chunker.py` extends [`chunking/semantic_chunker.py`](../chunking/semantic_chunker.py)
rather than replacing it. The base class splits a flat string wherever the
cosine distance between consecutive sentence embeddings spikes above a
percentile threshold. That is the right instinct for prose but wrong for these
documents: a `CREATE TABLE` block has no sentence punctuation, so it becomes
one enormous "sentence", and a pipe table would be cut apart mid-row.

So the subclass applies that logic selectively:

1. The document is parsed into sections on markdown headings. `ddl_index.md`
   and `business_index.md` were authored with one self-contained topic per
   `##` heading, so those boundaries are real information, not a guess.
2. Fenced code blocks, ```` ```meta ```` blocks and pipe tables are **atomic**
   and never split.
3. A section already under `--max-chunk-tokens` is emitted whole.
4. Only an oversized section is split further, and only then does the base
   class's drift detection run, on the prose parts, with atomic blocks kept
   intact.
5. Chunks below `--min-chunk-tokens` are folded into their neighbour from the
   same section.

Every chunk is prefixed with its heading path (`# DDL Index > dim_date`) so a
chunk retrieved alone still says what it is about, and any ```` ```meta ````
block in the section is parsed into a JSONB column for metadata filtering.

Because most sections already fit, embedding is usually never invoked at chunk
time — the embedding backend is constructed lazily and, for the current three
documents, is not needed at all.

Result at default settings:

| Document | Chunks | Token estimate (min/max/avg) |
|---|---|---|
| `business_index.md` | 20 | 115 / 314 / 202 |
| `data_dictionary.md` | 13 | 93 / 510 / 287 |
| `ddl_index.md` | 20 | 115 / 393 / 219 |

## Embedding

Defaults to **bge-m3 served by Ollama on the local machine** (1024
dimensions), which reuses the model already pulled there instead of
downloading a second copy of the weights.

```bash
python 04_embed_document.py --all                              # bge-m3 via local Ollama
python 04_embed_document.py --all --model nomic-embed-text     # any Ollama embedding model
python 04_embed_document.py --all --ollama-url http://host:11434
python 04_embed_document.py --all --backend sentence-transformers --model BAAI/bge-m3
```

The vector column is sized from the model's actual output dimension at table
creation, so switching models means dropping the embedding table first.

## Incremental updates

`run_update.sh` is the abbreviated path for after you edit a document.

A chunk's id is a hash of its own content (`business_index:9b5aa4777d064c57`),
not its position. So editing one section leaves every other chunk's id
unchanged, and the pipeline can tell exactly what moved:

```
1 new, 2 unchanged, 1 removed, 2 re-ordered      <- chunk store
1 embedded, 2 already current, 1 removed         <- vector store
```

Only the new chunk is sent to the embedding model; the vector for the deleted
chunk is removed. Re-running with no edits embeds nothing.

## Persistence and publishing

Both databases put `PGDATA` at `/var/lib/pgdata`, deliberately outside
`/var/lib/postgresql`, which the base images declare as a `VOLUME`. Writes to a
volume path are discarded from the image -- by `docker commit`, and by any
`RUN` in a build -- so a cluster living there could never be published.

Day to day, the data lives in named volumes (`nl2sql-rag-chunkdb-data`,
`nl2sql-rag-vectordb-data`) and survives `stop`, `start`, `restart` and
`docker compose down`. Only `down -v` destroys it.

To publish a database as a self-contained, multi-arch image:

```bash
./publish_db_image.sh vectordb mcfaddja/nl2sql-rag-vectordb:v4
./publish_db_image.sh chunkdb  mcfaddja/nl2sql-rag-chunkdb:v4
# or, as part of a full run:
./run_all.sh --publish mcfaddja --tag v4
```

| Tag | Contents |
|---|---|
| `nl2sql-rag-vectordb:v3_2` | knowledge collections **and** both golden-pair vector tables, 48 pairs; amd64 and arm64 |
| `nl2sql-rag-chunkdb:v3_2` | knowledge chunks **and** `golden_pairs` with its BM25 index, 48 pairs; amd64 and arm64 |
| `nl2sql-rag-vectordb:v3_1`, `nl2sql-rag-chunkdb:v3_1` | the same, with the first 45 pairs |
| `nl2sql-rag-vectordb:v3`, `nl2sql-rag-chunkdb:v3` | `v3_1`'s contents, arm64 only |
| `nl2sql-rag-vectordb:v1` | knowledge collections only -- what the v2 agent searches |

The script dumps the store with `pg_dumpall` -- live, since a dump reads each
database in one snapshot; a stopped store is started for it and stopped again
-- and builds the kind's own Dockerfile continued by
[`docker/restore.Dockerfile`](docker/restore.Dockerfile), which runs `initdb`
and restores the dump *inside the build*, once for each of `linux/amd64` and
`linux/arm64`, then pushes both in one `docker buildx build`. `--no-push`
builds this machine's platform alone and loads it; `PUBLISH_PLATFORMS`
overrides the pair.

It used to stop the container, tar the volume and add the tar as a layer. That
was one machine's data directory, and Postgres does not promise a data
directory moves between architectures -- so `v3` is arm64 only. `v3_1` is `v3`
republished the new way, from the published images themselves rather than
from whatever a volume holds today:

```bash
./publish_db_image.sh vectordb mcfaddja/nl2sql-rag-vectordb:v3_1 --from mcfaddja/nl2sql-rag-vectordb:v3
./publish_db_image.sh chunkdb  mcfaddja/nl2sql-rag-chunkdb:v3_1  --from mcfaddja/nl2sql-rag-chunkdb:v3
```

and was checked against `v3` on both architectures before it was pinned: the
schema, every table's contents, the roles, databases, `pg_hba.conf` and
settings, a password login over the network, and the HNSW indexes -- rebuilt by
the restore -- returning the same five nearest neighbours for every stored
vector.

`v3_2` brings the golden set up to the question document. It was made from
`v3_1` the way the stack itself catches up -- the two golden-pair loaders,
run in the review service's image against a copy of the published stores --
and then published from that copy. It was made with 5.5.1's review image;
every review image since carries the same two loaders:

```bash
docker network create v32
docker run -d --name v32-chunkdb  --network v32 mcfaddja/nl2sql-rag-chunkdb:v3_1
docker run -d --name v32-vectordb --network v32 mcfaddja/nl2sql-rag-vectordb:v3_1
docker run --rm --network v32 --add-host host.docker.internal:host-gateway \
  -v "$PWD/../context_questions:/app/context_questions:ro" --entrypoint sh \
  mcfaddja/nl2sql-review:v6_0_1 -c 'cd /app/rag &&
    python 05_load_golden_pairs.py /app/context_questions/translated_questions.md \
      --db-url postgresql://ragproc:ragproc@v32-chunkdb:5432/nl2sql_chunks &&
    python 06_embed_golden_pairs.py --model bge-m3 --ollama-url http://host.docker.internal:11434 \
      --chunk-db-url postgresql://ragproc:ragproc@v32-chunkdb:5432/nl2sql_chunks \
      --vector-db-url postgresql://ragproc:ragproc@v32-vectordb:5432/nl2sql_vectors'
docker stop v32-chunkdb v32-vectordb
docker commit v32-chunkdb nl2sql-rag-chunkdb:v3_2-source
docker commit v32-vectordb nl2sql-rag-vectordb:v3_2-source
./publish_db_image.sh chunkdb  mcfaddja/nl2sql-rag-chunkdb:v3_2  --from nl2sql-rag-chunkdb:v3_2-source
./publish_db_image.sh vectordb mcfaddja/nl2sql-rag-vectordb:v3_2 --from nl2sql-rag-vectordb:v3_2-source
```

The loaders wrote three new pairs and embedded only those; every knowledge
table's rows hashed the same before and after, and the knowledge documents'
stored file hashes match `knowledge/`. Both published architectures were then
checked against the source as `v3_1` was against `v3`.

The published image carries the data with it:

```bash
./01_start_chunk_db.sh  --image mcfaddja/nl2sql-rag-chunkdb:v1
./03_start_vector_db.sh --image mcfaddja/nl2sql-rag-vectordb:v1
```

Docker Hub creates new repositories as public by default. These contain only
synthetic documentation, but the database credentials are baked into the
cluster — treat them as public and do not reuse them.

## Schema

**Chunk store** (`nl2sql_chunks` on 5433) — `<doc>_chunks`:

| Column | Notes |
|---|---|
| `chunk_id` | PK, `<slug>:<content hash prefix>` |
| `source_doc` | document slug |
| `ordinal` | position in the document |
| `heading_path` | e.g. `DDL Index > dim_date` |
| `chunk_meta` | JSONB, parsed from the ```` ```meta ```` block |
| `content` | the chunk text, heading prefix included |
| `token_estimate`, `content_hash`, `created_at`, `updated_at` | |

A `rag_documents` registry tracks each document's source path, file hash,
chunk count and the chunker settings used.

**Vector store** (`nl2sql_vectors` on 5434) — `<doc>_embeddings`: the same
columns plus `embedding_model` and `embedding vector(1024)`, with an HNSW
index using `vector_cosine_ops`.

## Querying it from a RAG

```python
from ragproc import vector_store
from ragproc.embedder import build_embedder

embedder = build_embedder("ollama", "bge-m3", "http://localhost:11434")
conn = vector_store.connect("postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors")

vector = embedder.embed(["how do I avoid double counting market share"])[0]
for hit in vector_store.search(conn, "business_index", vector, limit=3):
    print(hit["distance"], hit["heading_path"])
```

The golden pairs are queried through the agent's ensemble rather than a single
store, since the ranking spans both databases:

```python
import sys; sys.path.insert(0, "../agent")
from nl2sql_agent.examples import GoldenPairLibrary, build_embedder

class S:
    embed_model, embed_base_url = "bge-m3", "http://localhost:11434"

library = GoldenPairLibrary(
    "postgresql+psycopg://ragproc:ragproc@localhost:5433/nl2sql_chunks",
    "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors",
    build_embedder(S()),
)
for pair in library.search("how do I avoid double counting market share"):
    print(f"{pair.score:.3f}  {pair.pair_id}  [{pair.found_by}]  {pair.title}")
```

## Configuration

Every setting is an environment variable with a CLI override:

| Variable | Default |
|---|---|
| `CHUNK_DB_URL` | `postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks` |
| `VECTOR_DB_URL` | `postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors` |
| `OLLAMA_URL` | `http://localhost:11434` |
| `EMBED_MODEL` | `bge-m3` |
| `EMBED_BACKEND` | `ollama` |
| `MAX_CHUNK_TOKENS` | 500 |
| `MIN_CHUNK_TOKENS` | 40 |
| `CHUNK_THRESHOLD_PERCENTILE` | 60 |
| `CHUNK_DB_PORT` / `VECTOR_DB_PORT` | 5433 / 5434 |
| `SNIPPETS_DB_URL` | `postgresql://snippets:snippets@localhost:5438/nl2sql_snippets`, for `07_load_snippets.py` |
| `SNIPPETS_READER_USER` / `SNIPPETS_READER_PASSWORD` | `snippets_reader` / `snippets_reader`, the role it creates |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r rag/requirements.txt
```

## Tests

```bash
pytest tests/rag --run-docker
```

322 tests: the parser against the real document, the BM25 ranking compared
score for score against an independent Okapi implementation, the pgvector
storage layer, all three loader scripts as command line programs, the snippet
document's parser and the snippet store -- the phrase matcher's ranking, the
loader's incremental embedding and its source record, and what the reader
role can and cannot do, asked of a live pgvector -- and the seven shell
scripts on this page. 80 of them need a database, behind `--run-docker`.

Those last ones are run rather than read. Each gets a throwaway copy of `rag/`
with a fake `docker` on PATH that records every call and returns scripted
results, so what is asserted is the decision: which service is started, which
image is pulled and when, whether a stopped store is started for its dump and
put back afterwards, what the build is handed -- the kind's Dockerfile and the
restore, the dump without its bootstrap `CREATE ROLE` -- and which `die` a bad
argument reaches.
They were untested until they were not, and writing the tests turned up three
defects -- see the repository README's
[coverage section](../README.md#the-parts-a-coverage-report-cannot-see).

The two images, the restore and `docker-compose.yml` are checked too,
including the one invariant the whole publishing story rests on: `PGDATA` has
to sit outside the path the base images declare as a `VOLUME`, or the restore's
writes are discarded and the published image ships a perfectly valid,
completely empty database.

Everything that writes gets a **throwaway database**, created from `template0`
and dropped afterwards. A scratch schema would not be enough: every function
here addresses its tables unqualified, so with `public` still on the search path
an unqualified `TRUNCATE` in `rebuild_bm25_index` would fall through to the
published table whenever the scratch copy did not exist yet. The published
golden pairs and embeddings are what the v3_2 images ship, and nothing in the
suite can reach them.

The vector tests use synthetic unit vectors rather than calling bge-m3, so the
distances are predictable instead of merely plausible; the real embedding round
trip is covered by `tests/agent/test_examples_live.py`.

`sentence-transformers` is commented out of `requirements.txt` — it is only
needed for `--backend sentence-transformers` and pulls in torch.
