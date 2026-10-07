"""nl2sql_ops.report: what each database holds, as lines the scripts relay."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from nl2sql_ops import report as rp
from nl2sql_ops.settings import OpsSettings

from .conftest import FakeConn


@pytest.fixture
def settings(tmp_path, monkeypatch) -> OpsSettings:
    for name in ("NL2SQL_SOCKETS_DIR", "NL2SQL_GOLDEN_DOCUMENT", "NL2SQL_SNIPPETS_DOCUMENT"):
        monkeypatch.delenv(name, raising=False)
    golden = tmp_path / "translated_questions.md"
    golden.write_text("# Golden\n\n## Q01 - one\n\n## Q02 - two\n\n## Q100 - three\n")
    snippets = tmp_path / "sql_snippets.md"
    snippets.write_text("# Snippets\n")
    return replace(OpsSettings.from_env(), sockets=tmp_path / "sockets", golden_document=golden,
                   snippets_document=snippets)


def wire(monkeypatch, up: dict[str, FakeConn]) -> list[tuple[str, str, str]]:
    """Each socket in `up` is listening and answers with its fake."""
    opened = []

    def connect(sockets, name, *, user, dbname):
        opened.append((name, user, dbname))
        return up[name]

    monkeypatch.setattr(rp, "connect", connect)
    monkeypatch.setattr(rp, "listening", lambda sockets, name: name in up)
    return opened


def retail(rows=194101, trgm=1, extra=0) -> FakeConn:
    return FakeConn({"fact_pos_retail_sales": [(rows,)], "pg_extension": [(trgm,)], "role_table_grants": [(extra,)]})


def full(monkeypatch, *, chunks=53, vectors=3, ddl=20, pairs=3, snippets=32, meanings=32):
    return wire(monkeypatch, {
        "retail": retail(),
        # ddl_index_embeddings first: it is one of the `_embeddings` tables too.
        "vector": FakeConn({"ddl_index_embeddings": [(ddl,)], "_embeddings": [(chunks,)],
                            "golden_pair_question_vectors": [(vectors,)]}),
        "context": FakeConn({"golden_pairs": [(pairs,)]}),
        "stores": FakeConn({"sql_snippet_vectors": [(meanings,)], "sql_snippets": [(snippets,)]}),
    })


def text(lines) -> list[str]:
    return [str(line) for line in lines]


def test_a_full_stack_reports_every_store_and_the_pipeline(settings, monkeypatch):
    opened = full(monkeypatch)
    assert text(rp.report(settings)) == [
        "STEP Checking what is actually in each database",
        "STATE retail_rows=194101",
        "INFO retail dataset: 194101 sales rows",
        "INFO knowledge base: 53 embedded chunks",
        "INFO worked examples: 3 golden pairs, 3 embedded questions",
        "INFO SQL snippets: 32 snippets, 32 embedded meanings",
        "INFO schema index: 20 DDL chunks (table selection needs no model call)",
        "STEP Checking the multi-agent pipeline",
        "INFO literal matching: pg_trgm installed (trigram search)",
        "INFO least privilege: the agent's role holds SELECT and nothing else",
    ]
    assert opened == [
        ("retail", "postgres", "nl2sql_retail"),
        ("vector", "ragproc", "nl2sql_vectors"),
        ("context", "ragproc", "nl2sql_chunks"),
        ("stores", "snippets", "nl2sql_snippets"),
    ], "each store as its own owner, the retail database as its superuser"


def test_empty_stores_and_a_drifted_pipeline_are_warned_about(settings, monkeypatch):
    wire(monkeypatch, {
        "retail": retail(rows=0, trgm=0, extra=2),
        "vector": FakeConn(),
        "context": FakeConn({"golden_pairs": [(2,)]}),
        "stores": FakeConn(),
    })
    lines = text(rp.report(settings))
    assert "STATE retail_rows=0" in lines
    assert "WARN the retail database is up but has no sales rows in it." in lines
    assert "WARN the vector store is up but holds no embedded chunks." in lines
    assert "WARN the context store holds 2 golden pairs and the vector store" in lines
    assert "WARN the context store holds 2 golden pairs, and the question document 3." in lines
    assert "WARN the snippet store holds no SQL snippets, so the generator is shown none." in lines
    assert "WARN the vector store has no ddl_index_embeddings collection." in lines
    assert "WARN pg_trgm is not installed; literal matching falls back to difflib." in lines
    assert "WARN the agent's role holds 2 non-SELECT grants; it should hold none." in lines


def test_without_the_knowledge_stores_only_the_retail_database_is_read(settings, monkeypatch):
    opened = wire(monkeypatch, {"retail": retail()})
    lines = text(rp.report(settings))
    assert [name for name, _, _ in opened] == ["retail"]
    assert not any("knowledge base" in line for line in lines)


def test_without_the_runtime_stores_the_snippets_are_not_asked_about(settings, monkeypatch):
    up = {"retail": retail(), "vector": FakeConn({"_embeddings": [(1,)]}), "context": FakeConn()}
    wire(monkeypatch, up)
    assert not any("snippet" in line for line in text(rp.report(settings)))


def test_a_question_document_that_is_not_there_is_not_compared(settings, monkeypatch):
    full(monkeypatch, pairs=99)
    missing = replace(settings, golden_document=Path("/nonexistent/translated_questions.md"))
    assert not any("question document" in line for line in text(rp.report(missing)))


def test_a_table_that_is_not_there_counts_as_none():
    conn = FakeConn(fail={"golden_pairs": psycopg.errors.UndefinedTable("no such table")})
    assert rp._count(conn, "SELECT count(*) FROM golden_pairs") == 0
    assert rp._count(FakeConn({"x": [(None,)]}), "SELECT x") == 0


def test_the_snippet_store_is_behind_until_it_was_loaded_from_this_document(settings, monkeypatch):
    digest = hashlib.sha256(settings.snippets_document.read_bytes()).hexdigest()
    wire(monkeypatch, {"stores": FakeConn({"sql_snippet_source": [(digest,)]})})
    assert rp.snippets_state(settings) == "current"
    wire(monkeypatch, {"stores": FakeConn({"sql_snippet_source": [("0ld",)]})})
    assert rp.snippets_state(settings) == "behind"
    wire(monkeypatch, {"stores": FakeConn()})
    assert rp.snippets_state(settings) == "behind", "never loaded"
    wire(monkeypatch, {"stores": FakeConn(fail={"sql_snippet_source": psycopg.errors.UndefinedTable("x")})})
    assert rp.snippets_state(settings) == "behind", "a fresh store"


def test_the_snippet_state_is_unknown_without_the_document_or_the_store(settings, monkeypatch):
    wire(monkeypatch, {})
    assert rp.snippets_state(settings) == "unknown"
    wire(monkeypatch, {"stores": FakeConn()})
    assert rp.snippets_state(replace(settings, snippets_document=Path("/nonexistent"))) == "unknown"
