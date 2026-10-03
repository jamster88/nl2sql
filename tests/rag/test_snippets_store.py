"""`07_load_snippets.py` and the snippet store it fills.

The loader is the only way a snippet reaches the agent, so its argument
handling is checked offline and its writes against a throwaway database in
the real store: the rows, the keyword matcher, the reader role, the vectors
and the load record -- and the agent's own retriever reading all of it back
as that role, which is the end-to-end claim the two halves make together.

The embedder is deterministic, as in test_pipeline_cli.py: a real model makes
the numbers plausible, a synthetic one makes them checkable. The keyword
matcher needs no embedder at all, and is checked against the real document.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
DOCUMENT = REPO_ROOT / "context_questions" / "sql_snippets.md"
SNIPPETS = len(re.findall(r"^## S\d{2,} - ", DOCUMENT.read_text(), re.M))

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), RAG_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def loader():
    return _load("07_load_snippets.py")


class FakeEmbedder:
    """Vectors from the words in a text, so near means sharing words."""

    model_name = "bge-m3"
    dimension = 16

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []
        self.checked = 0

    def check(self) -> None:
        self.checked += 1
        if self.fail:
            raise RuntimeError(self.fail)

    def vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for word in re.findall(r"[a-z]+", text.lower()):
            vec[sum(map(ord, word)) % self.dimension] += 1.0
        return vec

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self.vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


# ---------------------------------------------------------------------------
# Offline: what the flags say
# ---------------------------------------------------------------------------


def test_the_defaults_are_the_document_the_stores_port_and_the_agents_role(loader, monkeypatch):
    for var in ("SNIPPETS_DB_URL", "SNIPPETS_READER_USER", "SNIPPETS_READER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    args = loader.parse_args([])
    assert Path(args.document) == DOCUMENT
    assert args.db_url == "postgresql://snippets:snippets@localhost:5438/nl2sql_snippets"
    assert (args.reader_role, args.reader_password) == ("snippets_reader", "snippets_reader")
    assert args.model == "bge-m3"


def test_the_store_and_the_role_are_read_from_the_environment(loader, monkeypatch):
    monkeypatch.setenv("SNIPPETS_DB_URL", "postgresql://o:o@store:5432/s")
    monkeypatch.setenv("SNIPPETS_READER_USER", "reader")
    monkeypatch.setenv("SNIPPETS_READER_PASSWORD", "secret")
    args = loader.parse_args([])
    assert (args.db_url, args.reader_role, args.reader_password) == ("postgresql://o:o@store:5432/s", "reader", "secret")


def test_a_dry_run_parses_everything_and_writes_nothing(loader, capsys):
    assert loader.main(["--dry-run", "--db-url", "postgresql://nobody@127.0.0.1:1/x"]) == 0
    out = capsys.readouterr().out
    assert f"-> sql_snippets: {SNIPPETS} snippets (" in out
    assert "dry run: nothing written" in out
    assert re.search(r"\(\d+ join, \d+ filter, \d+ measure, \d+ dimension\)", out)


def test_a_missing_document_fails_loudly(loader, tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        loader.main([str(tmp_path / "absent.md")])


def test_the_script_exits_with_the_code_its_main_returns():
    import runpy

    saved = sys.argv
    sys.argv = ["07_load_snippets.py", "--help"]
    try:
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(RAG_DIR / "07_load_snippets.py"), run_name="__main__")
    finally:
        sys.argv = saved
    assert raised.value.code == 0


# ---------------------------------------------------------------------------
# Against a throwaway database in the real store
# ---------------------------------------------------------------------------


def read(conn, statement: str, params=None):
    try:
        return conn.execute(statement, params).fetchall()
    finally:
        conn.commit()


def as_role(url: str, role: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, f"{role}:{password}@{host}", parts.path, "", ""))


def load(loader, conn, role, monkeypatch, embedder=None, *extra) -> int:
    embedder = embedder or FakeEmbedder()
    monkeypatch.setattr(loader, "build_embedder", lambda *a, **k: embedder)
    return loader.main(
        ["--db-url", conn.scratch_url, "--reader-role", role, "--reader-password", "pw", *extra]
    )


@pytest.mark.docker
def test_a_load_writes_every_snippet_its_vector_and_the_document_it_came_from(
    loader, snippet_store, monkeypatch, capsys
):
    from ragproc import snippets as sn

    conn, role = snippet_store
    assert load(loader, conn, role, monkeypatch) == 0
    out = capsys.readouterr().out
    assert f"{SNIPPETS} rows written, 0 stale rows removed" in out
    assert f"vectors -> sql_snippet_vectors: {SNIPPETS} embedded, 0 already current" in out
    assert f"role {role} can read the store and write nothing" in out

    [(rows,)] = read(conn, "SELECT count(*) FROM sql_snippets")
    [(vectors,)] = read(conn, "SELECT count(*) FROM sql_snippet_vectors")
    [(digest, count, model)] = read(conn, "SELECT document_hash, snippets, embedding_model FROM sql_snippet_source")
    assert rows == vectors == count == SNIPPETS
    assert digest == sn.document_hash(DOCUMENT.read_bytes())
    assert model == "bge-m3"


@pytest.mark.docker
def test_a_second_load_embeds_nothing_and_never_asks_the_embedding_host(loader, snippet_store, monkeypatch, capsys):
    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    capsys.readouterr()
    down = FakeEmbedder(fail="cannot reach ollama")
    assert load(loader, conn, role, monkeypatch, down) == 0
    assert f"0 embedded, {SNIPPETS} already current" in capsys.readouterr().out
    assert down.checked == 0 and down.calls == []


@pytest.mark.docker
def test_an_edit_re_embeds_that_snippet_and_a_removal_drops_its_row_and_vector(
    loader, snippet_store, monkeypatch, tmp_path, capsys
):
    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    text = DOCUMENT.read_text()
    edited = text.replace("Revenue actually collected", "Money actually collected", 1)
    # S32 is the last section; cut it off to remove it.
    edited = edited[: edited.index("## S32 - ")]
    path = tmp_path / "sql_snippets.md"
    path.write_text(edited)
    capsys.readouterr()

    embedder = FakeEmbedder()
    monkeypatch.setattr(loader, "build_embedder", lambda *a, **k: embedder)
    assert loader.main([str(path), "--db-url", conn.scratch_url, "--reader-role", role, "--reader-password", "pw"]) == 0
    out = capsys.readouterr().out
    assert "1 stale rows removed" in out
    assert "1 embedded" in out
    [[embedded]] = embedder.calls
    assert embedded.startswith("Net sales. Money actually collected")
    assert read(conn, "SELECT count(*) FROM sql_snippet_vectors WHERE snippet_id = 'S32'") == [(0,)]


@pytest.mark.docker
def test_an_embedding_host_that_is_down_loads_the_rows_and_records_no_document(
    loader, snippet_store, monkeypatch, capsys
):
    """The load record is what the next start compares, so a load that could
    not embed must not claim the document: then the next start finishes it."""
    conn, role = snippet_store
    assert load(loader, conn, role, monkeypatch, FakeEmbedder(fail="cannot reach ollama")) == 1
    out = capsys.readouterr().out
    assert "vectors -> sql_snippet_vectors: FAILED: cannot reach ollama" in out
    assert read(conn, "SELECT count(*) FROM sql_snippets") == [(SNIPPETS,)]
    assert read(conn, "SELECT count(*) FROM sql_snippet_source") == [(0,)]


@pytest.mark.docker
def test_no_embed_loads_the_rows_and_leaves_the_vectors_for_the_next_load(loader, snippet_store, monkeypatch, capsys):
    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    assert load(loader, conn, role, monkeypatch, None, "--no-embed") == 0
    assert "vectors: skipped (--no-embed)" in capsys.readouterr().out
    assert read(conn, "SELECT count(*) FROM sql_snippet_source") == [(0,)]


@pytest.mark.docker
def test_the_probe_shows_both_halves(loader, snippet_store, monkeypatch, capsys):
    conn, role = snippet_store
    assert load(loader, conn, role, monkeypatch, None, "--probe", "net sales by department") == 0
    out = capsys.readouterr().out
    assert "by keyword for 'net sales by department':" in out
    assert re.search(r"S19  Net sales  \[net sales", out)
    assert "by meaning for 'net sales by department':" in out
    load(loader, conn, role, monkeypatch, None, "--probe", "zzzz qqqq")
    assert "(no keyword phrase matched)" in capsys.readouterr().out


@pytest.mark.docker
def test_the_reader_role_can_read_everything_and_write_nothing(loader, snippet_store, monkeypatch):
    import psycopg

    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    with psycopg.connect(as_role(conn.scratch_url, role, "pw")) as reader:
        assert reader.execute("SELECT count(*) FROM sql_snippet_vectors").fetchone() == (SNIPPETS,)
        assert reader.execute("SELECT count(*) FROM sql_snippets_keyword_match('net sales')").fetchone()[0] > 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("DELETE FROM sql_snippets")
    # A second load resets the password rather than failing on an existing role.
    monkeypatch.setattr(loader, "build_embedder", lambda *a, **k: FakeEmbedder())
    loader.main(["--db-url", conn.scratch_url, "--reader-role", role, "--reader-password", "changed"])
    with psycopg.connect(as_role(conn.scratch_url, role, "changed")) as reader:
        assert reader.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.docker
@pytest.mark.parametrize(
    "question,expected,absent",
    [
        ("What were our total net sales in fiscal year 2025?", {"S19", "S09", "S01"}, set()),
        ("How many stores are there?", set(), {"S03", "S13"}),
        ("What share of our sales comes from store brands?", {"S25", "S13"}, set()),
        ("What were sales on weekends versus weekdays last year?", {"S31", "S12", "S10"}, set()),
        ("What is the click-through rate by ad channel?", {"S26", "S05"}, set()),
    ],
)
def test_the_keyword_matcher_matches_phrases_not_words(loader, snippet_store, monkeypatch, question, expected, absent):
    """A phrase matches only when all its words are in the question: "stores"
    does not reach the store-brand snippets, "store brands" does."""
    from ragproc import snippets as sn

    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    matched = {row[0] for row in sn.search_keywords(conn, question, limit=40)}
    conn.commit()
    assert expected <= matched
    assert not (absent & matched)


@pytest.mark.docker
def test_the_agents_retriever_reads_the_store_as_the_reader_role(loader, snippet_store, monkeypatch):
    """The two halves together: what the loader wrote, the agent finds."""
    from nl2sql_agent.snippets import SnippetLibrary, usable

    conn, role = snippet_store
    load(loader, conn, role, monkeypatch)
    url = as_role(conn.scratch_url, role, "pw").replace("postgresql://", "postgresql+psycopg://")
    library = SnippetLibrary(url, FakeEmbedder(), top_k=5)
    found = library.search("What were our total net sales in fiscal year 2025?")
    assert found and found[0].snippet_id in {"S19", "S09", "S01"}
    assert {"S19", "S09"} <= {s.snippet_id for s in found if s.matched}
    assert library.count() == SNIPPETS
    in_scope = usable(found, ["fact_pos_retail_sales", "dim_date"])
    assert {"S19", "S09"} <= {s.snippet_id for s in in_scope}
    assert all(set(s.table_list) <= {"fact_pos_retail_sales", "dim_date"} for s in in_scope)
    library._engine.dispose()
