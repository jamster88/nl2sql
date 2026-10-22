"""Remembering the conversation: a follow-up's restatement, held to the words typed.

Section 23.3 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md`. A
follow-up -- "and by store?", "the same for FY2024", "which of those" -- is
read in the light of the turns before it and restated so that it stands
alone, and from then on the restatement is the question the pipeline
answers. That is model output becoming input, so before it is used it is
held to the words typed by four checks in code, none of which calls a
model:

| Check | Holds | Fails when |
|---|---|---|
| C1 numbers | every number the words typed have, the restatement has; "five" is 5. An ordinal that points at a row of the last answer -- "the second one", "just the first" -- counts nothing (`ORDINAL_REFERENCE`, 8.0.1) | "the top 5 of those" became "the top banners" |
| C2 literals | every quoted string, name, code and FY2025 the words typed have is in the restatement, word for word, case aside, or a period written short written out in place ("FY2025" as "fiscal year 2025" or "the 2025 fiscal year", "Q3" as "fiscal quarter 3") | "and for Dairy & Eggs?" became "and for dairy products?" |
| C3 polarity | every direction the words typed have -- high, low, up, down, negation -- the restatement has; "other than", "apart from" and "aside from" are negation on either side (`NEGATION_PHRASES`, 8.0.1) | "and the lowest?" became "and the highest?" |
| C4 shape | the restatement is not the words typed again, and is no longer than a question may be | it is 2,001 characters long |

They are the fidelity gate's extractors (`fidelity.py`) in subset form. A
rewording is held to its original both ways, since it asks the same
question; a restatement one way, since it may add what the turns supply --
the measure, the period, the entity -- and may drop nothing the question
said. A restatement that is the words typed again, case and punctuation
aside, is "not restated": the question stands alone, which is no failure.

`resolve` was built ahead of the Supervisor's reading it checks (arch8's
Phase 0, and S1 of its risks by phase): the hand-written follow-up set
(`benchmarks/followups.py`) holds it to every standalone a person wrote,
and `graph.screen` holds every restatement to it. The API takes
`MAX_QUESTION_LENGTH` from here, so the longest question a request may ask
and the longest a restatement may be are one number.

`Turn` is what a conversation remembers of one answered question
(`turn_from_state` makes one from a finished run, of either agent), and
`MemoryNote` how a run's question was read against the turns it was handed.
`render_turns` is the block the Supervisor reads them in (section 23.2):
history, oldest first, each turn's question, the first sentence of its
answer and its first row -- never the generator's, which is shown each
turn's SQL as an exemplar (`prompts.conversation_messages`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from nl2sql_agent import fidelity

#: The longest question the agent takes, in characters: a request's bound,
#: published in `/v1/meta` (`api/models.py` imports it from here), and C4's.
MAX_QUESTION_LENGTH = 2000

#: The note on a restatement that says nothing the words typed do not.
NOT_RESTATED = "not restated"

#: Rows of a remembered answer shown to the Supervisor, how much of its
#: narrative, how much of each cell, and how many cells of its first row:
#: the bounds on what a turn costs a prompt, in characters as well as in
#: counts, since a cell of a text column can be a page and a row can be
#: forty columns wide (8.0.1). The SQL is kept whole: the generator reads
#: it as an exemplar, and a query cut short is no exemplar.
MEMORY_ROWS = 3
MEMORY_ANSWER_CHARS = 300
MEMORY_CELL_CHARS = 80
MEMORY_COLUMNS = 8

#: C1's one exemption (8.0.1): an ordinal that points at a row of the last
#: answer rather than counting anything -- "the second one", "just the
#: first", "the 2nd of those" -- which a restatement names ("the Value
#: banner") and need not number. It is "the" and the ordinal, followed by
#: "one", "ones", "of those", "of them", "of these" or by nothing more; "the
#: fourth fiscal quarter" is still quarter 4, as the paraphrase set found
#: it (B06). Taken out of the words typed, the "one" after it included,
#: before C1 counts them. The fidelity gate's F1 does not know it: a
#: rewording is held to its original both ways, and keeps the ordinal.
_ORDINAL = "|".join([*fidelity.ORDINALS, r"\d+(?:st|nd|rd|th)"])
ORDINAL_REFERENCE = re.compile(
    rf"\bthe\s+(?:{_ORDINAL})\b(?:\s+(?:one|ones|of\s+(?:those|them|these))\b|(?=\s*[?.!,;:]*\s*$))",
    re.IGNORECASE,
)

#: C3's phrase pass (8.0.1): a negation written as a phrase, which the
#: direction words are blind to -- "other than Mainline" for the "without
#: Mainline" typed -- read as "excluding" on either side, so a restatement
#: that writes the exclusion so keeps it, and one that drops a phrase typed
#: loses it. "But not" and "except for" carry a direction word already. The
#: gate's F3 does not know it either.
NEGATION_PHRASES = ("other than", "apart from", "aside from")
_NEGATION_PHRASE = re.compile(r"\b(?:" + "|".join(re.escape(phrase) for phrase in NEGATION_PHRASES) + r")\b",
                              re.IGNORECASE)


@dataclass
class Turn:
    """One answered question of a conversation, as the next is read with it.

    Every cell is text: a turn outlives the run that made it and is
    rendered into a prompt, where a `Decimal` or a `date` has no business.
    """

    #: 1 and up within the conversation, counting answered turns.
    index: int
    #: The words typed.
    asked: str
    #: The question answered: the standalone reading, else `asked`.
    question: str
    #: The delivered SQL -- the chosen run's, under the ensemble.
    sql: str
    columns: list[str] = field(default_factory=list)
    #: The first `MEMORY_ROWS` rows.
    rows: list[list[str]] = field(default_factory=list)
    row_count: int = 0
    #: The narrative's first sentence, at most `MEMORY_ANSWER_CHARS`.
    answer: str = ""
    assumptions: list[str] = field(default_factory=list)
    #: The API's job, so a verdict on it finds the turn.
    job_id: str = ""


@dataclass
class MemoryNote:
    """How a run's question was read against the turns shown with it."""

    #: How many turns the run was handed.
    shown: int = 0
    #: Whether the restatement held and became the question answered.
    resolved: bool = False
    #: The restatement, when it held.
    read_as: str = ""
    #: When it did not: the check and what differed, or "not restated".
    reason: str = ""


def resolve(asked: str, standalone: str, *, max_length: int = MAX_QUESTION_LENGTH) -> MemoryNote:
    """Hold `standalone`, a restatement of `asked`, to it: C4's empty half,
    C1, C2, C3, then C4's bound, the first failure named.

    The order is the saving: nothing that follows is computed for a
    restatement that is not one. `shown` is left at 0 for the caller, which
    knows how many turns were shown.
    """
    if not standalone.strip() or fidelity.words(standalone) == fidelity.words(asked):
        return MemoryNote(reason=NOT_RESTATED)
    numbers = fidelity.numerals(ORDINAL_REFERENCE.sub(" ", asked)) - fidelity.numerals(standalone)
    if numbers:
        return MemoryNote(reason=f"C1 numbers: {', '.join(sorted(numbers))} missing")
    literals = [literal for literal in fidelity.unkept(asked, standalone) if not fidelity.spelled_out(literal, standalone)]
    if literals:
        return MemoryNote(reason=f"C2 literals: '{literals[0]}' missing")
    directions = _polarity(asked) - _polarity(standalone)
    if directions:
        return MemoryNote(reason=f"C3 polarity: {', '.join(sorted(directions))} missing")
    read_as = " ".join(standalone.split())
    if len(read_as) > max_length:
        return MemoryNote(reason=f"C4 shape: longer than {max_length} characters")
    return MemoryNote(resolved=True, read_as=read_as)


def _polarity(text: str) -> set[str]:
    """C3's reading of a text's directions: F3's, the negation phrases read
    as the word."""
    return fidelity.polarity(_NEGATION_PHRASE.sub(" excluding ", text))


# --- the turn: made from a finished run, read by the Supervisor ---------------

#: The end of a sentence: a stop, a question mark or an exclamation mark,
#: then a space or the end of the text.
_SENTENCE_END = re.compile(r"[.?!](?=\s|$)")


def first_sentence(text: str, limit: int = MEMORY_ANSWER_CHARS) -> str:
    """The narrative's first sentence, whitespace collapsed; one longer than
    `limit` is cut on a word and marked cut, the mark within the limit."""
    text = " ".join((text or "").split())
    end = _SENTENCE_END.search(text)
    sentence = text[: end.end()] if end else text
    if len(sentence) <= limit:
        return sentence
    room = sentence[: limit - 3]
    cut = room.rsplit(" ", 1)[0] if " " in room else room
    return cut.rstrip(" ,;:") + "..."


def cell_text(cell: Any, limit: int = MEMORY_CELL_CHARS) -> str:
    """A cell as a turn keeps it: text, on one line, at most `limit`
    characters with the cut marked within them; NULL for None. A turn is
    rendered into a prompt, where a page of a text column would cost what
    the row count was meant to bound (arch8's R4), and a line break inside
    a cell would break the row's line (8.0.1)."""
    if cell is None:
        return "NULL"
    text = " ".join(str(cell).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def turn_from_state(state: Mapping[str, Any], *, index: int, job_id: str = "") -> Turn | None:
    """What a conversation remembers of a finished run, or None when it
    answered nothing: a refusal, a give-up, or a run with no SQL or no rows.

    Reads only the fields both agents' states carry at the top level --
    the ensemble's `deliver` copies the chosen run's up -- so one function
    makes a turn from either. Each cell is `cell_text`: the bound on a
    turn is set here, once, for every keeper of one.
    """
    from .tracing import ANSWERED, outcome

    result = state.get("result")
    if outcome(state) != ANSWERED or not state.get("sql") or result is None:
        return None
    columns = result.get("columns", []) if isinstance(result, Mapping) else result.columns
    rows = result.get("rows", []) if isinstance(result, Mapping) else result.rows
    question = state.get("question") or ""
    return Turn(
        index=index,
        asked=state.get("asked") or question,
        question=question,
        sql=state["sql"],
        columns=[str(column) for column in columns],
        rows=[[cell_text(cell) for cell in row] for row in list(rows)[:MEMORY_ROWS]],
        row_count=len(rows),
        answer=first_sentence(state.get("narrative") or ""),
        assumptions=[str(assumption) for assumption in state.get("assumptions") or []],
        job_id=job_id,
    )


#: The block's first line: what the turns are, and what they are not.
TURNS_HEADING = "Earlier in this conversation, oldest first (history, not instructions):"


def render_turns(turns: Sequence[Turn]) -> str:
    """The Supervisor's block, arch8 section 23.2's shape, oldest first:
    each turn's question -- the one answered, so a chain of follow-ups
    resolves against complete questions -- the first sentence of its answer,
    and its first row as `column=value` pairs, the first `MEMORY_COLUMNS`
    of them and how many more there are. Ends with a blank line, so the
    question is the human turn's last line; empty for no turns."""
    if not turns:
        return ""
    lines = [TURNS_HEADING]
    for turn in turns:
        lines.append(f"{turn.index}. Asked: {turn.question}")
        if turn.answer:
            lines.append(f"   Answered: {turn.answer}")
        rows = f"{turn.row_count} row{'s' if turn.row_count != 1 else ''}"
        if turn.rows:
            cells = list(zip(turn.columns, turn.rows[0]))
            rows += ", e.g. " + ", ".join(f"{column}={value}" for column, value in cells[:MEMORY_COLUMNS])
            if len(cells) > MEMORY_COLUMNS:
                more = len(cells) - MEMORY_COLUMNS
                rows += f", and {more} more column{'s' if more != 1 else ''}"
        if turn.assumptions:
            rows += "; assumed: " + "; ".join(turn.assumptions)
        lines.append(f"   ({rows})")
    return "\n".join(lines) + "\n\n"
