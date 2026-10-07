"""The promotion path, against the real parser and a real file.

The step that matters is the round trip: render, then parse the candidate
document with `ragproc.golden_pairs.parse_document` -- the loader's own code,
not a copy of its rules -- and refuse unless the pair count went up by
exactly one and every field came back the way it went in. These tests are
mostly ways of making that step fail on purpose.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from nl2sql_review import promote as promotion
from nl2sql_review.promote import Promotion, PromotionError, StepResult
from nl2sql_review.render import Draft

from ragproc import golden_pairs as gp

from .conftest import BASE_PAIRS, NEXT_ID, pair_after, renumber_highest


# ---------------------------------------------------------------------------
# The happy path, measured against the real document
# ---------------------------------------------------------------------------


def test_a_pair_is_added_and_the_document_still_parses(settings, draft, document):
    before = len(gp.parse_document(document))
    result = promotion.promote(settings, draft)

    assert result.pair_id == NEXT_ID
    assert result.chunk_id == f"eval:{NEXT_ID.lower()}"
    assert (before, result.pairs_after) == (BASE_PAIRS, BASE_PAIRS + 1)

    pairs = gp.parse_document(document)
    assert len(pairs) == before + 1
    added = pairs[-1]
    assert added.pair_id == NEXT_ID
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
    assert result.pair_id == pair_after(NEXT_ID)
    assert len(gp.parse_document(document)) == BASE_PAIRS + 2


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


def test_the_hundredth_pair_is_promoted_and_the_loader_sees_it(settings, draft, document):
    """Until 5.5.1 Q99 was the last id the loader matched, and the hundredth
    promotion was refused rather than risk a pair the loader would not see."""
    document.write_text(renumber_highest(document.read_text(), "Q99"))
    result = promotion.promote(settings, draft)
    assert (result.pair_id, result.chunk_id) == ("Q100", "eval:q100")
    pairs = gp.parse_document(document)
    assert (len(pairs), pairs[-1].pair_id, pairs[-1].question) == (BASE_PAIRS + 1, "Q100", draft.question)


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
    with pytest.raises(PromotionError, match=f"not {BASE_PAIRS + 1}"):
        promotion.promote(settings, draft)


def test_a_render_that_produces_nothing_parseable_is_caught(settings, draft, monkeypatch):
    monkeypatch.setattr(promotion.render, "append_pair", lambda text, value, pair_id: text)
    with pytest.raises(PromotionError, match=f"not {BASE_PAIRS + 1}"):
        promotion.promote(settings, draft)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_returns_the_block_without_writing(settings, draft, document):
    before = document.read_text()
    pair_id, markdown, problems = promotion.preview(settings, draft)

    assert (pair_id, problems) == (NEXT_ID, [])
    assert markdown.startswith(f"## {NEXT_ID} - ")
    assert document.read_text() == before


def test_preview_reports_problems_instead_of_a_block(settings):
    pair_id, markdown, problems = promotion.preview(settings, Draft(title="t"))
    assert (pair_id, markdown) == (NEXT_ID, "")
    assert problems


def test_preview_offers_q100_after_q99(settings, draft, document):
    document.write_text(renumber_highest(document.read_text(), "Q99"))
    pair_id, markdown, problems = promotion.preview(settings, draft)
    assert (pair_id, problems) == ("Q100", [])
    assert "## Q100 - " in markdown


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


class Report:
    def __init__(self, text: str, complete: bool = True) -> None:
        self.text, self.complete = text, complete

    def summary(self) -> str:
        return self.text


class FakeLoaders:
    """`ragproc.loaders` as the review service calls it: in this process,
    each store's URL an argument (V6-27)."""

    def __init__(self, fail: dict[str, BaseException] | None = None) -> None:
        self.fail = fail or {}
        self.calls: list[tuple] = []

    def _done(self, name: str, *args, text: str = "46 rows written"):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]
        return Report(text)

    def load_golden_pairs(self, document, db_url, **options):
        return self._done("load_golden_pairs", str(document), db_url)

    def embed_golden_pairs(self, chunk_db_url, vector_db_url, embedder, **options):
        return self._done("embed_golden_pairs", chunk_db_url, vector_db_url, embedder.model_name)


@pytest.fixture
def loaders(monkeypatch):
    fake = FakeLoaders()
    monkeypatch.setattr(promotion, "_loaders", lambda settings: fake)
    return fake


def test_the_loaders_are_called_in_this_process_with_each_store_as_an_argument(settings, draft, loaders):
    result = promotion.promote(replace(settings, reload_context=True, reload_vectors=True), draft)
    assert loaders.calls == [
        ("load_golden_pairs", str(settings.document_path), settings.chunk_db_url),
        ("embed_golden_pairs", settings.chunk_db_url, settings.vector_db_url, settings.embed_model),
    ]
    assert result.reloaded is True
    assert "46 rows written" in result.detail


def test_the_context_loader_is_pointed_at_the_document_that_was_written(settings, draft, loaders):
    promotion.promote(replace(settings, reload_context=True, reload_vectors=False), draft)
    assert [call[:2] for call in loaders.calls] == [("load_golden_pairs", str(settings.document_path))]


def test_the_embedder_is_not_attempted_when_the_context_load_failed(settings, draft, loaders):
    loaders.fail["load_golden_pairs"] = psycopg.OperationalError("could not connect to the context store")
    result = promotion.promote(replace(settings, reload_context=True, reload_vectors=True), draft)

    context, vectors = result.steps
    assert (context.ran, context.ok) == (True, False)
    assert "OperationalError: could not connect to the context store" in context.detail
    # Running it anyway would fail with a confusing error about the wrong
    # thing: it reads the rows the first step writes.
    assert vectors.ran is False
    assert "not attempted" in vectors.detail
    assert result.reloaded is False


def test_a_failed_reload_does_not_un_write_the_pair(settings, draft, document, loaders):
    loaders.fail["load_golden_pairs"] = psycopg.OperationalError("boom")
    result = promotion.promote(replace(settings, reload_context=True), draft)

    # The document is the source of truth and it is already correct. Rolling
    # it back because a downstream store did not rebuild would be undoing
    # the part that worked.
    assert result.pair_id == NEXT_ID
    assert len(gp.parse_document(document)) == BASE_PAIRS + 1
    assert result.reloaded is False


def test_the_embedding_host_is_waited_on_for_the_reload_timeout_and_no_longer(settings):
    """In this process a load cannot be killed, so the one part that can
    take minutes -- a request to the embedding host -- is what is bounded."""
    embedder = promotion.embedder_factory(replace(settings, reload_timeout_seconds=42))()
    assert embedder.timeout == 42 and embedder.model_name == settings.embed_model


def test_a_loader_that_breaks_is_a_failed_step_not_a_crash(settings, draft, loaders):
    """Whatever a load does wrong -- a bug among it -- the pair is in the
    document by then, so it is reported, not raised."""
    loaders.fail["load_golden_pairs"] = TypeError("unexpected keyword argument")
    result = promotion.promote(replace(settings, reload_context=True), draft)
    assert result.pair_id == NEXT_ID
    assert (result.steps[0].ran, result.steps[0].ok) == (True, False)
    assert "TypeError: unexpected keyword argument" in result.steps[0].detail


def test_a_load_that_says_it_is_incomplete_is_a_failed_step(settings, draft, monkeypatch):
    class Partial(FakeLoaders):
        def load_golden_pairs(self, document, db_url, **options):
            return Report("rows written; vectors FAILED", complete=False)

    monkeypatch.setattr(promotion, "_loaders", lambda settings: Partial())
    result = promotion.promote(replace(settings, reload_context=True), draft)
    assert result.steps[0].ok is False and "vectors FAILED" in result.steps[0].detail


# --- one writer at a time (V6-27) ----------------------------------------------------


def test_a_write_holds_the_documents_directory_against_other_processes(tmp_path):
    import fcntl
    import os

    with promotion.writing(tmp_path):
        other = os.open(str(tmp_path), os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
    other = os.open(str(tmp_path), os.O_RDONLY)
    try:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(other)


def test_two_writes_in_this_process_take_turns(tmp_path):
    import threading
    import time

    order: list[str] = []
    inside = threading.Event()

    def first():
        with promotion.writing(tmp_path):
            order.append("first in")
            inside.set()
            time.sleep(0.2)
            order.append("first out")

    thread = threading.Thread(target=first)
    thread.start()
    inside.wait(5)
    with promotion.writing(tmp_path):
        order.append("second in")
    thread.join()
    assert order == ["first in", "first out", "second in"]


def test_a_directory_that_cannot_be_locked_still_takes_turns_here(tmp_path, monkeypatch):
    import fcntl

    def refuse(handle, operation):
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", refuse)
    with promotion.writing(tmp_path):
        pass
    with promotion.writing(tmp_path / "not-there"):
        pass


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


# ---------------------------------------------------------------------------
# Withdrawing a promoted pair (5.4)
# ---------------------------------------------------------------------------


def test_a_withdrawn_pair_leaves_the_document_as_it_was_before(settings, draft, document):
    original = document.read_text()
    promotion.promote(settings, draft)
    promoted = document.read_text()

    result = promotion.withdraw(settings, NEXT_ID)

    assert document.read_text() == original
    assert (result.found, result.pairs_before, result.pairs_after) == (True, BASE_PAIRS + 1, BASE_PAIRS)
    assert Path(result.backup).read_text() == promoted
    assert result.document == str(document)


def test_the_withdrawn_pair_comes_back_as_the_draft_that_would_promote_it(settings, draft):
    draft.extra_meta = {"source": "feedback"}
    promotion.promote(settings, draft)

    withdrawn = promotion.withdraw(settings, NEXT_ID).draft

    for name in ("title", "question", "tables", "keywords", "reasoning_target", "sql_code", "result"):
        assert getattr(withdrawn, name) == getattr(draft, name).strip(), name
    assert withdrawn.extra_meta == {"source": "feedback"}
    assert "Promoted from web GUI feedback" in withdrawn.translation_note


def test_a_pair_the_document_no_longer_holds_is_reported_not_refused(settings, document):
    before = document.read_text()
    result = promotion.withdraw(settings, "Q98")
    assert (result.found, result.backup, result.draft) == (False, "", None)
    assert document.read_text() == before


def test_the_stores_are_reloaded_after_a_withdrawal_even_of_a_pair_already_gone(settings, loaders):
    """They follow the document, whoever last edited it."""
    result = promotion.withdraw(replace(settings, reload_context=True, reload_vectors=True), "Q98")
    assert [call[0] for call in loaders.calls] == ["load_golden_pairs", "embed_golden_pairs"]
    assert result.reloaded is True


def test_with_both_loaders_off_a_withdrawal_is_not_called_reloaded(settings, draft):
    promotion.promote(settings, draft)
    assert promotion.withdraw(settings, NEXT_ID).reloaded is False


def test_a_document_that_does_not_parse_is_not_taken_from(settings, draft, document):
    promotion.promote(settings, draft)
    broken = document.read_text() + "\n## Q97 - half a pair\n\nnothing else\n"
    document.write_text(broken)

    with pytest.raises(PromotionError, match="does not parse, so nothing can be taken out"):
        promotion.withdraw(settings, NEXT_ID)
    assert document.read_text() == broken


def test_a_removal_that_would_change_another_pair_is_refused(settings, draft, document, monkeypatch):
    promotion.promote(settings, draft)
    before = document.read_text()
    original = promotion.render.remove_pair
    monkeypatch.setattr(
        promotion.render,
        "remove_pair",
        lambda text, pair_id: original(text, pair_id).replace('**Question:** "', '**Question:** "Edited: ', 1),
    )

    with pytest.raises(PromotionError, match="would change other pairs as well"):
        promotion.withdraw(settings, NEXT_ID)
    assert document.read_text() == before


def test_a_removal_that_would_break_the_document_is_refused(settings, draft, document, monkeypatch):
    promotion.promote(settings, draft)
    before = document.read_text()
    original = promotion.render.remove_pair
    monkeypatch.setattr(
        promotion.render,
        "remove_pair",
        lambda text, pair_id: original(text, pair_id) + "\n## Q97 - stowaway heading\n",
    )

    with pytest.raises(PromotionError, match="would leave a document that does not parse"):
        promotion.withdraw(settings, NEXT_ID)
    assert document.read_text() == before


def test_the_loaders_are_rag_s_own_when_the_image_has_them(settings):
    """Not faked: the module a promotion calls is `rag/ragproc/loaders.py`."""
    loaders = promotion._loaders(settings)
    assert loaders.__name__ == "ragproc.loaders"
    assert hasattr(loaders, "load_golden_pairs") and hasattr(loaders, "load_snippets")
