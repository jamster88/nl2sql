"""The base chunker in `chunking/semantic_chunker.py`.

It is not a spare file: `ragproc.chunker.MarkdownSemanticChunker` subclasses
it, and the sentence splitter and cosine-distance helper the whole pipeline
relies on are inherited from here. What the subclass does *not* inherit is
this class's own `chunk_text` -- the plain, non-markdown path that embeds
with `sentence_transformers` -- and that is the part nothing exercised.

So it is exercised here against a stub model. The embedding backend is not
what is interesting: the splitting decision is. Given distances between
consecutive sentences, does it break where the meaning changes, does the
token cap actually cap, and does the percentile threshold mean what it says.
Real embeddings would make those questions unanswerable.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from ragproc import _ensure_semantic_chunker_on_path  # noqa: F401  (puts it on sys.path)
from semantic_chunker import SemanticChunker


class _StubModel:
    """Returns whatever vectors the test asked for, in order."""

    def __init__(self, vectors) -> None:
        self.vectors = vectors
        self.encoded: list[list[str]] = []

    def encode(self, sentences, convert_to_numpy=True):
        self.encoded.append(list(sentences))
        return np.array(self.vectors[: len(sentences)], dtype=float)


@pytest.fixture
def stub_sentence_transformers(monkeypatch):
    """Stand in for the real library, which nothing here installs.

    `torch` and a model download are a heavy price for testing a comparison
    against a percentile, and the class imports the library inside `__init__`
    precisely so a subclass with its own backend does not pay it.
    """
    loaded: list[str] = []

    class _SentenceTransformer:
        def __init__(self, model_name):
            loaded.append(model_name)
            self.model_name = model_name

        def encode(self, sentences, convert_to_numpy=True):
            # Non-zero on purpose: a zero vector has no direction, and the
            # cosine distance would divide by its norm and warn. A real model
            # never returns one, so a stub that does would be testing a case
            # that cannot happen.
            return np.array(
                [[len(s) % 5 + 1.0, len(s.split()) + 1.0, 1.0] for s in sentences]
            )

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return loaded


def chunker_with(vectors, **kwargs) -> SemanticChunker:
    """A chunker whose model is the stub, without constructing the real one."""
    made = SemanticChunker.__new__(SemanticChunker)
    made.model = _StubModel(vectors)
    made.threshold_percentile = kwargs.get("threshold_percentile", 60)
    made.max_chunk_tokens = kwargs.get("max_chunk_tokens", 500)
    return made


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_it_loads_the_model_it_was_named(stub_sentence_transformers, capsys):
    chunker = SemanticChunker(model_name="BAAI/bge-m3", threshold_percentile=65)
    assert stub_sentence_transformers == ["BAAI/bge-m3"]
    assert chunker.threshold_percentile == 65
    assert chunker.max_chunk_tokens == 500
    assert "Loading BAAI/bge-m3" in capsys.readouterr().out


def test_the_library_is_imported_only_when_the_base_class_is_constructed(monkeypatch):
    """The import sits inside `__init__` so that a subclass supplying its own
    embedding backend -- which is what this repository actually uses -- never
    needs `sentence_transformers` or torch installed. Importing the module
    must therefore not drag it in.
    """
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    import importlib

    import semantic_chunker

    importlib.reload(semantic_chunker)
    assert "sentence_transformers" not in sys.modules


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_no_text_is_no_chunks():
    assert chunker_with([]).chunk_text("") == []
    assert chunker_with([]).chunk_text("   \n  ") == []


def test_a_single_sentence_is_returned_without_being_embedded():
    """There is nothing to compare it against, so paying for an embedding
    would buy nothing.
    """
    chunker = chunker_with([[1.0, 0.0]])
    assert chunker.chunk_text("Only one sentence here.") == ["Only one sentence here."]
    assert chunker.model.encoded == [], "it embedded a sentence it did not need to"


def test_it_breaks_where_the_meaning_changes():
    """Two sentences about one thing, then two about another: the distance
    between the second and third is the only spike, and that is where the
    break belongs.
    """
    same, other = [1.0, 0.0], [0.0, 1.0]
    chunker = chunker_with([same, same, other, other])
    chunks = chunker.chunk_text(
        "Chunking splits text by meaning. Related context stays together. "
        "Storage clusters need careful networking. Isolated VLANs keep latency low."
    )
    assert len(chunks) == 2
    assert chunks[0] == "Chunking splits text by meaning. Related context stays together."
    assert chunks[1].startswith("Storage clusters")


def test_a_chunk_that_grows_too_long_is_cut_even_without_a_spike():
    """The cap is a safety net for prose that never drifts: an embedding
    model has a context window, and a chunk past it is silently truncated
    rather than rejected.
    """
    identical = [1.0, 0.0]
    sentences = " ".join(f"Sentence number {i} says something unremarkable." for i in range(8))
    chunker = chunker_with([identical] * 8, max_chunk_tokens=8)
    chunks = chunker.chunk_text(sentences)
    assert len(chunks) > 1, "the token cap never fired"
    assert all(len(c.split()) * 1.3 <= 8 * 2 for c in chunks)


def vectors_with_distances(distances) -> list[list[float]]:
    """Unit vectors whose consecutive cosine distances are the ones given.

    Distance is `1 - cos(theta)`, so each step is a rotation of
    `acos(1 - d)` from the last. Building the input backwards from the
    distances is what makes the threshold arithmetic in the tests below
    checkable rather than approximate.
    """
    angle = 0.0
    points = [[1.0, 0.0]]
    for distance in distances:
        angle += float(np.arccos(1.0 - distance))
        points.append([float(np.cos(angle)), float(np.sin(angle))])
    return points


def test_the_percentile_decides_how_eagerly_it_breaks():
    """The threshold is a percentile of *this document's own* distances, not
    an absolute number, which is what lets one setting work on prose that
    drifts constantly and prose that does not. A lower percentile is a lower
    bar, so it can only break in more places.
    """
    text = " ".join(f"Sentence number {i} here." for i in range(5))
    vectors = vectors_with_distances([0.1, 0.3, 0.5, 0.7])

    eager = chunker_with(vectors, threshold_percentile=10).chunk_text(text)
    middling = chunker_with(vectors, threshold_percentile=50).chunk_text(text)
    reluctant = chunker_with(vectors, threshold_percentile=90).chunk_text(text)

    assert len(eager) > len(reluctant), "the percentile changed nothing"
    assert len(eager) >= len(middling) >= len(reluctant)
    assert len(reluctant) == 2, "the widest gap is a break at any percentile"


def test_it_always_breaks_at_the_widest_gap_however_high_the_percentile():
    """A consequence of comparing strictly against an interpolated
    percentile: the largest distance is above every percentile of the set it
    belongs to. So multi-topic prose is never returned as one chunk, which is
    the behaviour that matters and is not obvious from the setting's name.
    """
    text = " ".join(f"Sentence number {i} here." for i in range(4))
    vectors = vectors_with_distances([0.01, 0.9, 0.01])
    assert len(chunker_with(vectors, threshold_percentile=99).chunk_text(text)) == 2


def test_every_sentence_survives_whatever_the_distances_say():
    """The one property that must hold however it breaks: chunking loses no
    text. A dropped sentence is a fact the retriever can never return.
    """
    vectors = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0], [0.0, 1.0]]
    text = "First point made. Second point made. Third point made. Fourth point made."
    chunks = chunker_with(vectors).chunk_text(text)
    assert " ".join(chunks) == text


# ---------------------------------------------------------------------------
# The distance helper the rest of the pipeline inherits
# ---------------------------------------------------------------------------


def test_identical_vectors_are_no_distance_apart():
    chunker = chunker_with([])
    [distance] = chunker._calculate_cosine_distances(np.array([[1.0, 2.0], [1.0, 2.0]]))
    assert distance == pytest.approx(0.0, abs=1e-9)


def test_orthogonal_vectors_are_one_apart():
    chunker = chunker_with([])
    [distance] = chunker._calculate_cosine_distances(np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert distance == pytest.approx(1.0)


def test_an_abbreviation_does_not_end_a_sentence():
    """The splitter's lookbehinds exist for this; a knowledge document is
    full of "e.g." and "Dr.".
    """
    chunker = chunker_with([])
    assert chunker._split_into_sentences("Mr. Smith arrived. He was late.") == [
        "Mr. Smith arrived.",
        "He was late.",
    ]


def test_the_worked_example_at_the_bottom_still_runs(stub_sentence_transformers, capsys):
    """The file ends with a demo, which is the only documentation this module
    has -- there is no README beside it. A demo that raises is worse than no
    demo, and nothing else here imports the module as a script.
    """
    import runpy

    import semantic_chunker

    runpy.run_path(semantic_chunker.__file__, run_name="__main__")

    output = capsys.readouterr().out
    assert "Loading BAAI/bge-m3" in output
    assert "--- Chunk 1 ---" in output
    assert "Semantic chunking splits text based on meaning." in output
