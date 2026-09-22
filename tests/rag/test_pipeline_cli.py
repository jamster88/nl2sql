"""The two loader scripts as command line programs.

They are the only way the golden pairs get into either store, so their argument
handling is part of the contract: a default that silently points at the wrong
database, or a --dry-run that writes anyway, would be found by nobody until it
had already happened.

Offline -- argument parsing and the dry-run path touch no database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
DOCUMENT = REPO_ROOT / "context_questions" / "translated_questions.md"

if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")


def _load(filename: str):
    """Import a numbered script, whose name is not a legal module name."""
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), RAG_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def loader():
    return _load("05_load_golden_pairs.py")


@pytest.fixture(scope="module")
def embedder():
    return _load("06_embed_golden_pairs.py")


# ---------------------------------------------------------------------------
# 05: loading the pairs
# ---------------------------------------------------------------------------


def test_the_loader_defaults_to_the_translated_questions_document(loader):
    """Running it bare is the documented path, so the default has to resolve
    from the rag/ directory to the document one level up.
    """
    args = loader.parse_args([])
    assert Path(args.document) == DOCUMENT
    assert Path(args.document).is_file()


def test_the_loader_defaults_to_the_context_store_not_the_vector_store(loader):
    """The two are different databases on different ports. Crossing them writes
    golden pairs into the vector store and is not obvious from the output.
    """
    assert ":5433/nl2sql_chunks" in loader.parse_args([]).db_url


def test_the_bm25_parameters_are_exposed_and_default_to_okapi_values(loader):
    args = loader.parse_args([])
    assert (args.k1, args.b) == (1.2, 0.75)
    tuned = loader.parse_args(["--k1", "1.6", "--b", "0.4"])
    assert (tuned.k1, tuned.b) == (1.6, 0.4)


def test_a_dry_run_parses_everything_and_writes_nothing(loader, capsys):
    """The db-url default points at a real database, so if --dry-run ever
    stopped short-circuiting, this test would write to it.
    """
    assert loader.main(["--dry-run", "--db-url", "postgresql://nobody@127.0.0.1:1/none"]) == 0
    out = capsys.readouterr().out
    assert "45 pairs across 25 suites" in out
    assert "nothing written" in out


def test_the_dry_run_names_the_eight_columns_it_will_write(loader, capsys):
    loader.main(["--dry-run", "--db-url", "postgresql://nobody@127.0.0.1:1/none"])
    out = capsys.readouterr().out
    for column in ("chunk_id", "type", "tables", "keywords",
                   "question", "reasoning_target", "sql_code", "result"):
        assert column in out


def test_a_missing_document_fails_loudly_rather_than_loading_nothing(loader):
    with pytest.raises(SystemExit, match="not found"):
        loader.main(["/no/such/questions.md", "--dry-run"])


# ---------------------------------------------------------------------------
# 06: embedding them
# ---------------------------------------------------------------------------


def test_the_embedder_reads_the_context_store_and_writes_the_vector_store(embedder):
    """It spans both databases -- pairs come from 5433, vectors go to 5434 --
    which is the one thing about this script that is easy to get backwards.
    """
    args = embedder.parse_args([])
    assert ":5433/nl2sql_chunks" in args.chunk_db_url
    assert ":5434/nl2sql_vectors" in args.vector_db_url


def test_both_fields_are_embedded_by_default(embedder):
    """The ensemble weights them separately, so embedding only one silently
    disables a whole retrieval leg.
    """
    assert embedder.FIELDS == ("question", "reasoning_target")
    assert embedder.parse_args([]).field is None


def test_a_single_field_can_be_selected_and_repeated(embedder):
    assert embedder.parse_args(["--field", "question"]).field == ["question"]
    both = embedder.parse_args(["--field", "question", "--field", "reasoning_target"]).field
    assert both == ["question", "reasoning_target"]


def test_an_unknown_field_is_refused(embedder):
    with pytest.raises(SystemExit):
        embedder.parse_args(["--field", "sql_code"])


def test_the_embedding_model_defaults_to_the_one_the_stores_were_built_with(embedder):
    """A different model puts the query in a different vector space, and
    retrieval returns confident nonsense rather than failing.
    """
    assert embedder.parse_args([]).model == "bge-m3"


def test_embedding_an_empty_context_store_says_to_run_the_loader_first(embedder, monkeypatch):
    monkeypatch.setattr(embedder.gp, "connect", lambda url: _FakeConn())
    monkeypatch.setattr(embedder.gp, "load_pairs", lambda conn: [])
    with pytest.raises(SystemExit, match="05_load_golden_pairs"):
        embedder.main([])


class _FakeConn:
    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# What the scripts actually do, against a throwaway database
# ---------------------------------------------------------------------------
#
# Everything above is argument handling and the dry-run path, which is the
# half that can be checked offline. This is the other half: the writes. They
# are the only way the golden pairs reach either store, so "the flags parse"
# is not much of a guarantee on its own.


def read(conn, statement: str):
    """Read, then end the transaction before anything else needs the table.

    Each script opens its *own* connection, and loading takes an ACCESS
    EXCLUSIVE lock to add the generated tsvector column. A `SELECT` left
    open on the fixture's connection holds that lock off indefinitely, and
    the two sit waiting for each other until something kills the run -- which
    is exactly what happened while these tests were being written.
    """
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.commit()


@pytest.mark.docker
def test_the_loader_writes_every_pair_and_builds_the_keyword_index(loader, chunk_conn, capsys):
    """One call, and the context store holds the corpus the ensemble's
    keyword retriever runs against.
    """
    from ragproc import golden_pairs as gp

    assert loader.main(["--db-url", chunk_conn.scratch_url]) == 0
    output = capsys.readouterr().out

    [(written,)] = read(chunk_conn, f"SELECT count(*) FROM {gp.TABLE}")
    assert written == 45, f"the context store holds {written} pairs"
    assert f"{written} rows written" in output
    assert "0 stale rows removed" in output

    # The keyword half of the ensemble reads these three, not the pairs.
    [(terms,)] = read(chunk_conn, f"SELECT count(*) FROM {gp.TERMS_TABLE}")
    [(docs,)] = read(chunk_conn, f"SELECT count(*) FROM {gp.DOCS_TABLE}")
    assert terms > 0 and docs == written, "the BM25 index was never built"
    assert "distinct terms" in output


@pytest.mark.docker
def test_loading_twice_updates_rather_than_duplicates(loader, chunk_conn):
    """Re-running is how a corrected question gets in, so it has to be an
    upsert. A second insert would double every row and quietly halve every
    BM25 score.
    """
    from ragproc import golden_pairs as gp

    loader.main(["--db-url", chunk_conn.scratch_url])
    [(first,)] = read(chunk_conn, f"SELECT count(*) FROM {gp.TABLE}")
    loader.main(["--db-url", chunk_conn.scratch_url])
    [(second,)] = read(chunk_conn, f"SELECT count(*) FROM {gp.TABLE}")
    assert first == second == 45


@pytest.mark.docker
def test_the_loaders_probe_ranks_pairs_by_keyword_overlap(loader, chunk_conn, capsys):
    """`--probe` is how someone checks the index is worth anything without
    starting the agent, so it has to return ranked rows rather than just not
    fail.
    """
    assert loader.main(["--db-url", chunk_conn.scratch_url, "--probe", "gross margin by department"]) == 0
    output = capsys.readouterr().out
    assert "BM25 probe 'gross margin by department'" in output
    assert "(no keyword overlap)" not in output


@pytest.mark.docker
def test_a_probe_that_matches_nothing_says_so_rather_than_printing_nothing(loader, chunk_conn, capsys):
    assert loader.main(
        ["--db-url", chunk_conn.scratch_url, "--probe", "zzzzqqqq nonexistent lexeme"]
    ) == 0
    assert "(no keyword overlap)" in capsys.readouterr().out


class _FakeEmbedder:
    """Deterministic vectors, so the distances below are arithmetic.

    The storage tests in this directory work the same way: a real embedding
    model makes the numbers plausible, a synthetic one makes them checkable.
    """

    model_name = "fake-embed"
    dimension = 8

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float((len(t) + i) % 7) for i in range(self.dimension)] for t in texts]


@pytest.mark.docker
def test_the_embedder_writes_one_vector_table_per_field(
    loader, embedder, chunk_conn, vector_conn, monkeypatch, capsys
):
    """Two tables, not one: the ensemble scores question similarity and
    reasoning-target similarity separately and weights them differently, and
    it cannot do that if they share a table.
    """
    from ragproc import golden_vectors as gv

    loader.main(["--db-url", chunk_conn.scratch_url])
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedder, "build_embedder", lambda *a, **k: fake)

    assert embedder.main(
        ["--chunk-db-url", chunk_conn.scratch_url, "--vector-db-url", vector_conn.scratch_url]
    ) == 0
    output = capsys.readouterr().out

    assert "45 golden pairs in the context store" in output
    for field in embedder.FIELDS:
        stored = gv.current_state(vector_conn, field)
        vector_conn.commit()
        assert len(stored) == 45, f"{field} has {len(stored)} vectors"
        assert field in output


@pytest.mark.docker
def test_only_the_named_field_is_embedded_when_one_is_chosen(
    loader, embedder, chunk_conn, vector_conn, monkeypatch
):
    from ragproc import golden_vectors as gv

    loader.main(["--db-url", chunk_conn.scratch_url])
    monkeypatch.setattr(embedder, "build_embedder", lambda *a, **k: _FakeEmbedder())
    embedder.main([
        "--chunk-db-url", chunk_conn.scratch_url,
        "--vector-db-url", vector_conn.scratch_url,
        "--field", "question",
    ])
    question = gv.current_state(vector_conn, "question")
    vector_conn.commit()
    assert len(question) == 45
    assert gv.current_state(vector_conn, "reasoning_target") == {}
    vector_conn.commit()


@pytest.mark.docker
def test_re_running_the_embedder_embeds_nothing_that_has_not_changed(
    loader, embedder, chunk_conn, vector_conn, monkeypatch, capsys
):
    """Embedding is the expensive step -- 90 model calls against a host that
    is usually someone else's machine. Being incremental is the difference
    between re-running it casually and not re-running it at all.
    """
    loader.main(["--db-url", chunk_conn.scratch_url])
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedder, "build_embedder", lambda *a, **k: fake)
    args = ["--chunk-db-url", chunk_conn.scratch_url, "--vector-db-url", vector_conn.scratch_url]

    embedder.main(args)
    after_first = len(fake.calls)
    capsys.readouterr()

    embedder.main(args)
    assert len(fake.calls) == after_first, "it re-embedded rows nothing had changed"
    assert "0 embedded, 45 already current" in capsys.readouterr().out

    embedder.main([*args, "--force"])
    assert len(fake.calls) > after_first, "--force embedded nothing"


@pytest.mark.docker
def test_the_embedders_probe_reports_the_nearest_pairs(
    loader, embedder, chunk_conn, vector_conn, monkeypatch, capsys
):
    loader.main(["--db-url", chunk_conn.scratch_url])
    monkeypatch.setattr(embedder, "build_embedder", lambda *a, **k: _FakeEmbedder())
    embedder.main([
        "--chunk-db-url", chunk_conn.scratch_url,
        "--vector-db-url", vector_conn.scratch_url,
        "--probe", "gross margin by department",
    ])
    output = capsys.readouterr().out
    assert "nearest by question for 'gross margin by department'" in output
    assert "nearest by reasoning_target" in output


@pytest.mark.docker
def test_an_embedder_that_cannot_answer_is_checked_before_any_writing(
    loader, embedder, chunk_conn, vector_conn, monkeypatch
):
    """`check()` exists so an unreachable Ollama fails before the script has
    opened the vector store and started a transaction it cannot finish.
    """

    class _Unreachable(_FakeEmbedder):
        def check(self):
            raise RuntimeError("cannot reach ollama")

    loader.main(["--db-url", chunk_conn.scratch_url])
    monkeypatch.setattr(embedder, "build_embedder", lambda *a, **k: _Unreachable())
    with pytest.raises(RuntimeError, match="cannot reach ollama"):
        embedder.main([
            "--chunk-db-url", chunk_conn.scratch_url,
            "--vector-db-url", vector_conn.scratch_url,
        ])


# ---------------------------------------------------------------------------
# As scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", ["05_load_golden_pairs.py", "06_embed_golden_pairs.py"])
def test_each_script_exits_with_the_code_its_main_returns(script: str):
    """`python rag/05_load_golden_pairs.py` is how rag/README.md says to run
    these, and the line that carries the exit code out is the one a pipeline
    stops on.
    """
    import runpy

    saved = sys.argv
    sys.argv = [script, "--help"]
    try:
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(RAG_DIR / script), run_name="__main__")
    finally:
        sys.argv = saved
    assert raised.value.code == 0
