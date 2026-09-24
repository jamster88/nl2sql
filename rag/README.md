# RAG Data Pipeline

Loads the documents in [`knowledge/`](../knowledge), semantically chunks them,
stores the chunks in one Postgres instance, embeds them with BGE-M3, and stores
the vectors in a separate pgvector instance.

It also loads the 45 golden question/SQL pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md)
-- as a relational table rather than as chunked prose -- and embeds two of their
columns separately, which is what the agent's ensemble retriever searches.

```bash
./run_all.sh          # everything, from nothing to a queryable vector store
./run_update.sh       # after editing a document: re-chunk and re-embed only what changed
./start_rag_db.sh     # bring up just the vector store, for the RAG to query

python 05_load_golden_pairs.py     # golden pairs -> context store, with a BM25 index
python 06_embed_golden_pairs.py    # their question and reasoning columns -> pgvector
```

Both databases stay off port 5432 so they never collide with the retail
testing database: the chunk store is on **5433**, the vector store on **5434**.

## The six steps

| Step | File | What it does |
|---|---|---|
| 1 | `01_start_chunk_db.sh` | Starts the Postgres instance that holds chunked text |
| 2 | `02_chunk_document.py` | Loads, parses, chunks a document; writes it to that instance |
| 3 | `03_start_vector_db.sh` | Starts the pgvector instance |
| 4 | `04_embed_document.py` | Reads chunks, embeds them, writes vectors to pgvector |
| 5 | `05_load_golden_pairs.py` | Parses the golden pairs into a table and builds a BM25 index over their keywords |
| 6 | `06_embed_golden_pairs.py` | Embeds the `question` and `reasoning_target` columns into two separate pgvector tables |
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

The 45 pairs are **not** chunked the way the `knowledge/` documents are. Every
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
| `golden_pairs` | the 45 rows, plus a generated `keywords_tsv` column |
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
volume path are invisible to `docker commit` and to any image built from the
container, so a cluster living there could never be published.

Day to day, the data lives in named volumes (`nl2sql-rag-chunkdb-data`,
`nl2sql-rag-vectordb-data`) and survives `stop`, `start`, `restart` and
`docker compose down`. Only `down -v` destroys it.

To publish a database as a self-contained image:

```bash
./publish_db_image.sh vectordb mcfaddja/nl2sql-rag-vectordb:v3
./publish_db_image.sh chunkdb  mcfaddja/nl2sql-rag-chunkdb:v3
# or, as part of a full run:
./run_all.sh --publish mcfaddja --tag v3
```

| Tag | Contents |
|---|---|
| `nl2sql-rag-vectordb:v3` | knowledge collections **and** both golden-pair vector tables |
| `nl2sql-rag-chunkdb:v3` | knowledge chunks **and** `golden_pairs` with its BM25 index |
| `nl2sql-rag-vectordb:v1` | knowledge collections only -- what the v2 agent searches |

That script stops the container for a clean shutdown checkpoint, tars the
volume, bakes the tar into a new image layer, restarts the container, and
pushes. The published image carries the data with it:

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

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r rag/requirements.txt
```

## Tests

```bash
pytest tests/rag --run-docker
```

265 tests: the parser against the real document, the BM25 ranking compared
score for score against an independent Okapi implementation, the pgvector
storage layer, both loader scripts as command line programs, and the seven
shell scripts on this page.

Those last ones are run rather than read. Each gets a throwaway copy of `rag/`
with a fake `docker` on PATH that records every call and returns scripted
results, so what is asserted is the decision: which service is started, which
image is pulled and when, whether a container is stopped before its volume is
snapshotted and restarted afterwards, and which `die` a bad argument reaches.
They were untested until they were not, and writing the tests turned up three
defects -- see the repository README's
[coverage section](../README.md#the-parts-a-coverage-report-cannot-see).

The two images and `docker-compose.yml` are checked too, including the one
invariant the whole publishing story rests on: `PGDATA` has to sit outside the
path the base images declare as a `VOLUME`, or the published image ships a
perfectly valid, completely empty database.

Everything that writes gets a **throwaway database**, created from `template0`
and dropped afterwards. A scratch schema would not be enough: every function
here addresses its tables unqualified, so with `public` still on the search path
an unqualified `TRUNCATE` in `rebuild_bm25_index` would fall through to the
published table whenever the scratch copy did not exist yet. The published
golden pairs and embeddings are what the v3 images ship, and nothing in the
suite can reach them.

The vector tests use synthetic unit vectors rather than calling bge-m3, so the
distances are predictable instead of merely plausible; the real embedding round
trip is covered by `tests/agent/test_examples_live.py`.

`sentence-transformers` is commented out of `requirements.txt` — it is only
needed for `--backend sentence-transformers` and pulls in torch.
