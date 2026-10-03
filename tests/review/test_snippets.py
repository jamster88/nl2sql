"""Writing SQL snippets into their document: `nl2sql_review.snippets`.

Against a copy of the real document, as the golden pairs' tests are, and for
the same reason: every write is a bet that the loader's parser reads the
result the way it was meant, and that bet is only worth anything against the
document the loader actually reads.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from nl2sql_review import promote as promotion_module
from nl2sql_review import snippets as sn
from nl2sql_review.promote import StepResult
from nl2sql_review.snippets import SnippetDraft, SnippetError, SnippetMissing

from .conftest import REAL_SNIPPETS

BASE = len(re.findall(r"^## S\d{2,} - ", REAL_SNIPPETS.read_text(), re.M))
NEXT = sn.next_snippet_id(REAL_SNIPPETS.read_text())


def parse(path: Path):
    from ragproc import snippets as parser

    return parser.parse_document(path)


# ---------------------------------------------------------------------------
# The draft and its rules
# ---------------------------------------------------------------------------


def test_a_complete_draft_has_no_problems(snippet_draft):
    assert sn.problems(snippet_draft, NEXT) == []


def test_every_missing_field_is_named_at_once():
    found = sn.problems(SnippetDraft(), "S40")
    assert [p.split(" is empty")[0] for p in found] == list(sn.REQUIRED)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"kind": "having"}, "'having' is not a kind of snippet"),
        ({"name": "two\nlines"}, "name spans more than one line"),
        ({"means": "two\nlines"}, "means spans more than one line"),
        ({"note": "two\nlines"}, "note spans more than one line"),
        ({"tables": " , "}, "tables has no entries"),
        ({"keywords": ","}, "keywords has no entries"),
        ({"means": "a ``` fence"}, "means contains a ``` fence"),
        ({"note": "a ``` fence"}, "note contains a ``` fence"),
        ({"sql": "d.is_holiday; DROP TABLE x"}, "contains a semicolon"),
        ({"sql": "d.is_holiday -- hidden"}, "contains a comment"),
    ],
)
def test_each_rule_says_what_is_wrong(snippet_draft, change, message):
    assert any(message in p for p in sn.problems(replace(snippet_draft, **change), "S40"))


def test_an_id_that_is_not_one_is_refused(snippet_draft):
    assert sn.problems(snippet_draft, "X1") == ["'X1' is not a snippet id; they are S and at least two digits"]


def test_the_draft_is_cleaned_before_it_is_written():
    clean = SnippetDraft(
        name=" N ", kind=" Filter ", tables="a ,b,, c", keywords=" x, y ", means=" m ",
        applies_to=" t ;", sql=" s; ", note=" n ",
    ).cleaned()
    assert clean.as_dict() == {
        "name": "N", "kind": "filter", "tables": "a, b, c", "keywords": "x, y", "means": "m",
        "applies_to": "t", "sql": "s", "note": "n",
    }


def test_a_draft_from_the_wire_drops_what_it_does_not_know():
    draft = SnippetDraft.from_mapping({"name": "N", "kind": 3, "extra": "x"})
    assert draft == SnippetDraft(name="N")


def test_ids_are_the_highest_plus_one_never_a_gap():
    assert sn.next_snippet_id("") == "S01"
    assert sn.next_snippet_id("## S01 - a\n## S07 - b\n") == "S08"
    assert sn.next_snippet_id("## S99 - a\n") == "S100"


# ---------------------------------------------------------------------------
# Where a snippet goes, and what is left when it goes
# ---------------------------------------------------------------------------


def test_a_rendered_snippet_reads_back_as_written(snippet_draft):
    from ragproc import snippets as parser

    [read] = parser.parse_text(sn.render_snippet(snippet_draft, "S40"))
    assert (read.snippet_id, read.chunk_id, read.kind, read.sql) == ("S40", "snippet:s40", "filter", "d.is_holiday")
    assert read.note == sn.DEFAULT_NOTE


def test_a_new_snippet_goes_at_the_end_of_its_kinds_section(snippet_draft):
    text = REAL_SNIPPETS.read_text()
    updated = sn.insert_snippet(text, snippet_draft, "S40")
    filters, measures = updated.index("# Filters\n"), updated.index("# Measures\n")
    assert filters < updated.index("## S40 - Holiday sales") < measures
    # The section above it and the heading below it are laid out as before.
    assert "**Note:** 24 of the 47 promo cycles.\n\n## S40 - " in updated
    assert "validated against the retail database.\n\n# Measures\n" in updated


def test_a_new_snippet_of_the_last_kind_goes_at_the_end(snippet_draft):
    text = REAL_SNIPPETS.read_text()
    updated = sn.insert_snippet(text, replace(snippet_draft, kind="dimension"), "S40")
    assert updated.endswith(sn.render_snippet(replace(snippet_draft, kind="dimension"), "S40"))
    assert "\n\n## S40 - " in updated


def test_a_kind_with_no_section_yet_starts_one():
    draft = SnippetDraft(name="N", kind="join", tables="t", keywords="k", means="m", applies_to="t a", sql="JOIN u b ON b.x = a.x")
    updated = sn.insert_snippet("# SQL Snippets\n\nProse.", draft, "S01")
    assert updated.startswith("# SQL Snippets\n\nProse.\n\n# Joins\n\n## S01 - N\n")


def test_removing_a_snippet_leaves_the_rest_as_it_was(snippet_draft):
    text = REAL_SNIPPETS.read_text()
    added = sn.insert_snippet(text, snippet_draft, "S40")
    assert sn.remove_snippet(added, "S40") == text
    last = re.findall(r"^## (S\d+) - ", text, re.M)[-1]
    trimmed = sn.remove_snippet(text, last)
    assert trimmed.endswith("\n") and not trimmed.endswith("\n\n")
    assert sn.remove_snippet(text, "S99") is None


def test_a_changed_snippet_is_rewritten_in_place(snippet_draft):
    text = REAL_SNIPPETS.read_text()
    changed = sn.replace_snippet(text, "S11", replace(snippet_draft, name="Holidays, renamed", kind="filter"))
    assert changed.index("## S11 - Holidays, renamed") < changed.index("## S12 - ")
    assert changed.count("## S11 - ") == 1


def test_a_snippet_whose_kind_changed_moves_to_its_new_section(snippet_draft):
    text = REAL_SNIPPETS.read_text()
    moved = sn.replace_snippet(text, "S11", replace(snippet_draft, kind="dimension", sql="d.is_holiday"))
    assert moved.index("# Dimensions\n") < moved.index("## S11 - ")
    assert sn.replace_snippet(text, "S99", snippet_draft) is None


# ---------------------------------------------------------------------------
# Writing, through the loader's parser
# ---------------------------------------------------------------------------


def test_adding_writes_the_document_keeps_a_backup_and_skips_a_disabled_reload(settings, snippet_draft):
    outcome = sn.add(settings, snippet_draft)
    assert (outcome.action, outcome.snippet_id, outcome.chunk_id) == ("added", NEXT, sn.chunk_id_for(NEXT))
    assert (outcome.snippets_before, outcome.snippets_after) == (BASE, BASE + 1)
    assert outcome.markdown == sn.render_snippet(snippet_draft, NEXT)
    assert Path(outcome.backup).read_text() == REAL_SNIPPETS.read_text()
    assert [s.snippet_id for s in parse(settings.snippets_document_path)][-1] != NEXT  # filed by kind
    assert NEXT in {s.snippet_id for s in parse(settings.snippets_document_path)}
    assert [(s.name, s.ran) for s in outcome.steps] == [("load_snippets", False)]
    assert outcome.reloaded is False


def test_a_draft_with_problems_writes_nothing(settings, snippet_draft):
    before = settings.snippets_document_path.read_text()
    with pytest.raises(SnippetError) as raised:
        sn.add(settings, replace(snippet_draft, means=""))
    assert raised.value.reasons == ["means is empty -- needs what it means and which questions it is for"]
    with pytest.raises(SnippetError):
        sn.change(settings, "S11", replace(snippet_draft, kind="nope"))
    assert settings.snippets_document_path.read_text() == before


def test_changing_and_removing_report_what_they_did(settings, snippet_draft):
    changed = sn.change(settings, "S11", replace(snippet_draft, name="Public holidays"))
    assert (changed.action, changed.snippets_before, changed.snippets_after) == ("changed", BASE, BASE)
    assert next(s for s in parse(settings.snippets_document_path) if s.snippet_id == "S11").name == "Public holidays"

    removed = sn.delete(settings, "S11")
    assert (removed.action, removed.markdown, removed.snippets_after) == ("removed", "", BASE - 1)
    assert "S11" not in {s.snippet_id for s in parse(settings.snippets_document_path)}


def test_a_snippet_that_is_not_there_is_missing_not_refused(settings, snippet_draft):
    with pytest.raises(SnippetMissing, match="there is no snippet S99"):
        sn.change(settings, "S99", snippet_draft)
    with pytest.raises(SnippetMissing, match="there is no snippet S99"):
        sn.delete(settings, "S99")


def test_a_document_that_does_not_parse_is_never_written_over(settings, snippet_draft):
    path = settings.snippets_document_path
    path.write_text(path.read_text().replace("**Means:** Restricts to the days", "**Meaning:** Restricts to the days"))
    broken = path.read_text()
    with pytest.raises(SnippetError, match="the snippet document does not parse"):
        sn.add(settings, snippet_draft)
    assert path.read_text() == broken


def test_a_write_that_would_change_another_snippet_is_refused(settings, snippet_draft, monkeypatch):
    monkeypatch.setattr(sn, "insert_snippet", lambda doc, draft, sid: doc.replace("d.is_holiday", "d.is_holiday OR true") + "\n" + sn.render_snippet(draft, sid))
    with pytest.raises(SnippetError, match="would change other snippets as well"):
        sn.add(settings, snippet_draft)


def test_a_section_that_does_not_read_back_is_refused(settings, snippet_draft, monkeypatch):
    monkeypatch.setattr(sn, "insert_snippet", lambda doc, draft, sid: doc)
    with pytest.raises(SnippetError, match="is not in the rewritten document"):
        sn.add(settings, snippet_draft)
    monkeypatch.setattr(sn, "remove_snippet", lambda doc, sid: doc)
    with pytest.raises(SnippetError, match="S11 is still in the rewritten document"):
        sn.delete(settings, "S11")


def test_a_section_that_reads_back_differently_names_the_field(settings, snippet_draft, monkeypatch):
    real = sn.insert_snippet
    monkeypatch.setattr(
        sn, "insert_snippet", lambda doc, draft, sid: real(doc, draft, sid).replace(f"## {sid} - Holiday sales", f"## {sid} - Holiday salez")
    )
    with pytest.raises(SnippetError) as raised:
        sn.add(settings, snippet_draft)
    assert raised.value.reasons[0] == "the snippet does not survive a round trip"
    assert "name: wrote 'Holiday sales', read back 'Holiday salez'" in raised.value.reasons


def test_an_unreadable_or_unwritable_document_is_a_refusal(settings, snippet_draft, monkeypatch):
    missing = replace(settings, snippets_document=str(settings.snippets_document_path.parent / "absent.md"))
    with pytest.raises(SnippetError, match="cannot read the snippet document"):
        sn.add(missing, snippet_draft)

    def refuse(path, text):
        raise promotion_module.PromotionError(["cannot write it"])

    monkeypatch.setattr(promotion_module, "_write_atomically", refuse)
    with pytest.raises(SnippetError, match="cannot write it"):
        sn.add(settings, snippet_draft)


def test_the_reload_runs_the_loader_against_the_store_with_the_readers_credentials(settings, monkeypatch):
    seen = {}

    def run(config, script, args, *, enabled, env=None):
        seen.update(script=script, args=args, enabled=enabled, env=env)
        return StepResult(name="load_snippets", ran=True, ok=True, detail="32 rows written")

    monkeypatch.setattr(promotion_module, "_run_loader", run)
    configured = replace(settings, reload_snippets=True, snippets_reader_user="r", snippets_reader_password="pw")
    [step] = sn.reload(configured)
    assert step.ok and seen["script"] == "07_load_snippets.py" and seen["enabled"] is True
    assert seen["args"] == [
        configured.snippets_document, "--db-url", configured.snippets_db_url,
        "--ollama-url", configured.ollama_url, "--model", configured.embed_model,
    ]
    assert (seen["env"]["SNIPPETS_READER_USER"], seen["env"]["SNIPPETS_READER_PASSWORD"]) == ("r", "pw")


def test_preview_shows_the_section_or_says_why_not(settings, snippet_draft):
    assert sn.preview(settings, snippet_draft) == (NEXT, sn.render_snippet(snippet_draft, NEXT), [])
    assert sn.preview(settings, snippet_draft, "S11")[1].startswith("## S11 - Holiday sales")
    snippet_id, markdown, problems = sn.preview(settings, snippet_draft, "S99")
    assert (snippet_id, markdown, problems) == ("S99", "", ["there is no snippet S99 in the document"])


def test_the_listing_is_the_document_parsed_with_its_next_id_and_hash(settings):
    from ragproc import snippets as parser

    snippets, next_id, digest = sn.listing(settings)
    assert (len(snippets), next_id) == (BASE, NEXT)
    assert digest == parser.document_hash(REAL_SNIPPETS.read_bytes())


def test_a_crlf_checkout_lists_the_same_snippets_and_is_hashed_as_its_bytes(settings):
    """What `sha256sum` sees in launch.sh, and the loader records -- not the
    text with its line endings translated, which would never match."""
    from ragproc import snippets as parser

    crlf = REAL_SNIPPETS.read_bytes().replace(b"\n", b"\r\n")
    settings.snippets_document_path.write_bytes(crlf)
    snippets, next_id, digest = sn.listing(settings)
    assert (len(snippets), next_id) == (BASE, NEXT)
    assert digest == parser.document_hash(crlf) != parser.document_hash(REAL_SNIPPETS.read_bytes())


# ---------------------------------------------------------------------------
# The store, beside the document
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, answers):
        self.answers = list(answers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement):
        return _Cursor(self.answers.pop(0))


def test_a_store_that_holds_this_document_is_current():
    conn = _Conn([(True, True, True), (32,), (32,), ("abc",)])
    status = sn.store_status("u", "abc", connect=lambda url, connect_timeout: conn)
    assert status == {
        "reachable": True, "snippets": 32, "embedded": 32, "current": True,
        "detail": "32 snippets, all embedded, loaded from this document",
    }


def test_a_store_behind_its_document_says_so():
    conn = _Conn([(True, False, True), (31,), None])
    status = sn.store_status("u", "abc", connect=lambda url, connect_timeout: conn)
    assert (status["current"], status["embedded"]) == (False, 0)
    assert status["detail"] == "31 snippets, 0 embedded -- behind the document until the next load"


def test_a_store_never_loaded_and_one_that_cannot_be_reached():
    conn = _Conn([(False, False, False)])
    assert sn.store_status("u", "abc", connect=lambda url, connect_timeout: conn)["detail"].startswith("never loaded")

    def down(url, connect_timeout):
        raise OSError("connection refused")

    assert sn.store_status("u", "abc", connect=down) == {"reachable": False, "detail": "OSError: connection refused"}
