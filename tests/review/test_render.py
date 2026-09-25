"""Rendering a golden pair, against the rules the loader actually enforces.

`ragproc.golden_pairs.ENTRY_RE` is one regular expression over the whole
document, and it fails in the worst possible way: a pair that does not match
is not reported as malformed, it is simply *not seen*. The only thing that
catches it is a count of `## Q..` headings that disagrees with the number
parsed. So every test here is about a way a pair could silently fail to
exist.
"""

from __future__ import annotations

import pytest

from nl2sql_review import render
from nl2sql_review.render import Draft


# ---------------------------------------------------------------------------
# What the document already says
# ---------------------------------------------------------------------------


def test_the_next_pair_id_follows_the_highest_one_present(document):
    assert render.next_pair_id(document.read_text()) == "Q46"


def test_the_next_pair_id_is_the_highest_plus_one_not_the_count():
    """A removed pair leaves a gap, and reusing its number would collide.

    `chunk_id` is the primary key of the context store, so two different
    questions sharing one would mean the newer silently overwriting the
    older -- for the lifetime of that database, not just this load.
    """
    gappy = "## Q01 - one\n\n## Q07 - seven\n"
    assert render.next_pair_id(gappy) == "Q08"


def test_an_empty_document_starts_at_q01():
    assert render.next_pair_id("") == "Q01"


def test_the_golden_set_is_full_at_q99():
    """`Q\\d{2}` is a wall, not a slope: Q100 would parse as nothing at all."""
    with pytest.raises(ValueError, match="Q99 is the last id"):
        render.next_pair_id("## Q99 - the last one\n")


def test_the_suite_in_force_is_the_last_heading(document):
    assert render.suite_in_force(document.read_text()).startswith("Suite 25")


def test_a_document_with_no_suites_has_none():
    assert render.suite_in_force("## Q01 - one\n") == ""


def test_the_chunk_id_follows_the_convention_every_pair_uses():
    assert render.chunk_id_for("Q46") == "eval:q46"


# ---------------------------------------------------------------------------
# The rendered block
# ---------------------------------------------------------------------------


def test_the_rendered_pair_has_every_field_the_loader_requires(draft):
    block = render.render_pair(draft, "Q46")
    assert block.startswith("## Q46 - Total net sales")
    for marker in ("```meta", "chunk_id: eval:q46", "type: golden pair", "**Question:**",
                   "**Reasoning target:**", "```sql", "**Result:**", "**Translation note:**"):
        assert marker in block


def test_a_blank_translation_note_records_where_the_pair_came_from(draft):
    assert "Promoted from web GUI feedback" in render.render_pair(draft, "Q46")


def test_a_translation_note_that_was_written_is_kept(draft):
    draft.translation_note = "Checked against the live database on 2026-09-24."
    assert "Checked against the live database" in render.render_pair(draft, "Q46")


def test_comma_separated_fields_are_normalised(draft):
    draft.tables = "  a ,b,,  c  "
    assert "tables: a, b, c" in render.render_pair(draft, "Q46")


def test_extra_meta_is_written_after_the_required_keys(draft):
    draft.extra_meta = {"source": "feedback"}
    lines = render.render_pair(draft, "Q46").splitlines()
    meta = lines[lines.index("```meta") + 1 : lines.index("```", 3)]
    assert [line.split(":")[0] for line in meta] == ["chunk_id", "type", "tables", "keywords", "source"]


# ---------------------------------------------------------------------------
# Appending
# ---------------------------------------------------------------------------


def test_appending_keeps_everything_that_was_there(document, draft):
    before = document.read_text()
    after = render.append_pair(before, draft, "Q46")
    assert after.startswith(before)


def test_a_pair_with_no_suite_inherits_the_one_in_force(document, draft):
    after = render.append_pair(document.read_text(), draft, "Q46")
    assert "# Suite 26" not in after


def test_a_new_suite_gets_a_heading(document, draft):
    draft.suite = "Suite 26 - Feedback derived"
    after = render.append_pair(document.read_text(), draft, "Q46")
    assert "# Suite 26 - Feedback derived" in after


def test_a_suite_that_is_already_in_force_is_not_repeated(document, draft):
    text = document.read_text()
    draft.suite = render.suite_in_force(text)
    after = render.append_pair(text, draft, "Q46")
    assert after.count(f"# {draft.suite}") == 1


def test_a_suite_name_without_the_word_suite_is_given_it(draft):
    draft.suite = "Feedback derived"
    assert "# Suite - Feedback derived" in render.append_pair("", draft, "Q46")


def test_a_document_with_no_trailing_newline_is_not_run_together(draft):
    after = render.append_pair("## Q01 - one\n\n**x**", draft, "Q02")
    assert "**x**\n\n## Q02" in after


# ---------------------------------------------------------------------------
# What is refused, and why
# ---------------------------------------------------------------------------


def test_a_complete_draft_has_no_problems(draft):
    assert render.problems(draft, "Q46") == []


@pytest.mark.parametrize("field", sorted(render.REQUIRED))
def test_every_required_field_is_required(draft, field):
    setattr(draft, field, "   ")
    found = render.problems(draft, "Q46")
    assert any(problem.startswith(f"{field} is empty") for problem in found), found


def test_every_problem_is_reported_at_once_not_just_the_first(draft):
    """A curator filling in a form wants the list, not a game of whack-a-mole."""
    draft.keywords = ""
    draft.result = ""
    draft.title = ""
    assert len(render.problems(draft, "Q46")) == 3


def test_a_question_ending_in_a_quote_closes_its_own_field(draft):
    # `**Question:** "(.*?)"\n` ends at the first quote a newline follows.
    draft.question = 'Which store did they call "the flagship"'
    assert any("double quote" in p for p in render.problems(draft, "Q46"))


def test_a_quote_inside_the_question_is_fine(draft):
    draft.question = 'Which store is the "flagship" store?'
    assert render.problems(draft, "Q46") == []


@pytest.mark.parametrize("field", ["sql_code", "reasoning_target", "result", "translation_note"])
def test_a_fence_anywhere_ends_the_block_early(draft, field):
    setattr(draft, field, "before ``` after")
    assert any("fence" in p for p in render.problems(draft, "Q46"))


@pytest.mark.parametrize("field", ["question", "reasoning_target", "result"])
def test_the_single_line_fields_must_be_single_line(draft, field):
    setattr(draft, field, "one\ntwo")
    assert any("more than one line" in p for p in render.problems(draft, "Q46"))


def test_a_multi_line_title_splits_the_heading(draft):
    draft.title = "one\ntwo"
    assert any("one line" in p for p in render.problems(draft, "Q46"))


def test_a_pair_id_the_loader_cannot_match_is_refused(draft):
    assert any("two-digit pair id" in p for p in render.problems(draft, "Q100"))


@pytest.mark.parametrize("field", ["tables", "keywords"])
def test_a_list_field_of_only_commas_has_no_entries(draft, field):
    setattr(draft, field, " , , ")
    found = render.problems(draft, "Q46")
    assert any("no entries" in p or "is empty" in p for p in found)


def test_a_meta_key_the_parser_will_not_read_is_refused(draft):
    draft.extra_meta = {"not a key": "value"}
    assert any("meta key" in p for p in render.problems(draft, "Q46"))


# ---------------------------------------------------------------------------
# The draft itself
# ---------------------------------------------------------------------------


def test_a_draft_survives_a_round_trip_through_a_mapping(draft):
    assert Draft.from_mapping(draft.as_dict()) == draft


def test_an_unknown_key_is_dropped_rather_than_raising():
    """The draft column is JSONB written by a browser.

    A client that sends one field too many should not take the promotion
    endpoint down with it.
    """
    built = Draft.from_mapping({"title": "t", "nonsense": "x"})
    assert built.title == "t"
    assert not hasattr(built, "nonsense")


def test_a_non_string_field_becomes_empty_rather_than_breaking_the_render():
    assert Draft.from_mapping({"title": 42}).title == ""


def test_extra_meta_that_is_not_a_mapping_is_ignored():
    assert Draft.from_mapping({"extra_meta": "nope"}).extra_meta == {}
