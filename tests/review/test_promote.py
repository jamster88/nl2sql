"""The promotion path, against the real parser and a real file.

The step that matters is the round trip: render, then parse the candidate
document with `ragproc.golden_pairs.parse_document` -- the loader's own code,
not a copy of its rules -- and refuse unless the pair count went up by
exactly one and every field came back the way it went in. These tests are
mostly ways of making that step fail on purpose.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from nl2sql_review import promote as promotion
from nl2sql_review.promote import Promotion, PromotionError, StepResult
from nl2sql_review.render import Draft

from ragproc import golden_pairs as gp


# ---------------------------------------------------------------------------
# The happy path, measured against the real document
# ---------------------------------------------------------------------------


def test_a_pair_is_added_and_the_document_still_parses(settings, draft, document):
    before = len(gp.parse_document(document))
    result = promotion.promote(settings, draft)

    assert result.pair_id == "Q46"
    assert result.chunk_id == "eval:q46"
    assert (before, result.pairs_after) == (45, 46)

    pairs = gp.parse_document(document)
    assert len(pairs) == before + 1
    added = pairs[-1]
    assert added.pair_id == "Q46"
    assert added.question == draft.question
    assert added.sql_code == draft.sql_code
    assert added.keyword_list == ["net sales", "produce", "department", "fiscal year"]


def test_the_previous_version_is_kept_beside_the_new_one(settings, draft, document):
    original = document.read_text()
    result = promotion.promote(settings, draft)
    assert Path(result.backup).read_text() == original


def test_the_pair_inherits_the_suite_it_was_appended_after(settings, draft, document):
    result = promotion.promote(settings, draft)
    assert result.suite == gp.parse_document(document)[-1].suite


def test_a_second_promotion_takes_the_next_id(settings, draft, document):
    promotion.promote(settings, draft)
    second = replace(draft, question="How many stores are in the Tristate Metro region?")
    result = promotion.promote(settings, second)
    assert result.pair_id == "Q47"
    assert len(gp.parse_document(document)) == 47


def test_the_written_pair_is_the_markdown_that_was_reported(settings, draft, document):
    result = promotion.promote(settings, draft)
    assert result.markdown in document.read_text()


# ---------------------------------------------------------------------------
# What is refused, and the document left untouched
# ---------------------------------------------------------------------------


def test_an_incomplete_draft_is_refused_without_touching_the_document(settings, document):
    before = document.read_text()
    with pytest.raises(PromotionError) as caught:
        promotion.promote(settings, Draft(title="t", question="q?"))
    assert len(caught.value.reasons) > 1
    assert document.read_text() == before


def test_a_draft_that_would_break_the_block_is_refused(settings, draft, document):
    before = document.read_text()
    draft.sql_code = "SELECT 1;\n```\n## Q99 - smuggled in"
    with pytest.raises(PromotionError, match="fence"):
        promotion.promote(settings, draft)
    assert document.read_text() == before


def test_a_document_that_already_does_not_parse_is_not_added_to(settings, draft, document):
    """A pair short of a field is invisible to the parser, not an error.

    The heading count is what notices, and it notices about the document as
    a whole -- so the honest response is to refuse to add anything until
    someone has looked at what is already broken.
    """
    document.write_text(document.read_text() + "\n## Q46 - half a pair\n\nnothing else\n")
    with pytest.raises(PromotionError, match="does not parse"):
        promotion.promote(settings, draft)


def test_a_missing_document_is_refused_by_name(settings, draft, document):
    document.unlink()
    with pytest.raises(PromotionError, match="cannot read"):
        promotion.promote(settings, draft)


def test_a_document_that_cannot_be_written_is_refused(settings, draft, document, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(promotion.shutil, "copy2", refuse)
    with pytest.raises(PromotionError, match="cannot write"):
        promotion.promote(settings, draft)


def test_a_full_golden_set_is_refused(settings, draft, document):
    document.write_text("## Q99 - the last one\n")
    with pytest.raises(ValueError, match="Q99 is the last id"):
        promotion.promote(settings, draft)


# ---------------------------------------------------------------------------
# The round trip itself, forced to fail
# ---------------------------------------------------------------------------


def test_a_render_that_loses_a_field_is_caught(settings, draft, monkeypatch):
    """The guarantee is not "it looked right", it is "it read back the same".

    A renderer that quietly dropped the SQL would still produce something
    the parser matches, so the check compares what went in against what came
    out rather than checking the text for markers.
    """
    original = promotion.render.render_pair

    def lossy(value: Draft, pair_id: str) -> str:
        return original(replace(value, sql_code="SELECT 'not what was asked for'"), pair_id)

    monkeypatch.setattr(promotion.render, "render_pair", lossy)
    with pytest.raises(PromotionError, match="does not survive a round trip"):
        promotion.promote(settings, draft)


def test_a_render_that_adds_a_second_pair_is_caught(settings, draft, monkeypatch):
    original = promotion.render.append_pair
    monkeypatch.setattr(
        promotion.render,
        "append_pair",
        lambda text, value, pair_id: original(text, value, pair_id)
        + original("", replace(value, title="stowaway"), "Q47"),
    )
    with pytest.raises(PromotionError, match="not 46"):
        promotion.promote(settings, draft)


def test_a_render_that_produces_nothing_parseable_is_caught(settings, draft, monkeypatch):
    monkeypatch.setattr(promotion.render, "append_pair", lambda text, value, pair_id: text)
    with pytest.raises(PromotionError, match="not 46"):
        promotion.promote(settings, draft)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_returns_the_block_without_writing(settings, draft, document):
    before = document.read_text()
    pair_id, markdown, problems = promotion.preview(settings, draft)

    assert (pair_id, problems) == ("Q46", [])
    assert markdown.startswith("## Q46 - ")
    assert document.read_text() == before


def test_preview_reports_problems_instead_of_a_block(settings):
    pair_id, markdown, problems = promotion.preview(settings, Draft(title="t"))
    assert (pair_id, markdown) == ("Q46", "")
    assert problems


def test_preview_reports_a_full_golden_set(settings, document):
    document.write_text("## Q99 - the last one\n")
    _, _, problems = promotion.preview(settings, Draft())
    assert any("golden set is full" in p for p in problems)


def test_preview_reports_an_unreadable_document(settings, document, draft):
    document.unlink()
    with pytest.raises(PromotionError, match="cannot read"):
        promotion.preview(settings, draft)


# ---------------------------------------------------------------------------
# Reloading, which is allowed to fail without un-promoting anything
# ---------------------------------------------------------------------------


def test_nothing_runs_when_both_loaders_are_switched_off(settings, draft):
    result = promotion.promote(settings, draft)
    assert [(s.name, s.ran) for s in result.steps] == [
        ("load_golden_pairs", False),
        ("embed_golden_pairs", False),
    ]
    # `all()` over no steps is True, and a log line claiming the stores were
    # rebuilt when nothing touched them is worse than no line at all.
    assert result.reloaded is False


def test_a_promotion_is_reloaded_only_when_every_step_that_ran_worked():
    base = dict(pair_id="Q46", chunk_id="c", suite="s", title="t", markdown="m", document="d")
    assert Promotion(**base, steps=[StepResult("a", True, True)]).reloaded is True
    assert Promotion(**base, steps=[StepResult("a", True, False)]).reloaded is False
    assert Promotion(**base, steps=[StepResult("a", True, True), StepResult("b", False)]).reloaded is True
    assert Promotion(**base, steps=[]).reloaded is False


def test_the_loaders_are_run_as_scripts_from_the_rag_directory(settings, draft, monkeypatch):
    calls: list[dict] = []

    class Done:
        returncode = 0
        stdout = "46 rows written"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return Done()

    monkeypatch.setattr(promotion.subprocess, "run", fake_run)
    result = promotion.promote(replace(settings, reload_context=True, reload_vectors=True), draft)

    assert [Path(call["argv"][1]).name for call in calls] == [
        "05_load_golden_pairs.py",
        "06_embed_golden_pairs.py",
    ]
    assert all(call["cwd"] == settings.rag_dir for call in calls)
    assert result.reloaded is True
    assert "46 rows written" in result.detail


def test_the_context_loader_is_pointed_at_the_document_that_was_written(settings, draft, monkeypatch):
    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        promotion.subprocess, "run", lambda argv, **kw: (seen.append(argv), Done())[1]
    )
    promotion.promote(replace(settings, reload_context=True, reload_vectors=False), draft)
    assert settings.document in seen[0]


def test_the_embedder_is_not_attempted_when_the_context_load_failed(settings, draft, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "could not connect to the context store"

    monkeypatch.setattr(promotion.subprocess, "run", lambda argv, **kw: Failed())
    result = promotion.promote(replace(settings, reload_context=True, reload_vectors=True), draft)

    context, vectors = result.steps
    assert (context.ran, context.ok) == (True, False)
    # Running it anyway would fail with a confusing error about the wrong
    # thing: it reads the rows the first step writes.
    assert vectors.ran is False
    assert "not attempted" in vectors.detail
    assert result.reloaded is False


def test_a_failed_reload_does_not_un_write_the_pair(settings, draft, document, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(promotion.subprocess, "run", lambda argv, **kw: Failed())
    result = promotion.promote(replace(settings, reload_context=True), draft)

    # The document is the source of truth and it is already correct. Rolling
    # it back because a downstream store did not rebuild would be undoing
    # the part that worked.
    assert result.pair_id == "Q46"
    assert len(gp.parse_document(document)) == 46
    assert result.reloaded is False


def test_a_loader_that_hangs_is_given_up_on(settings, draft, monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 600)

    monkeypatch.setattr(promotion.subprocess, "run", timeout)
    result = promotion.promote(replace(settings, reload_context=True), draft)
    assert "timed out" in result.steps[0].detail


def test_a_missing_loader_script_is_named(settings, draft, tmp_path):
    result = promotion.promote(replace(settings, reload_context=True, rag_dir=str(tmp_path)), draft)
    assert "is not there" in result.steps[0].detail
    assert result.steps[0].ok is False


def test_loader_output_is_bounded_before_it_reaches_a_database_column():
    assert promotion._tail("x" * 5000).startswith("...")
    assert len(promotion._tail("x" * 5000)) <= 603
    assert promotion._tail("a  b\n c") == "a b c"


def test_the_detail_names_every_step_and_what_it_did():
    base = dict(pair_id="Q46", chunk_id="c", suite="", title="", markdown="", document="")
    detail = Promotion(
        **base,
        steps=[
            StepResult("load_golden_pairs", True, True, "46 rows"),
            StepResult("embed_golden_pairs", True, False, "no host"),
            StepResult("skipped_one", False),
        ],
    ).detail
    assert "load_golden_pairs: ok 46 rows" in detail
    assert "embed_golden_pairs: FAILED no host" in detail
    assert "skipped_one: skipped" in detail


# ---------------------------------------------------------------------------
# The atomic write
# ---------------------------------------------------------------------------


def test_the_replacement_is_atomic_within_the_documents_own_directory(settings, draft, document, monkeypatch):
    """`os.replace` is only atomic within one filesystem.

    A temporary file in /tmp -- routinely a different one -- would turn the
    single operation this depends on into a copy that can be interrupted.
    """
    seen: dict = {}
    original = promotion.tempfile.mkstemp

    def watched(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(promotion.tempfile, "mkstemp", watched)
    promotion.promote(settings, draft)
    assert seen["dir"] == str(document.parent)


def test_no_candidate_files_are_left_behind(settings, draft, document):
    promotion.promote(settings, draft)
    leftovers = [p.name for p in document.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_the_documents_permissions_survive_the_replacement(settings, draft, document):
    document.chmod(0o640)
    promotion.promote(settings, draft)
    assert oct(document.stat().st_mode)[-3:] == "640"


def test_a_render_that_leaves_a_heading_without_a_pair_is_caught(settings, draft, monkeypatch):
    """The loader's own loud failure, surfaced as a refusal.

    `parse_document` counts `## Q..` headings and compares that to the number
    of pairs it parsed cleanly; a heading whose pair is missing a field makes
    those disagree and it raises. That is the one case the loader does notice
    on its own, and promotion has to stop rather than write it.
    """
    original = promotion.render.append_pair
    monkeypatch.setattr(
        promotion.render,
        "append_pair",
        lambda text, value, pair_id: original(text, value, pair_id) + "\n## Q47 - half a pair\n\nnothing\n",
    )
    with pytest.raises(PromotionError, match="does not parse"):
        promotion.promote(settings, draft)


def test_a_render_that_adds_the_wrong_pair_is_caught(settings, draft, monkeypatch):
    """The count goes up by one and the pair that arrived is not the one asked
    for -- which the count check alone would wave through."""
    original = promotion.render.render_pair
    monkeypatch.setattr(
        promotion.render, "render_pair", lambda value, pair_id: original(value, "Q47")
    )
    with pytest.raises(PromotionError, match="not in the reparsed document"):
        promotion.promote(settings, draft)
