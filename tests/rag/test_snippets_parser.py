"""`ragproc.snippets`' parser, against the real document and against broken ones.

The parser is the contract the review service writes to and the loader reads
by, so it is tested the way the golden pairs' is: on the document as it is,
and on every way a section can be malformed -- each of which has to be a
loud failure, never a quietly shorter load.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ragproc import snippets as sn

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCUMENT = REPO_ROOT / "context_questions" / "sql_snippets.md"

SECTION = """## S01 - Sales on the fiscal calendar

```meta
chunk_id: snippet:s01
kind: join
tables: fact_pos_retail_sales, dim_date
keywords: sales by fiscal month, monthly sales
```

**Means:** Sales placed on the fiscal calendar.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
JOIN dim_date d ON d.date_key = f.sales_date_key
```

**Note:** The sales fact names its date column sales_date_key.
"""


@pytest.fixture(scope="module")
def document() -> list[sn.Snippet]:
    return sn.parse_document(DOCUMENT)


def test_the_document_parses_every_heading_it_has(document):
    headings = re.findall(r"^## (S\d{2,}) - ", DOCUMENT.read_text(), re.M)
    assert [s.snippet_id for s in document] == headings
    assert len(document) >= 30


def test_every_kind_is_represented_and_every_snippet_is_one_of_them(document):
    assert {s.kind for s in document} == set(sn.KINDS)


def test_every_chunk_id_follows_the_convention_and_the_ordinal_is_document_order(document):
    assert [s.chunk_id for s in document] == [sn.chunk_id_for(s.snippet_id) for s in document]
    assert [s.ordinal for s in document] == list(range(1, len(document) + 1))


def test_every_snippet_lists_its_tables_and_keywords_and_says_what_it_means(document):
    for snippet in document:
        assert snippet.table_list, snippet.snippet_id
        assert snippet.keyword_list, snippet.snippet_id
        assert snippet.means and "\n" not in snippet.means, snippet.snippet_id
        assert snippet.note and "\n" not in snippet.note, snippet.snippet_id


def test_one_section_reads_back_field_for_field():
    [snippet] = sn.parse_text(SECTION)
    assert snippet.snippet_id == "S01"
    assert snippet.name == "Sales on the fiscal calendar"
    assert snippet.kind == "join"
    assert snippet.table_list == ["fact_pos_retail_sales", "dim_date"]
    assert snippet.keyword_list == ["sales by fiscal month", "monthly sales"]
    assert snippet.means == "Sales placed on the fiscal calendar."
    assert snippet.applies_to == "fact_pos_retail_sales f"
    assert snippet.sql == "JOIN dim_date d ON d.date_key = f.sales_date_key"
    assert snippet.note == "The sales fact names its date column sales_date_key."
    assert snippet.meta["chunk_id"] == "snippet:s01"


def test_what_is_embedded_is_the_name_the_meaning_and_the_phrasings():
    [snippet] = sn.parse_text(SECTION)
    assert snippet.search_text == (
        "Sales on the fiscal calendar. Sales placed on the fiscal calendar. "
        "Asked as: sales by fiscal month, monthly sales."
    )


def test_the_content_hash_moves_with_every_loaded_field_and_not_with_the_note():
    [base] = sn.parse_text(SECTION)
    for old, new in [
        ("JOIN dim_date d", "LEFT JOIN dim_date d"),
        ("fact_pos_retail_sales f\n```", "fact_pos_retail_sales s\n```"),
        ("Sales placed on", "Sales put on"),
        ("monthly sales", "sales per month"),
        ("Sales on the fiscal calendar\n", "Sales by fiscal period\n"),
    ]:
        [changed] = sn.parse_text(SECTION.replace(old, new, 1))
        assert changed.content_hash != base.content_hash, old
    [noted] = sn.parse_text(SECTION.replace("names its date column", "calls its date column"))
    assert noted.content_hash == base.content_hash


def test_ids_grow_past_two_digits():
    [snippet] = sn.parse_text(SECTION.replace("## S01 -", "## S100 -").replace("snippet:s01", "snippet:s100"))
    assert snippet.snippet_id == "S100"


@pytest.mark.parametrize(
    "old,new",
    [
        ("**Means:** ", "**Meaning:** "),
        ("**Applies to:**\n", "**Applies:**\n"),
        ("**SQL:**\n", "**Query:**\n"),
        ("**Note:** ", "Note: "),
        ("```sql\nJOIN", "```\nJOIN"),
    ],
)
def test_a_section_missing_a_labelled_field_is_counted_and_refused(old, new):
    broken = SECTION.replace(old, new, 1)
    with pytest.raises(ValueError, match="1 snippet headings but only 0 parsed cleanly"):
        sn.parse_text(broken)


@pytest.mark.parametrize("key", ["chunk_id", "kind", "tables", "keywords"])
def test_a_meta_block_missing_a_key_names_the_snippet_and_the_key(key):
    broken = re.sub(rf"^{key}: .*\n", "", SECTION, flags=re.M)
    with pytest.raises(ValueError, match=f"S01 has a meta block missing {key}"):
        sn.parse_text(broken)


def test_a_kind_nobody_validates_is_refused():
    with pytest.raises(ValueError, match="S01 is a 'having'; a snippet is one of join, filter, measure, dimension"):
        sn.parse_text(SECTION.replace("kind: join", "kind: having"))


def test_the_kind_is_read_case_insensitively():
    [snippet] = sn.parse_text(SECTION.replace("kind: join", "kind: JOIN"))
    assert snippet.kind == "join"


def test_an_id_or_chunk_id_used_twice_is_refused():
    second = SECTION.replace("## S01 -", "## S02 -")
    with pytest.raises(ValueError, match="snippet:s01 is used by more than one snippet"):
        sn.parse_text(SECTION + "\n" + second)
    with pytest.raises(ValueError, match="S01 is used by more than one snippet"):
        sn.parse_text(SECTION + "\n" + SECTION.replace("snippet:s01", "snippet:s02"))


def test_headings_between_sections_are_prose_and_not_snippets():
    text = "# Joins\n\nSome prose.\n\n" + SECTION + "\n# Filters\n\n" + SECTION.replace("S01", "S02").replace("s01", "s02")
    assert [s.snippet_id for s in sn.parse_text(text)] == ["S01", "S02"]


def test_the_document_hash_is_of_the_text_itself():
    assert sn.document_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sn.document_hash(b"abc") == sn.document_hash("abc")
    # The bytes, line endings and all: what sha256sum in launch.sh sees.
    assert sn.document_hash(b"a\r\nb") != sn.document_hash("a\nb")
    assert sn.vector_literal([1, 0.5]) == "[1.0,0.5]"
