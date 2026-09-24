"""Turning an approved submission into a golden pair, as markdown.

`context_questions/translated_questions.md` is the single source of truth for
the golden set: `05_load_golden_pairs.py` parses it into the context store and
`06_embed_golden_pairs.py` embeds what that produced. Step 5 also *deletes*
rows whose pair is no longer in the document, which is the fact that decides
this module's existence -- a pair written straight into the database and not
into the document is erased the next time anyone reloads. So promotion writes
the document, and the database is downstream of it, exactly as it already was.

That makes the format a contract rather than a preference, and the contract is
`ragproc.golden_pairs.ENTRY_RE`. It is a single regular expression over the
whole file, and it fails in the worst possible way: a pair that does not match
it is not reported as malformed, it is simply *not seen*, and the only reason
the loader notices at all is a count of `## Q..` headings that disagrees with
the number parsed. Everything below exists to make that mismatch impossible.

Three constraints in that expression are easy to miss and are checked here:

* **`Q\\d{2}` is exactly two digits**, so the golden set tops out at Q99. At 45
  pairs that is a long way off, but it is a wall rather than a slope: Q100
  would parse as nothing at all.
* **The question is delimited by a double quote followed by a newline.** A
  question ending in one closes the field early.
* **The SQL is fenced, and the fence is not escapable.** SQL containing a
  triple backtick ends the block wherever it appears.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The fields the loader requires in the meta block, in the order the existing
#: document writes them. Order is cosmetic to the parser and not to a reader
#: diffing the file, which is the only reason it is fixed.
META_KEYS = ("chunk_id", "type", "tables", "keywords")

#: What `type:` says for a pair that came from the golden set rather than from
#: the surrounding prose. The loader stores it; the retriever does not read it.
PAIR_TYPE = "golden pair"

#: The ceiling `Q\d{2}` imposes. Named so the error message can cite it.
MAX_PAIR_NUMBER = 99

PAIR_ID_RE = re.compile(r"^Q(\d{2})$")
SUITE_RE = re.compile(r"^# (Suite .+?)\s*$", re.M)
HEADING_RE = re.compile(r"^## Q(\d{2}) - ", re.M)

#: Fields a draft must carry to become a pair, and what to call them when one
#: is missing. Everything the loader requires is here; nothing optional is.
REQUIRED = {
    "title": "a short title for the `## Qnn -` heading",
    "question": "the natural-language question",
    "tables": "the tables the SQL reads, comma-separated",
    "keywords": "keywords for the BM25 index, comma-separated",
    "reasoning_target": "what this pair tests, and where generated SQL goes wrong",
    "sql_code": "the verified SQL",
    "result": "the shape of what comes back",
}


@dataclass
class Draft:
    """The editable golden pair a curator builds from a submission.

    Seeded from the submission -- the question and SQL are already there, and
    the tables usually are -- but the three fields that make a pair *useful*
    (`keywords`, `reasoning_target`, `result`) cannot be derived from a thumbs
    up and have to be written. That is the work the review GUI exists for.
    """

    title: str = ""
    question: str = ""
    tables: str = ""
    keywords: str = ""
    reasoning_target: str = ""
    sql_code: str = ""
    result: str = ""
    translation_note: str = ""
    suite: str = ""
    extra_meta: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Draft":
        """Build from whatever JSON the staging row is holding.

        Unknown keys are dropped rather than raising: the draft column is
        JSONB written by a browser, and a client that sends one field too
        many should not take the promotion endpoint down.
        """
        known = {f: data.get(f, "") for f in cls.__dataclass_fields__ if f != "extra_meta"}
        text = {k: v if isinstance(v, str) else "" for k, v in known.items()}
        extra = data.get("extra_meta")
        return cls(**text, extra_meta=extra if isinstance(extra, dict) else {})

    def as_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


def problems(draft: Draft, pair_id: str = "Q46") -> list[str]:
    """Everything that would stop this draft from parsing, in one pass.

    All of them, not the first: a curator filling in a form wants the list,
    not a game where fixing one error reveals the next.
    """
    found: list[str] = []
    for name, description in REQUIRED.items():
        if not getattr(draft, name).strip():
            found.append(f"{name} is empty -- needs {description}")

    match = PAIR_ID_RE.match(pair_id)
    if not match:
        found.append(
            f"{pair_id!r} is not a two-digit pair id; the loader's pattern is Q\\d{{2}}, "
            f"so the golden set cannot go past Q{MAX_PAIR_NUMBER}"
        )

    # The heading runs to end of line, so a title carrying one splits the pair.
    if "\n" in draft.title:
        found.append("title spans more than one line; the `## Qnn -` heading is one line")

    # `**Question:** "(.*?)"\n` -- the field ends at the first quote that a
    # newline follows, so a question ending in one truncates itself.
    if draft.question.rstrip().endswith('"'):
        found.append(
            'question ends with a double quote, which closes the field early; '
            "reword it or move the quotation inside the sentence"
        )
    if "\n" in draft.question.strip():
        found.append("question spans more than one line; the loader reads it as one")

    for name in ("sql_code", "reasoning_target", "result", "translation_note"):
        if "```" in getattr(draft, name):
            found.append(f"{name} contains a ``` fence, which ends the block early")

    if "\n" in draft.reasoning_target.strip():
        found.append("reasoning_target spans more than one line; the loader reads it as one")
    if "\n" in draft.result.strip():
        found.append("result spans more than one line; the loader reads it as one")

    for name in ("tables", "keywords"):
        value = getattr(draft, name)
        if value.strip() and not [part for part in value.split(",") if part.strip()]:
            found.append(f"{name} has no entries; it is a comma-separated list")

    for key in draft.extra_meta:
        if not re.fullmatch(r"\w[\w.-]*", key):
            found.append(f"meta key {key!r} is not one the meta parser will read")

    return found


def next_pair_id(document: str) -> str:
    """The id after the highest one the document already uses.

    Highest rather than count: a pair removed by hand leaves a gap, and
    reusing its number would give two different questions the same
    `chunk_id` across the lifetime of the context store -- where the id is
    the primary key, so the newer would silently overwrite the older.
    """
    numbers = [int(m.group(1)) for m in HEADING_RE.finditer(document)]
    nxt = (max(numbers) + 1) if numbers else 1
    if nxt > MAX_PAIR_NUMBER:
        raise ValueError(
            f"the golden set is full: Q{MAX_PAIR_NUMBER} is the last id the loader's "
            f"Q\\d{{2}} pattern matches. Widening it means changing ENTRY_RE and "
            "HEADING checks in ragproc/golden_pairs.py first."
        )
    return f"Q{nxt:02d}"


def chunk_id_for(pair_id: str) -> str:
    """`Q46` -> `eval:q46`, the convention every existing pair follows."""
    return f"eval:{pair_id.lower()}"


def suite_in_force(document: str) -> str:
    """The `# Suite ...` heading a pair appended to the end would inherit.

    The loader decides a pair's suite by the last such heading *before* it,
    so this is what a new pair gets for free -- and what it has to be given
    a heading to escape.
    """
    headings = SUITE_RE.findall(document)
    return headings[-1].strip() if headings else ""


def render_pair(draft: Draft, pair_id: str) -> str:
    """One pair, as the block `ENTRY_RE` matches.

    Every separator here is load-bearing. The blank line between the heading
    and the meta fence, between each labelled field, and the newline the
    final field ends on are all in the pattern, so this is not a layout
    choice that can be tidied later.
    """
    meta = {
        "chunk_id": chunk_id_for(pair_id),
        "type": PAIR_TYPE,
        "tables": _csv(draft.tables),
        "keywords": _csv(draft.keywords),
        **draft.extra_meta,
    }
    meta_lines = "\n".join(f"{key}: {meta[key]}" for key in _meta_order(meta))
    note = draft.translation_note.strip() or (
        "Promoted from web GUI feedback; the SQL is the answer the agent produced "
        "and a reviewer confirmed."
    )
    return (
        f"## {pair_id} - {draft.title.strip()}\n"
        "\n"
        f"```meta\n{meta_lines}\n```\n"
        "\n"
        f'**Question:** "{draft.question.strip()}"\n'
        "\n"
        f"**Reasoning target:** {draft.reasoning_target.strip()}\n"
        "\n"
        f"```sql\n{draft.sql_code.strip()}\n```\n"
        "\n"
        f"**Result:** {draft.result.strip()}\n"
        "\n"
        f"**Translation note:** {note}\n"
    )


def _meta_order(meta: dict[str, str]) -> list[str]:
    extra = [key for key in meta if key not in META_KEYS]
    return [*META_KEYS, *extra]


def _csv(value: str) -> str:
    """Normalise a comma-separated field to `a, b, c`.

    `table_list` and `keyword_list` split on commas and strip, so the spacing
    never reaches the database -- but it does reach a reader of the diff.
    """
    return ", ".join(part.strip() for part in value.split(",") if part.strip())


def append_pair(document: str, draft: Draft, pair_id: str) -> str:
    """The whole document with the new pair on the end.

    Appended rather than inserted into its suite. Inserting would renumber
    nothing (the loader assigns `ordinal` by document order at load time) but
    it would move existing text in the diff, and a curation tool whose commits
    are hard to read is one people stop trusting. The suite heading is written
    only when the pair is not already inheriting the one it wants.
    """
    body = document if document.endswith("\n") else document + "\n"
    suite = draft.suite.strip()
    heading = ""
    if suite and suite != suite_in_force(body):
        name = suite if suite.lower().startswith("suite") else f"Suite - {suite}"
        heading = f"# {name}\n\n"
    return f"{body}\n{heading}{render_pair(draft, pair_id)}"
