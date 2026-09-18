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
