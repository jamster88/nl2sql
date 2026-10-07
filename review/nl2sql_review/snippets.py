"""Writing SQL snippets into their document, and the store after it.

`context_questions/sql_snippets.md` is the snippets' source of truth, as the
golden question document is the golden pairs': `rag/07_load_snippets.py`
builds the snippet store from it and deletes what it no longer holds. So a
snippet is added, changed or removed by rewriting the document, and the
store follows -- the same order, and for the same reason, as a promotion.

Every write is held to the standard a promotion is:

1. **The draft is checked** against the format's rules, and the snippet
   against the retail database (`snippet_validation.py`) by the route that
   calls this -- nothing here runs SQL.
2. **The new document is parsed with the loader's own parser**
   (`ragproc.snippets.parse_text`), and has to hold exactly the snippets it
   should: one more, one fewer, or the same number with only the one
   changed, every field of the written one read back as it went in. A
   section the parser cannot read is not reported -- it is not seen -- so
   this is the step that turns a silently dropped snippet into a refusal.
3. **The document is replaced atomically**, its previous version kept.
4. **The store is reloaded**, and a failure there is reported rather than
   undone: the document is the fact.

A new snippet goes at the end of its kind's section (`# Joins`, `# Filters`,
...), so the document stays readable by kind and the diff is one block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import promote as promotion_module
from .promote import PromotionError, StepResult
from .settings import ReviewSettings
from .snippet_validation import KINDS, static_problems
from nl2sql_common.errors import DATABASE_ERRORS

#: What the snippet load is called in a step's report.
STEP = "load_snippets"

#: The `#` heading each kind's snippets sit under.
SECTIONS = {"join": "Joins", "filter": "Filters", "measure": "Measures", "dimension": "Dimensions"}

SNIPPET_ID_RE = re.compile(r"^S(\d{2,})$")
HEADING_RE = re.compile(r"^## S(\d{2,}) - ", re.M)
_SECTION_RE = re.compile(r"^# (?P<title>.+?)\s*$", re.M)
_ANY_HEADING_RE = re.compile(r"^#{1,2} ", re.M)

REQUIRED = {
    "name": "a short name for the `## Snn -` heading",
    "kind": f"one of {', '.join(KINDS)}",
    "tables": "the tables it uses, comma-separated",
    "keywords": "the phrases a question uses for it, comma-separated",
    "means": "what it means and which questions it is for",
    "applies_to": "the FROM clause it is written against",
    "sql": "the snippet itself",
}

DEFAULT_NOTE = "Written in the curation interface and validated against the retail database."


class SnippetError(PromotionError):
    """Nothing was written: the document is as it was."""


class SnippetMissing(SnippetError):
    """The document holds no snippet by that id."""


@dataclass
class SnippetDraft:
    name: str = ""
    kind: str = ""
    tables: str = ""
    keywords: str = ""
    means: str = ""
    applies_to: str = ""
    sql: str = ""
    note: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SnippetDraft":
        """From whatever JSON arrived: unknown keys dropped, non-strings emptied."""
        return cls(**{f: data.get(f) if isinstance(data.get(f), str) else "" for f in cls.__dataclass_fields__})

    def as_dict(self) -> dict[str, str]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}

    def cleaned(self) -> "SnippetDraft":
        """As it will be written: trimmed, the kind lower-cased, lists normalised."""
        return SnippetDraft(
            name=self.name.strip(),
            kind=self.kind.strip().lower(),
            tables=_csv(self.tables),
            keywords=_csv(self.keywords),
            means=self.means.strip(),
            applies_to=self.applies_to.strip().rstrip(";").strip(),
            sql=self.sql.strip().rstrip(";").strip(),
            note=self.note.strip(),
        )


@dataclass
class Outcome:
    """What a write did, in enough detail to show on the screen."""

    action: str  # added | changed | removed
    snippet_id: str
    chunk_id: str
    document: str
    markdown: str = ""
    backup: str = ""
    snippets_before: int = 0
    snippets_after: int = 0
    steps: list[StepResult] = field(default_factory=list)

    @property
    def reloaded(self) -> bool:
        return promotion_module._reloaded(self.steps)


def _csv(value: str) -> str:
    return ", ".join(part.strip() for part in value.split(",") if part.strip())


def _parser():
    from ragproc import snippets as sn  # type: ignore[import-not-found]

    return sn


def chunk_id_for(snippet_id: str) -> str:
    return f"snippet:{snippet_id.lower()}"


def problems(draft: SnippetDraft, snippet_id: str = "S01") -> list[str]:
    """Everything that would stop the draft becoming a section, in one pass."""
    found: list[str] = []
    for name, description in REQUIRED.items():
        if not getattr(draft, name).strip():
            found.append(f"{name} is empty -- needs {description}")
    if not SNIPPET_ID_RE.match(snippet_id):
        found.append(f"{snippet_id!r} is not a snippet id; they are S and at least two digits")
    kind = draft.kind.strip().lower()
    if kind and kind not in KINDS:
        found.append(f"{draft.kind!r} is not a kind of snippet; it is one of {', '.join(KINDS)}")
    for name in ("name", "means", "note"):
        if "\n" in getattr(draft, name).strip():
            found.append(f"{name} spans more than one line; the document reads it as one")
    for name in ("tables", "keywords"):
        value = getattr(draft, name)
        if value.strip() and not _csv(value):
            found.append(f"{name} has no entries; it is a comma-separated list")
    for name in ("means", "note"):
        if "```" in getattr(draft, name):
            found.append(f"{name} contains a ``` fence, which ends its block early")
    if kind in KINDS and draft.applies_to.strip() and draft.sql.strip():
        found.extend(static_problems(kind, draft.applies_to.strip(), draft.sql.strip()))
    return found


def next_snippet_id(document: str) -> str:
    """The id after the highest in use -- never a gap's, so a removed
    snippet's id is not given to a different one."""
    numbers = [int(m.group(1)) for m in HEADING_RE.finditer(document)]
    return f"S{(max(numbers) + 1) if numbers else 1:02d}"


def render_snippet(draft: SnippetDraft, snippet_id: str) -> str:
    """One section, as `ragproc.snippets.ENTRY_RE` reads it. Every blank line is in the pattern."""
    clean = draft.cleaned()
    return (
        f"## {snippet_id} - {clean.name}\n"
        "\n"
        "```meta\n"
        f"chunk_id: {chunk_id_for(snippet_id)}\n"
        f"kind: {clean.kind}\n"
        f"tables: {clean.tables}\n"
        f"keywords: {clean.keywords}\n"
        "```\n"
        "\n"
        f"**Means:** {clean.means}\n"
        "\n"
        "**Applies to:**\n"
        "\n"
        f"```sql\n{clean.applies_to}\n```\n"
        "\n"
        "**SQL:**\n"
        "\n"
        f"```sql\n{clean.sql}\n```\n"
        "\n"
        f"**Note:** {clean.note or DEFAULT_NOTE}\n"
    )


def _block(document: str, snippet_id: str) -> tuple[int, int] | None:
    """Where a snippet's section starts and ends: its heading to the next heading."""
    heading = re.search(rf"^## {re.escape(snippet_id)} - .*$", document, re.M)
    if heading is None:
        return None
    following = _ANY_HEADING_RE.search(document, heading.end())
    return heading.start(), following.start() if following else len(document)


def insert_snippet(document: str, draft: SnippetDraft, snippet_id: str) -> str:
    """The document with the snippet at the end of its kind's section.

    A section that does not exist yet is started at the end of the document.
    """
    body = document if document.endswith("\n") else document + "\n"
    section = render_snippet(draft, snippet_id)
    title = SECTIONS[draft.kind.strip().lower()]
    heading = re.search(rf"^# {re.escape(title)}\s*$", body, re.M)
    if heading is None:
        return f"{body}\n# {title}\n\n{section}"
    after = _SECTION_RE.search(body, heading.end())
    if after is None:
        return f"{body.rstrip()}\n\n{section}"
    head, tail = body[: after.start()].rstrip(), body[after.start():]
    return f"{head}\n\n{section}\n{tail}"


def remove_snippet(document: str, snippet_id: str) -> str | None:
    """The document without the snippet's section, or None if it has none."""
    span = _block(document, snippet_id)
    if span is None:
        return None
    start, end = span
    remaining = document[:start] + document[end:]
    return remaining if end < len(document) else remaining.rstrip() + "\n"


def replace_snippet(document: str, snippet_id: str, draft: SnippetDraft) -> str | None:
    """The document with the snippet rewritten -- in place, or moved to its
    new kind's section when the kind changed."""
    span = _block(document, snippet_id)
    if span is None:
        return None
    start, end = span
    kind = re.search(r"^kind:\s*(\w+)", document[start:end], re.M)
    if kind is not None and kind.group(1).lower() == draft.kind.strip().lower():
        tail = document[end:]
        return document[:start] + render_snippet(draft, snippet_id) + ("\n" + tail if tail else "")
    return insert_snippet(remove_snippet(document, snippet_id) or "", draft, snippet_id)


def _fields(snippet) -> tuple:
    return (snippet.chunk_id, snippet.name, snippet.kind, snippet.tables, snippet.keywords,
            snippet.means, snippet.applies_to, snippet.sql, snippet.note)


def _parse(sn, text: str, what: str) -> list:
    try:
        return sn.parse_text(text)
    except ValueError as exc:
        raise SnippetError([f"{what} does not parse: {exc}"]) from exc


def _round_trip(document: str, updated: str, *, snippet_id: str, draft: SnippetDraft | None) -> tuple[int, int]:
    """Parse both versions with the loader's parser, and hold the change to what was meant.

    Every snippet but `snippet_id` must come back exactly as it was, and
    `snippet_id` must come back as `draft` -- or be gone, when `draft` is None.
    """
    sn = _parser()
    before = _parse(sn, document, "the snippet document")
    after = _parse(sn, updated, "the rewritten snippet document")
    kept_before = {s.snippet_id: _fields(s) for s in before if s.snippet_id != snippet_id}
    kept_after = {s.snippet_id: _fields(s) for s in after if s.snippet_id != snippet_id}
    if kept_before != kept_after:
        raise SnippetError([f"writing {snippet_id} would change other snippets as well, so it was not written"])
    written = next((s for s in after if s.snippet_id == snippet_id), None)
    if draft is None:
        if written is not None:
            raise SnippetError([f"{snippet_id} is still in the rewritten document"])
        return len(before), len(after)
    if written is None:
        raise SnippetError([f"{snippet_id} is not in the rewritten document; the rendered section does not parse"])
    clean = draft.cleaned()
    expected = (chunk_id_for(snippet_id), clean.name, clean.kind, clean.tables, clean.keywords,
                clean.means, clean.applies_to, clean.sql, clean.note or DEFAULT_NOTE)
    mismatches = [
        f"{name}: wrote {wrote!r}, read back {read!r}"
        for name, wrote, read in zip(
            ("chunk_id", "name", "kind", "tables", "keywords", "means", "applies_to", "sql", "note"),
            expected,
            _fields(written),
        )
        if wrote != read
    ]
    if mismatches:
        raise SnippetError(["the snippet does not survive a round trip", *mismatches])
    return len(before), len(after)


def _read_bytes(settings: ReviewSettings) -> bytes:
    try:
        return settings.snippets_document_path.read_bytes()
    except OSError as exc:
        raise SnippetError([f"cannot read the snippet document at {settings.snippets_document}: {exc}"]) from exc


def _text(data: bytes) -> str:
    """The document as `read_text` gives it: any line ending read as a newline."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _read(settings: ReviewSettings) -> str:
    return _text(_read_bytes(settings))


def _write(settings: ReviewSettings, text: str) -> str:
    try:
        return promotion_module._write_atomically(settings.snippets_document_path, text)
    except PromotionError as exc:
        raise SnippetError(exc.reasons) from exc


def reload(settings: ReviewSettings) -> list[StepResult]:
    """Load the store from the document, in this process (V6-27). The loader
    skips what is current; the reader's password is an argument, not an
    environment variable the loader's process inherits."""
    return [
        promotion_module.run_step(
            settings,
            STEP,
            lambda loaders: loaders.load_snippets(
                settings.snippets_document_path,
                settings.snippets_db_url,
                reader_role=settings.snippets_reader_user,
                reader_password=settings.snippets_reader_password,
                embedder=promotion_module.embedder_factory(settings),
                model=settings.embed_model,
            ),
            enabled=settings.reload_snippets,
        )
    ]


def preview(settings: ReviewSettings, draft: SnippetDraft, snippet_id: str | None = None) -> tuple[str, str, list[str]]:
    """The id and the section a draft would produce, without writing."""
    document = _read(settings)
    target = snippet_id or next_snippet_id(document)
    reasons = problems(draft, target)
    if snippet_id is not None and _block(document, snippet_id) is None:
        reasons.append(f"there is no snippet {snippet_id} in the document")
    if reasons:
        return target, "", reasons
    return target, render_snippet(draft, target), []


def add(settings: ReviewSettings, draft: SnippetDraft) -> Outcome:
    with promotion_module.writing(settings.snippets_document_path.parent):
        return _add(settings, draft)


def _add(settings: ReviewSettings, draft: SnippetDraft) -> Outcome:
    promotion_module._ensure_importable(settings.rag_dir)
    document = _read(settings)
    snippet_id = next_snippet_id(document)
    reasons = problems(draft, snippet_id)
    if reasons:
        raise SnippetError(reasons)
    updated = insert_snippet(document, draft, snippet_id)
    return _commit(settings, "added", snippet_id, document, updated, draft)


def change(settings: ReviewSettings, snippet_id: str, draft: SnippetDraft) -> Outcome:
    with promotion_module.writing(settings.snippets_document_path.parent):
        return _change(settings, snippet_id, draft)


def _change(settings: ReviewSettings, snippet_id: str, draft: SnippetDraft) -> Outcome:
    promotion_module._ensure_importable(settings.rag_dir)
    document = _read(settings)
    reasons = problems(draft, snippet_id)
    if reasons:
        raise SnippetError(reasons)
    updated = replace_snippet(document, snippet_id, draft)
    if updated is None:
        raise SnippetMissing([f"there is no snippet {snippet_id} in the document"])
    return _commit(settings, "changed", snippet_id, document, updated, draft)


def delete(settings: ReviewSettings, snippet_id: str) -> Outcome:
    with promotion_module.writing(settings.snippets_document_path.parent):
        return _delete(settings, snippet_id)


def _delete(settings: ReviewSettings, snippet_id: str) -> Outcome:
    promotion_module._ensure_importable(settings.rag_dir)
    document = _read(settings)
    updated = remove_snippet(document, snippet_id)
    if updated is None:
        raise SnippetMissing([f"there is no snippet {snippet_id} in the document"])
    return _commit(settings, "removed", snippet_id, document, updated, None)


def _commit(
    settings: ReviewSettings, action: str, snippet_id: str, document: str, updated: str, draft: SnippetDraft | None
) -> Outcome:
    before, after = _round_trip(document, updated, snippet_id=snippet_id, draft=draft)
    outcome = Outcome(
        action=action,
        snippet_id=snippet_id,
        chunk_id=chunk_id_for(snippet_id),
        document=str(settings.snippets_document_path),
        markdown=render_snippet(draft, snippet_id) if draft is not None else "",
        snippets_before=before,
        snippets_after=after,
    )
    outcome.backup = _write(settings, updated)
    outcome.steps = reload(settings)
    return outcome


def listing(settings: ReviewSettings) -> tuple[list, str, str]:
    """(snippets, the next id, the document's hash), as the document holds them."""
    promotion_module._ensure_importable(settings.rag_dir)
    sn = _parser()
    # Read once: parsed as text, and hashed as the bytes the loader records.
    data = _read_bytes(settings)
    document = _text(data)
    return _parse(sn, document, "the snippet document"), next_snippet_id(document), sn.document_hash(data)


def store_status(url: str, digest: str, *, connect: Any = None) -> dict[str, Any]:
    """What the store holds against the document: counts, and whether it is current."""
    import psycopg

    connect = connect or psycopg.connect
    try:
        with connect(url, connect_timeout=5) as conn:
            rows_present, vectors_present, source_present = conn.execute(
                "SELECT to_regclass('sql_snippets') IS NOT NULL, "
                "to_regclass('sql_snippet_vectors') IS NOT NULL, "
                "to_regclass('sql_snippet_source') IS NOT NULL"
            ).fetchone()
            snippets = conn.execute("SELECT count(*) FROM sql_snippets").fetchone()[0] if rows_present else 0
            embedded = conn.execute("SELECT count(*) FROM sql_snippet_vectors").fetchone()[0] if vectors_present else 0
            loaded = (
                conn.execute("SELECT document_hash FROM sql_snippet_source").fetchone() if source_present else None
            )
    except DATABASE_ERRORS as exc:  # reported, never raised at a browser
        return {"reachable": False, "detail": f"{type(exc).__name__}: {exc}"}
    current = loaded is not None and loaded[0] == digest
    if current:
        detail = f"{snippets} snippets, all embedded, loaded from this document"
    elif not rows_present:
        detail = "never loaded: the stack loads it on start, or a save here does"
    else:
        detail = f"{snippets} snippets, {embedded} embedded -- behind the document until the next load"
    return {"reachable": True, "snippets": int(snippets), "embedded": int(embedded), "current": current, "detail": detail}
