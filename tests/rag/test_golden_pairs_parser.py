"""The golden-pair parser, against the real document.

Nothing downstream can be right if the parse is wrong: a pair whose SQL is
truncated at a fence, or whose question captured the surrounding markdown, gets
embedded and then handed to a model as an example to copy. The parser is strict
-- a pair missing a labelled field does not match at all -- and these tests pin
both that strictness and the shape of what comes out.

No database and no embedding model: the parser is pure text.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
DOCUMENT = REPO_ROOT / "context_questions" / "translated_questions.md"

if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")

from ragproc.golden_pairs import GoldenPair, parse_document, parse_meta  # noqa: E402

EXPECTED_PAIRS = 45

# The eight columns the pairs are loaded into, as the task specifies them.
REQUIRED_FIELDS = (
    "chunk_id", "type", "tables", "keywords",
    "question", "reasoning_target", "sql_code", "result",
)


@pytest.fixture(scope="module")
def pairs() -> list[GoldenPair]:
    return parse_document(DOCUMENT)


def test_every_pair_in_the_document_is_parsed(pairs):
    assert len(pairs) == EXPECTED_PAIRS
    assert [p.pair_id for p in pairs] == [f"Q{i:02d}" for i in range(1, EXPECTED_PAIRS + 1)]


def test_every_pair_has_all_eight_fields_populated(pairs):
    for pair in pairs:
        for field in REQUIRED_FIELDS:
            assert getattr(pair, field).strip(), f"{pair.pair_id} has an empty {field}"


def test_chunk_ids_are_unique_and_namespaced(pairs):
    ids = [p.chunk_id for p in pairs]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("eval:q") for i in ids)


def test_the_sql_is_captured_whole_and_without_its_fence(pairs):
    """A fence left in, or SQL cut short at one, would be invisible in the
    database and fatal as an example.
    """
    for pair in pairs:
        assert "```" not in pair.sql_code, f"{pair.pair_id} kept a fence"
        upper = pair.sql_code.upper()
        assert upper.startswith(("SELECT", "WITH")), f"{pair.pair_id} does not start a query"
        assert pair.sql_code.rstrip().endswith(";"), f"{pair.pair_id} looks truncated"


def test_the_question_is_captured_without_its_surrounding_quotes(pairs):
    for pair in pairs:
        assert not pair.question.startswith('"')
        assert not pair.question.endswith('"')
        assert "**" not in pair.question


def test_no_field_bleeds_into_the_next(pairs):
    """The labels are the delimiters, so a regex that is too greedy shows up as
    one field swallowing the label of the one after it.
    """
    labels = ("**Question:**", "**Reasoning target:**", "**Result:**", "**Translation note:**")
    for pair in pairs:
        for field in REQUIRED_FIELDS:
            value = getattr(pair, field)
            for label in labels:
                assert label not in value, f"{pair.pair_id}.{field} swallowed {label}"


def test_each_pair_knows_which_suite_it_belongs_to(pairs):
    assert all(p.suite.startswith("Suite ") for p in pairs)
    assert len({p.suite for p in pairs}) == 25


def test_tables_and_keywords_split_into_lists(pairs):
    for pair in pairs:
        assert pair.table_list, f"{pair.pair_id} names no tables"
        assert pair.keyword_list, f"{pair.pair_id} has no keywords"
        assert all(not t.startswith(" ") for t in pair.table_list)


def test_every_table_named_by_a_pair_exists_in_the_ddl():
    """The tables column is a retrieval hint fed straight into table selection,
    so a typo here would push the agent toward a table that is not there.
    """
    ddl = (REPO_ROOT / "data_gen" / "ddl.sql").read_text()
    declared = {
        line.split()[2]
        for line in ddl.splitlines()
        if line.strip().startswith("CREATE TABLE")
    }
    for pair in parse_document(DOCUMENT):
        for table in pair.table_list:
            assert table in declared, f"{pair.pair_id} names {table}, absent from ddl.sql"


def test_the_content_hash_changes_when_any_loaded_field_changes(pairs):
    """The hash drives incremental re-embedding: if editing a pair's SQL left
    the hash alone, the stored vector would silently describe the old text.
    """
    import dataclasses

    original = pairs[0]
    for field in REQUIRED_FIELDS:
        edited = dataclasses.replace(original, **{field: getattr(original, field) + " x"})
        assert edited.content_hash != original.content_hash, f"{field} does not affect the hash"


def test_the_hash_ignores_fields_that_are_not_loaded(pairs):
    """Re-titling a pair should not force it to be re-embedded."""
    import dataclasses

    original = pairs[0]
    retitled = dataclasses.replace(original, title=original.title + " (renamed)")
    assert retitled.content_hash == original.content_hash


def test_a_pair_missing_a_field_is_refused_rather_than_partly_loaded(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_text(
        DOCUMENT.read_text().replace("**Reasoning target:** Reconciling daily sales", "Reconciling daily sales", 1)
    )
    with pytest.raises(ValueError, match="pair headings but only"):
        parse_document(broken)


def test_a_meta_block_missing_a_key_is_refused(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_text(DOCUMENT.read_text().replace("keywords: gross profit", "kewords: gross profit", 1))
    with pytest.raises(ValueError, match="missing keywords"):
        parse_document(broken)


def test_meta_blocks_parse_into_key_value_pairs():
    assert parse_meta("chunk_id: eval:q01\ntype: golden pair\n") == {
        "chunk_id": "eval:q01",
        "type": "golden pair",
    }
    assert parse_meta("not a key value line") == {}
