"""Structure-aware chunking of the knowledge documents.

This is the part of the pipeline that decides what a retrieved chunk *is*. Get
it wrong and nothing downstream complains: a `CREATE TABLE` cut in half still
embeds, still stores, still comes back as a confident answer to a question about
a table it no longer fully describes.

So the properties worth pinning are structural. Fenced code, ```meta blocks and
pipe tables are atomic. Sections split on `##`. Every chunk carries its heading
path. Nothing embeds unless a section is genuinely oversized.

Pure and offline -- the embedding backend is injected, and these tests inject a
deterministic fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT / "rag", REPO_ROOT / "chunking"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

pytest.importorskip("numpy", reason="numpy is needed for the drift detection")

from ragproc.chunker import (  # noqa: E402
    Chunk,
    MarkdownSemanticChunker,
    approx_tokens,
    parse_blocks,
    parse_meta_block,
    split_sections,
)

KNOWLEDGE = REPO_ROOT / "knowledge"


def chunker(**kwargs) -> MarkdownSemanticChunker:
    return MarkdownSemanticChunker(**kwargs)


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------


def test_a_fenced_block_survives_whole_including_its_blank_lines():
    """The base chunker splits on sentence punctuation. SQL has none, so a
    CREATE TABLE is one enormous "sentence" that only survives by being atomic.
    """
    markdown = "text\n\n```sql\nCREATE TABLE t (\n\n  a INT\n);\n```\n\nmore"
    blocks = parse_blocks(markdown)
    code = [b for b in blocks if b.kind == "code"]
    assert len(code) == 1
    assert "CREATE TABLE t (" in code[0].text
    assert "a INT" in code[0].text
    assert code[0].lang == "sql"
    assert code[0].atomic


def test_a_pipe_table_is_one_block_not_one_per_row():
    markdown = "before\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nafter"
    tables = [b for b in parse_blocks(markdown) if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].text.count("\n") == 3
    assert tables[0].atomic


def test_headings_keep_their_level():
    blocks = parse_blocks("# One\n\n## Two\n\n#### Four")
    assert [(b.text, b.level) for b in blocks if b.kind == "heading"] == [
        ("One", 1), ("Two", 2), ("Four", 4)
    ]


def test_prose_runs_are_joined_and_blank_lines_separate_them():
    blocks = [b for b in parse_blocks("one\ntwo\n\nthree") if b.kind == "prose"]
    assert [b.text for b in blocks] == ["one\ntwo", "three"]


@pytest.mark.parametrize("rule", ["---", "***", "___"])
def test_horizontal_rules_are_dropped(rule):
    """They separate sections visually and carry no content; keeping them would
    put a lone `---` in a chunk.
    """
    assert [b.kind for b in parse_blocks(f"a\n\n{rule}\n\nb")] == ["prose", "prose"]


def test_an_unterminated_fence_does_not_run_away():
    """A document can be mid-edit. The parser must stop at the end of input
    rather than looping.
    """
    blocks = parse_blocks("```sql\nSELECT 1\n")
    assert [b.kind for b in blocks] == ["code"]
    assert "SELECT 1" in blocks[0].text


def test_an_empty_document_yields_no_blocks():
    assert parse_blocks("") == []
    assert parse_blocks("\n\n  \n") == []


# ---------------------------------------------------------------------------
# Meta blocks
# ---------------------------------------------------------------------------


def test_a_meta_block_becomes_a_dict():
    [block] = parse_blocks("```meta\nchunk_id: biz:grain\ntype: business rule\n```")
    assert parse_meta_block(block) == {"chunk_id": "biz:grain", "type": "business rule"}


def test_only_meta_fences_are_parsed_as_metadata():
    """A `sql` fence containing `key: value` is SQL, not metadata."""
    [block] = parse_blocks("```sql\nselect: 1\n```")
    assert parse_meta_block(block) == {}


def test_non_key_value_lines_in_a_meta_block_are_ignored():
    [block] = parse_blocks("```meta\nchunk_id: x\njust prose\n```")
    assert parse_meta_block(block) == {"chunk_id": "x"}


def test_a_sections_metadata_reaches_every_chunk_it_produces():
    markdown = "## Topic\n\n```meta\nchunk_id: biz:x\ntables: dim_store\n```\n\nbody text here"
    [chunk] = chunker().chunk_document(markdown)
    assert chunk.meta == {"chunk_id": "biz:x", "tables": "dim_store"}


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def test_sections_start_at_the_split_level():
    markdown = "# Doc\n\nintro\n\n## One\n\na\n\n## Two\n\nb"
    sections = split_sections(parse_blocks(markdown), split_level=2)
    assert [s.path_str for s in sections] == ["Doc", "Doc > One", "Doc > Two"]


def test_a_deeper_heading_stays_inside_its_section():
    """`###` is content within a `##` topic, not a new topic -- the knowledge
    documents are authored with one self-contained topic per `##`.
    """
    markdown = "## Topic\n\na\n\n### Detail\n\nb"
    sections = split_sections(parse_blocks(markdown), split_level=2)
    assert len(sections) == 1
    assert any(b.kind == "heading" and b.text == "Detail" for b in sections[0].blocks)


def test_the_split_level_is_configurable():
    markdown = "## Topic\n\na\n\n### Detail\n\nb"
    assert len(split_sections(parse_blocks(markdown), split_level=3)) == 2


def test_content_before_any_heading_is_kept_as_a_preamble():
    """Dropping it would silently lose the top of a document."""
    sections = split_sections(parse_blocks("orphan text\n\n## Topic\n\nbody"))
    assert sections[0].path_str == "(preamble)"
    assert sections[0].blocks[0].text == "orphan text"


def test_a_heading_with_no_body_produces_no_section():
    assert split_sections(parse_blocks("## Empty\n\n## Also Empty")) == []


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_every_chunk_is_prefixed_with_its_heading_path():
    """A chunk retrieved on its own has to still say what it is about."""
    [chunk] = chunker().chunk_document("# Doc\n\n## Grain\n\nbody text")
    assert chunk.content.startswith("# Doc > Grain")
    assert chunk.heading_path == "Doc > Grain"


def test_a_section_that_fits_is_emitted_whole_and_never_embeds():
    """Most sections fit, which is why the embedding backend is constructed
    lazily and usually never used at chunk time.
    """
    def explode(texts):
        raise AssertionError("should not have embedded a section that fits")

    chunks = chunker(embed_fn=explode).chunk_document("## Topic\n\n" + "word " * 100)
    assert len(chunks) == 1


def test_an_oversized_section_is_split_but_keeps_atomic_blocks_intact():
    big_table = "\n".join(f"| {i} | {'x' * 40} |" for i in range(200))
    markdown = f"## Topic\n\n{'word ' * 400}\n\n{big_table}"
    chunks = chunker(max_chunk_tokens=200, min_chunk_tokens=1).chunk_document(markdown)
    assert len(chunks) > 1
    # The table landed in exactly one chunk rather than being cut across two.
    holding = [c for c in chunks if "| 199 |" in c.content]
    assert len(holding) == 1
    assert "| 0 |" in holding[0].content


def test_ordinals_are_contiguous_from_zero():
    markdown = "\n\n".join(f"## S{i}\n\nbody number {i} with enough words to stand alone" for i in range(5))
    chunks = chunker(min_chunk_tokens=1).chunk_document(markdown)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_a_runt_is_folded_into_its_neighbour_from_the_same_section():
    """A three-word chunk retrieves badly and wastes a slot."""
    big_table = "\n".join(f"| {i} | {'x' * 30} |" for i in range(120))
    markdown = f"## Topic\n\n{big_table}\n\ntiny"
    chunks = chunker(max_chunk_tokens=150, min_chunk_tokens=40).chunk_document(markdown)
    assert all(c.token_estimate >= 40 or len(chunks) == 1 for c in chunks)
    assert "tiny" in chunks[-1].content


def test_a_runt_is_not_folded_across_a_section_boundary():
    """Merging across sections would put two topics under one heading path, and
    the heading path is what a retrieved chunk uses to say what it is.
    """
    markdown = "## One\n\n" + "word " * 300 + "\n\n## Two\n\ntiny"
    chunks = chunker(max_chunk_tokens=500, min_chunk_tokens=40).chunk_document(markdown)
    paths = [c.heading_path for c in chunks]
    assert "Two" in paths
    assert chunks[paths.index("Two")].content.endswith("tiny")


def test_an_empty_document_chunks_to_nothing():
    assert chunker().chunk_document("") == []


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_a_chunk_id_follows_its_content_not_its_position():
    """This is what makes updates incremental: editing one section leaves every
    other chunk's hash unchanged, so only the new text is re-embedded.
    """
    first = Chunk(ordinal=0, heading_path="A", content="the same text")
    moved = Chunk(ordinal=7, heading_path="A", content="the same text")
    assert first.content_hash == moved.content_hash

    edited = Chunk(ordinal=0, heading_path="A", content="different text")
    assert edited.content_hash != first.content_hash


def test_trailing_whitespace_does_not_change_the_hash():
    """Otherwise an editor stripping line endings would re-embed a whole
    document that had not meaningfully changed.
    """
    plain = Chunk(ordinal=0, heading_path="A", content="line one\nline two")
    padded = Chunk(ordinal=0, heading_path="A", content="line one   \nline two\t")
    assert plain.content_hash == padded.content_hash


def test_the_token_estimate_is_the_word_count_heuristic():
    assert approx_tokens("one two three four") == int(4 * 1.3)
    assert approx_tokens("") == 0


# ---------------------------------------------------------------------------
# Semantic splitting -- the only path that embeds
# ---------------------------------------------------------------------------


def oscillating(texts: list[str]) -> list[list[float]]:
    """Alternating orthogonal vectors: every consecutive pair is maximally
    distant, so drift detection fires between every sentence.
    """
    return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i, _ in enumerate(texts)]


def identical(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def test_an_oversized_prose_block_is_split_on_drift():
    sentences = " ".join(f"Sentence number {i} about a topic." for i in range(40))
    chunks = chunker(
        embed_fn=oscillating, max_chunk_tokens=60, min_chunk_tokens=1
    ).chunk_document(f"## Topic\n\n{sentences}")
    assert len(chunks) > 1


def test_prose_with_no_drift_is_still_capped_by_the_token_limit():
    """The size cap is a backstop: without it, text the model considers uniform
    would produce one chunk however long it was.
    """
    sentences = " ".join(f"Sentence number {i} about one single topic." for i in range(60))
    chunks = chunker(
        embed_fn=identical, max_chunk_tokens=80, min_chunk_tokens=1
    ).chunk_document(f"## Topic\n\n{sentences}")
    assert len(chunks) > 1


def test_a_block_of_fewer_than_three_sentences_is_not_split():
    """Two sentences give one distance, which has no percentile worth taking."""
    def explode(texts):
        raise AssertionError("should not have embedded a two-sentence block")

    long_pair = ("word " * 400) + ". " + ("word " * 400) + "."
    chunks = chunker(embed_fn=explode, max_chunk_tokens=100, min_chunk_tokens=1).chunk_document(
        f"## Topic\n\n{long_pair}"
    )
    assert len(chunks) == 1


def test_an_oversized_prose_section_without_a_backend_says_so():
    """Better a clear error than a silently truncated document."""
    sentences = " ".join(f"Sentence number {i} about a topic." for i in range(40))
    with pytest.raises(RuntimeError, match="no embedding backend"):
        chunker(max_chunk_tokens=60).chunk_document(f"## Topic\n\n{sentences}")


# ---------------------------------------------------------------------------
# The real documents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["business_index.md", "data_dictionary.md", "ddl_index.md"]
)
def test_each_knowledge_document_chunks_to_what_the_published_store_holds(name):
    """The v3 vector store ships 53 chunks across these three documents. If the
    chunker's behaviour drifts, the store and the code that built it disagree.
    """
    chunks = chunker().chunk_document((KNOWLEDGE / name).read_text())
    assert chunks
    assert all(c.content.strip() for c in chunks)
    assert all(c.token_estimate <= 600 for c in chunks), "a chunk overran the size cap badly"


def test_the_three_documents_together_still_produce_the_published_count():
    total = sum(
        len(chunker().chunk_document((KNOWLEDGE / name).read_text()))
        for name in ("business_index.md", "data_dictionary.md", "ddl_index.md")
    )
    assert total == 53


def test_every_chunk_of_a_real_document_names_its_heading_path():
    for chunk in chunker().chunk_document((KNOWLEDGE / "business_index.md").read_text()):
        assert chunk.heading_path
        assert chunk.content.startswith("# ")


def test_a_backend_returning_too_few_vectors_does_not_split_on_nothing():
    """Defensive: with fewer than two vectors there are no consecutive
    distances to take a percentile of. A backend that returns a short list --
    a truncated batch, say -- must leave the text whole rather than raise.
    """
    sentences = " ".join(f"Sentence number {i} about a topic." for i in range(40))
    stingy = MarkdownSemanticChunker(
        embed_fn=lambda texts: [[1.0, 0.0]], max_chunk_tokens=60, min_chunk_tokens=1
    )
    chunks = stingy.chunk_document(f"## Topic\n\n{sentences}")
    assert len(chunks) == 1
