"""`ragproc.loaders`: steps 5, 6 and 7 as functions (V6-27), without a store.

The stores' own functions are faked here, so what is under test is the
order of the work and the report: which rows were written, what was
embedded, and when the snippet store may say it holds a document. The same
functions against real stores are the scripts' live tests
(`test_pipeline_cli.py`, `test_snippets_store.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ragproc import golden_pairs as gp
from ragproc import golden_vectors as gv
from ragproc import loaders
from ragproc import snippets as sn

REPO = Path(__file__).resolve().parent.parent.parent
PAIRS = REPO / "context_questions" / "translated_questions.md"
SNIPPETS = REPO / "context_questions" / "sql_snippets.md"


class Conn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = False
        self.rolled_back = False
        self.asked: list[tuple] = []

    def execute(self, sql, params=None):
        self.asked.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class Embedder:
    model_name = "bge-m3"
    dimension = 4

    def __init__(self, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.batches: list[list[str]] = []
        self.checked = 0

    def check(self):
        self.checked += 1

    def embed(self, texts):
        self.batches.append(list(texts))
        if self.fail_on is not None and len(self.batches) >= self.fail_on:
            raise ConnectionError("embedding host went away")
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


# --- step 5 ------------------------------------------------------------------------


@pytest.fixture
def pairs_store(monkeypatch):
    conn = Conn(rows=[("C1", "Q01", 1.5, "a title")])
    opened = []
    monkeypatch.setattr(gp, "connect", lambda url, **options: opened.append((url, options)) or conn)
    monkeypatch.setattr(gp, "ensure_tables", lambda c: None)
    monkeypatch.setattr(gp, "upsert_pairs", lambda c, pairs, source_doc: len(pairs))
    monkeypatch.setattr(gp, "delete_missing", lambda c, keep: 2)
    monkeypatch.setattr(gp, "rebuild_bm25_index", lambda c, k1, b: {"documents": 46, "distinct_terms": 9, "avg_doc_len": 3})
    monkeypatch.setattr(gp, "ensure_bm25_function", lambda c: None)
    return conn, opened


def test_the_pairs_are_loaded_and_the_report_says_what_was_done(pairs_store):
    conn, opened = pairs_store
    report = loaders.load_golden_pairs(PAIRS, "postgresql://chunks", probe="gross margin")
    count = len(gp.parse_document(PAIRS))
    assert (report.pairs, report.written, report.removed) == (count, count, 2)
    assert opened == [("postgresql://chunks", {"connect_timeout": loaders.CONNECT_TIMEOUT})]
    assert report.probe == [("C1", "Q01", 1.5, "a title")] and conn.closed
    assert f"{count} pairs across {report.suites} suites; {count} rows written, 2 stale rows removed" in report.summary()


def test_a_dry_run_parses_and_connects_to_nothing(pairs_store):
    _, opened = pairs_store
    report = loaders.load_golden_pairs(PAIRS, "postgresql://chunks", dry_run=True)
    assert report.pairs > 0 and report.written == 0 and opened == []


def test_without_a_probe_nothing_is_searched(pairs_store):
    conn, _ = pairs_store
    assert loaders.load_golden_pairs(PAIRS, "postgresql://chunks").probe == [] and conn.asked == []


# --- step 6 ------------------------------------------------------------------------


PAIR = {"chunk_id": "C1", "pair_id": "Q01", "ordinal": 1, "question": "q", "reasoning_target": "r", "content_hash": "h"}


@pytest.fixture
def vector_store(monkeypatch):
    written: list[tuple] = []
    state = {"question": {}, "reasoning_target": {"C1": ("h", "bge-m3")}}
    monkeypatch.setattr(gp, "connect", lambda url, **options: Conn())
    monkeypatch.setattr(gp, "load_pairs", lambda conn: [dict(PAIR), dict(PAIR, chunk_id="C2", pair_id="Q02")])
    monkeypatch.setattr(gv, "connect", lambda url, **options: Conn())
    monkeypatch.setattr(gv, "ensure_table", lambda conn, field, dimension: f"golden_{field}_vectors")
    monkeypatch.setattr(gv, "current_state", lambda conn, field: state[field])
    monkeypatch.setattr(gv, "upsert", lambda conn, field, records, model: written.append((field, records)) or len(records))
    monkeypatch.setattr(gv, "delete_missing", lambda conn, field, keep: 0)
    monkeypatch.setattr(gv, "search", lambda conn, field, vector, limit: [{"pair_id": "Q01", "field": field}])
    return written


def test_only_what_changed_is_embedded_unless_forced(vector_store):
    embedder = Embedder()
    report = loaders.embed_golden_pairs("chunks", "vectors", embedder, batch_size=1, probe="margin")
    assert embedder.checked == 1
    assert [(f.field, f.written) for f in report.fields] == [("question", 2), ("reasoning_target", 1)]
    assert report.probe == {
        "question": [{"pair_id": "Q01", "field": "question"}],
        "reasoning_target": [{"pair_id": "Q01", "field": "reasoning_target"}],
    }
    assert "question: 2 embedded, 0 already current" in report.summary()
    forced = loaders.embed_golden_pairs("chunks", "vectors", Embedder(), fields=("reasoning_target",), force=True)
    assert [(f.field, f.written) for f in forced.fields] == [("reasoning_target", 2)]


def test_an_embedder_with_nothing_to_check_is_used_as_it_is(vector_store):
    class Plain:
        model_name, dimension = "m", 4

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    assert loaders.embed_golden_pairs("chunks", "vectors", Plain()).model == "m"


def test_an_empty_context_store_says_which_step_comes_first(monkeypatch):
    monkeypatch.setattr(gp, "connect", lambda url, **options: Conn())
    monkeypatch.setattr(gp, "load_pairs", lambda conn: [])
    with pytest.raises(loaders.NothingToEmbed, match="05_load_golden_pairs"):
        loaders.embed_golden_pairs("chunks", "vectors", Embedder())


# --- step 7 ------------------------------------------------------------------------


@pytest.fixture
def snippet_store(monkeypatch):
    conn = Conn()
    calls: list[str] = []
    stored: dict = {}
    monkeypatch.setattr(sn, "connect", lambda url, **options: conn)
    for name in ("ensure_tables", "ensure_vector_table"):
        monkeypatch.setattr(sn, name, lambda *args, _name=name: calls.append(_name))
    monkeypatch.setattr(sn, "upsert_snippets", lambda c, snippets, source_doc: len(snippets))
    monkeypatch.setattr(sn, "delete_missing", lambda c, keep: 1)
    monkeypatch.setattr(sn, "ensure_reader", lambda c, role, password: calls.append(f"reader {role}/{password}"))
    monkeypatch.setattr(sn, "vector_state", lambda c: stored)
    monkeypatch.setattr(sn, "upsert_vectors", lambda c, batch, vectors, model: len(batch))
    monkeypatch.setattr(sn, "forget_load", lambda c: calls.append("forget_load"))
    monkeypatch.setattr(sn, "record_load", lambda c, **record: calls.append(f"record_load {record['snippets']}"))
    monkeypatch.setattr(sn, "search_keywords", lambda c, text: [("S01", "a join", 1.0, "join")])
    monkeypatch.setattr(sn, "search_vectors", lambda c, vector: [("S01", "a join", 0.9)])
    return conn, calls, stored


def _load(embedder=None, **options):
    return loaders.load_snippets(
        SNIPPETS, "postgresql://snippets", reader_role="reader", reader_password="pw",
        embedder=embedder, model="bge-m3", **options,
    )


def test_every_snippet_is_loaded_embedded_and_the_load_recorded(snippet_store):
    conn, calls, _ = snippet_store
    embedder = Embedder()
    report = _load(lambda: embedder, batch_size=5, probe="net sales")
    count = len(sn.parse_document(SNIPPETS))
    assert (report.snippets, report.written, report.removed, report.embedded) == (count, count, 1, count)
    assert report.complete and report.reader == "reader"
    assert calls == ["ensure_tables", "reader reader/pw", "ensure_vector_table", f"record_load {count}"]
    assert report.keyword_probe and report.meaning_probe and conn.closed
    assert f"{count} embedded, 0 already current" in report.summary()
    assert sum(report.kinds.values()) == count


def test_nothing_changed_needs_no_embedding_host(snippet_store):
    _, calls, stored = snippet_store
    for snippet in sn.parse_document(SNIPPETS):
        stored[snippet.chunk_id] = (snippet.content_hash, "bge-m3")

    def never():
        raise AssertionError("the embedding host was asked for nothing to embed")

    report = _load(never)
    assert report.embedded == 0 and report.complete and calls[-1].startswith("record_load")


def test_a_probe_with_nothing_to_embed_builds_the_embedder_for_the_probe(snippet_store):
    _, _, stored = snippet_store
    for snippet in sn.parse_document(SNIPPETS):
        stored[snippet.chunk_id] = (snippet.content_hash, "bge-m3")
    embedder = Embedder()
    report = _load(lambda: embedder, probe="net sales")
    assert report.meaning_probe and embedder.batches == [["net sales"]]


def test_an_embedding_host_that_fails_leaves_the_rows_and_no_claim_to_the_document(snippet_store):
    conn, calls, _ = snippet_store
    report = _load(lambda: Embedder(fail_on=2), batch_size=1)
    assert report.embedded == 1 and report.embed_error == "embedding host went away"
    assert not report.complete and conn.rolled_back and calls[-1] == "forget_load"
    assert "vectors FAILED: embedding host went away" in report.summary()


def test_without_an_embedder_the_rows_are_loaded_and_the_vectors_left(snippet_store):
    _, calls, _ = snippet_store
    report = _load(None)
    assert report.embedded is None and not report.complete and calls[-1] == "forget_load"
    assert report.summary().endswith("vectors skipped")


def test_a_snippet_dry_run_parses_and_connects_to_nothing(monkeypatch):
    monkeypatch.setattr(sn, "connect", lambda url, **options: pytest.fail("connected"))
    assert _load(None, dry_run=True).written == 0


@pytest.mark.parametrize("module", [gp, gv, sn])
def test_every_store_connection_can_be_given_a_timeout(module, monkeypatch):
    import psycopg

    seen = {}

    class Opened:
        def execute(self, *args):
            return self

        def commit(self):
            pass

    monkeypatch.setattr(psycopg, "connect", lambda url, **options: seen.update(options) or Opened())
    monkeypatch.setattr(gv, "register_vector", lambda conn: None)
    module.connect("postgresql://x", connect_timeout=3)
    assert seen == {"connect_timeout": 3}
    seen.clear()
    module.connect("postgresql://x")
    assert seen == {}
