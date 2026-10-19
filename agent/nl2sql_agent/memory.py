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
| C1 numbers | every number the words typed have, the restatement has; "five" is 5 | "the top 5 of those" became "the top banners" |
| C2 literals | every quoted string, name, code and FY2025 the words typed have is in the restatement, word for word, case aside | "and for Dairy & Eggs?" became "and for dairy products?" |
| C3 polarity | every direction the words typed have -- high, low, up, down, negation -- the restatement has | "and the lowest?" became "and the highest?" |
| C4 shape | the restatement is not the words typed again, and is no longer than a question may be | it is 2,001 characters long |

They are the fidelity gate's extractors (`fidelity.py`) in subset form. A
rewording is held to its original both ways, since it asks the same
question; a restatement one way, since it may add what the turns supply --
the measure, the period, the entity -- and may drop nothing the question
said. A restatement that is the words typed again, case and punctuation
aside, is "not restated": the question stands alone, which is no failure.

`resolve` is built ahead of the Supervisor's reading it will check (arch8's
Phase 0, and S1 of its risks by phase): the hand-written follow-up set
(`benchmarks/followups.py`) holds it to every standalone a person wrote,
and nothing in the pipeline calls it yet. The API takes
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

#: Rows of a remembered answer shown to the Supervisor, and how much of its
#: narrative: the bounds on what a turn costs a prompt.
MEMORY_ROWS = 3
MEMORY_ANSWER_CHARS = 300


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
    numbers = fidelity.numerals(asked) - fidelity.numerals(standalone)
    if numbers:
        return MemoryNote(reason=f"C1 numbers: {', '.join(sorted(numbers))} missing")
    literals = fidelity.unkept(asked, standalone)
    if literals:
        return MemoryNote(reason=f"C2 literals: '{literals[0]}' missing")
    directions = fidelity.polarity(asked) - fidelity.polarity(standalone)
    if directions:
        return MemoryNote(reason=f"C3 polarity: {', '.join(sorted(directions))} missing")
    read_as = " ".join(standalone.split())
    if len(read_as) > max_length:
        return MemoryNote(reason=f"C4 shape: longer than {max_length} characters")
    return MemoryNote(resolved=True, read_as=read_as)


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


def turn_from_state(state: Mapping[str, Any], *, index: int, job_id: str = "") -> Turn | None:
    """What a conversation remembers of a finished run, or None when it
    answered nothing: a refusal, a give-up, or a run with no SQL or no rows.

    Reads only the fields both agents' states carry at the top level --
    the ensemble's `deliver` copies the chosen run's up -- so one function
    makes a turn from either.
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
        rows=[["NULL" if cell is None else str(cell) for cell in row] for row in list(rows)[:MEMORY_ROWS]],
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
    and its first row as `column=value` pairs. Ends with a blank line, so
    the question is the human turn's last line; empty for no turns."""
    if not turns:
        return ""
    lines = [TURNS_HEADING]
    for turn in turns:
        lines.append(f"{turn.index}. Asked: {turn.question}")
        if turn.answer:
            lines.append(f"   Answered: {turn.answer}")
        rows = f"{turn.row_count} row{'s' if turn.row_count != 1 else ''}"
        if turn.rows:
            rows += ", e.g. " + ", ".join(f"{column}={value}" for column, value in zip(turn.columns, turn.rows[0]))
        if turn.assumptions:
            rows += "; assumed: " + "; ".join(turn.assumptions)
        lines.append(f"   ({rows})")
    return "\n".join(lines) + "\n\n"
