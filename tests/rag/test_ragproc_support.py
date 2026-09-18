"""Settings and the embedding backend, both of which the golden-pair scripts use.

`document_slug` decides the table a document lands in, and the embedder decides
whether the vectors are comparable with the ones already stored -- two places
where a quiet mistake produces a store that looks fine and retrieves nonsense.

Offline: the Ollama backend is exercised against a stub transport rather than a
live model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")

from ragproc import embedder as embedder_module  # noqa: E402
from ragproc.config import (  # noqa: E402
    DEFAULT_EMBED_DIM,
    DEFAULT_EMBED_MODEL,
    Settings,
    chunk_table,
    document_slug,
    embedding_table,
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_two_stores_default_to_different_ports(monkeypatch):
    """5433 and 5434, and neither is 5432 -- the retail database lives there and
    a pipeline that wrote into it would be writing into the thing under test.
    """
    monkeypatch.delenv("CHUNK_DB_URL", raising=False)
    monkeypatch.delenv("VECTOR_DB_URL", raising=False)
    settings = Settings.from_env()
    assert ":5433/" in settings.chunk_db_url
    assert ":5434/" in settings.vector_db_url
    assert ":5432/" not in settings.chunk_db_url + settings.vector_db_url


def test_every_setting_can_be_overridden_by_the_environment(monkeypatch):
    monkeypatch.setenv("CHUNK_DB_URL", "postgresql://u@h/c")
    monkeypatch.setenv("VECTOR_DB_URL", "postgresql://u@h/v")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("EMBED_DIM", "768")
    monkeypatch.setenv("MAX_CHUNK_TOKENS", "350")
    settings = Settings.from_env()
    assert settings.chunk_db_url == "postgresql://u@h/c"
    assert settings.vector_db_url == "postgresql://u@h/v"
    assert settings.embed_model == "nomic-embed-text"
    assert settings.embed_dim == 768
    assert settings.max_chunk_tokens == 350


def test_the_default_embedding_model_matches_the_published_stores():
    """Both v3 images were built with bge-m3 at 1024 dimensions. A different
    default here produces vectors in another space that still insert happily.
    """
    assert DEFAULT_EMBED_MODEL == "bge-m3"
    assert DEFAULT_EMBED_DIM == 1024


@pytest.mark.parametrize(
    "path,expected",
    [
        ("knowledge/business_index.md", "business_index"),
        ("business_index.md", "business_index"),
        ("/abs/path/Data Dictionary.MARKDOWN", "data_dictionary"),
        ("translated_questions.md", "translated_questions"),
        ("odd-name.txt", "odd_name"),
    ],
)
def test_a_document_slug_is_derived_from_the_filename(path, expected):
    assert document_slug(path) == expected


def test_a_slug_starting_with_a_digit_is_prefixed():
    """It becomes a table name, and an identifier cannot start with a digit."""
    assert document_slug("01_intro.md") == "doc_01_intro"


def test_a_filename_with_no_usable_characters_is_refused():
    with pytest.raises(ValueError, match="Cannot derive a table name"):
        document_slug("---.md")


def test_the_two_table_names_never_collide():
    """`<doc>_chunks` and `<doc>_embeddings` live in different databases, but
    the suffixes are also what the agent's collection discovery keys on.
    """
    assert chunk_table("business_index") == "business_index_chunks"
    assert embedding_table("business_index") == "business_index_embeddings"


# ---------------------------------------------------------------------------
# The Ollama embedding backend
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_the_backend_is_chosen_by_name():
    built = embedder_module.build_embedder("ollama", "bge-m3", "http://h:11434")
    assert isinstance(built, embedder_module.OllamaEmbedder)
    assert built.model_name == "bge-m3"


def test_an_unknown_backend_is_refused():
    with pytest.raises(ValueError, match="Unknown embedding backend"):
        embedder_module.build_embedder("magic", "m", "http://h")


def test_a_trailing_slash_on_the_host_does_not_double_up(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return _Response({"embeddings": [[0.0]]})

    monkeypatch.setattr(embedder_module.requests, "post", fake_post)
    embedder_module.OllamaEmbedder("bge-m3", "http://h:11434/").embed(["x"])
    assert seen["url"] == "http://h:11434/api/embed"


def test_embedding_nothing_makes_no_request(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not have called out for an empty batch")

    monkeypatch.setattr(embedder_module.requests, "post", fail)
    assert embedder_module.OllamaEmbedder("bge-m3", "http://h").embed([]) == []


def test_a_missing_model_says_how_to_pull_it(monkeypatch):
    """404 from Ollama means the host is up but the model is not there, which is
    a different fix from the host being down.
    """
    monkeypatch.setattr(embedder_module.requests, "post", lambda *a, **k: _Response(status=404))
    with pytest.raises(RuntimeError, match="ollama pull bge-m3"):
        embedder_module.OllamaEmbedder("bge-m3", "http://h").embed(["x"])


def test_a_response_with_no_vectors_is_an_error_not_an_empty_result(monkeypatch):
    """Returning [] would store nothing and report success, leaving a store that
    is silently half empty.
    """
    monkeypatch.setattr(embedder_module.requests, "post", lambda *a, **k: _Response({}))
    with pytest.raises(RuntimeError, match="no embeddings"):
        embedder_module.OllamaEmbedder("bge-m3", "http://h").embed(["x"])


def test_the_older_singular_embedding_field_is_still_accepted(monkeypatch):
    monkeypatch.setattr(
        embedder_module.requests, "post", lambda *a, **k: _Response({"embedding": [1.0, 2.0]})
    )
    assert embedder_module.OllamaEmbedder("bge-m3", "http://h").embed(["x"]) == [[1.0, 2.0]]


def test_the_dimension_is_probed_from_the_model_and_cached(monkeypatch):
    """The vector column is sized from this, so it is read from the model rather
    than assumed -- and read once, not per batch.
    """
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _Response({"embeddings": [[0.0] * 1024]})

    monkeypatch.setattr(embedder_module.requests, "post", fake_post)
    embedder = embedder_module.OllamaEmbedder("bge-m3", "http://h")
    assert embedder.dimension == 1024
    assert embedder.dimension == 1024
    assert len(calls) == 1


def test_an_unreachable_host_says_so_and_mentions_the_http_trap(monkeypatch):
    """Port 11434 does not terminate TLS, and an https:// URL fails with an SSL
    error that says nothing about the real cause.
    """
    def fake_get(*a, **k):
        raise embedder_module.requests.RequestException("refused")

    monkeypatch.setattr(embedder_module.requests, "get", fake_get)
    with pytest.raises(RuntimeError, match="http, not https"):
        embedder_module.OllamaEmbedder("bge-m3", "https://h:11434").check()
