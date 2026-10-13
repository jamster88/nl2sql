"""The Judge: every answer the runs gave, judged before the vote (arch7.1 section 22.7).

Agreement between runs is evidence, but measured it was evidence for a
shared mistake as often as for the answer: on the paraphrase set every wrong
answer the ensemble delivered had a majority behind it, and B07's runs that
fell into its grain trap agreed with each other and outvoted the run that
did not. So before the vote counts anything, the Judge reads each distinct
answer -- a group's representative query and its first rows -- against the
question as asked and the knowledge the runs were given, and sets aside the
ones it can name a mistake in. The vote then counts only the runs whose
answer it accepted (`ensemble.py`).

Bounded as arch7 bounds it:

* **One structured call a question**, at the heavy rung.
* **It reads; it does not count.** The answers are shown by letter, in the
  order the runs were asked -- the original's first -- and never with how
  many runs gave each.
* **Nothing it writes is run.** It returns a verdict per letter. A letter
  that names no answer is ignored, a second verdict on one is ignored, and
  an answer it gave no verdict on is accepted: the Judge raised no
  objection to it.
"""

from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, Field

from . import paraphrase
from .ensemble_state import Candidate, Group, GroupVerdict
from .prompts import JUDGE_PROMPT, knowledge_block
from .state import AnswerContract, QueryResult

#: The answers' letters: one original and at most ten rewordings.
LETTERS = "ABCDEFGHIJK"

#: How many of an answer's rows the Judge is shown.
ROWS_SHOWN = 5

#: The most of the retrieved knowledge the Judge is shown, in characters.
KNOWLEDGE_CHARS = 6000

#: Why an answer the Judge said nothing about stands.
NO_VERDICT = "no verdict given"


class Ruling(BaseModel):
    answer: str = Field(description="the answer's letter: A, B, ...")
    accepted: bool = Field(description="true when its query answers the question as asked")
    why: str = Field(default="", description="one sentence: the mistake in its query, or why it is right")


class Rulings(BaseModel):
    """The Judge's structured output."""

    rulings: list[Ruling] = Field(default_factory=list)


def shown(groups: Sequence[Group]) -> list[Group]:
    """The groups in the order the Judge sees them: by their earliest run,
    so the original's answer is A -- never by size, which is the vote's."""
    return sorted(groups, key=lambda group: min(group.members))


def messages(
    question: str,
    contract: AnswerContract,
    groups: Sequence[Group],
    candidates: Sequence[Candidate],
    *,
    knowledge: str = "",
    assumptions: Sequence[str] = (),
) -> list[Any]:
    runs = {candidate.index: candidate for candidate in candidates}
    ordered = shown(groups)
    held = paraphrase.invariants(contract)
    assumed = "".join(f"- {line}\n" for line in assumptions)
    return JUDGE_PROMPT.format_messages(
        question=question,
        held=(
            f"what the answer is about: {held['entities']}; the figure: {held['measure']}; "
            f"the period: {held['period']}; the rows asked for: {held['limit']}."
        ),
        assumptions=f"Every answer was told to assume:\n{assumed}" if assumed else "",
        knowledge=knowledge_block(knowledge[:KNOWLEDGE_CHARS]),
        answers="\n\n".join(
            answer_block(LETTERS[i], runs[group.representative]) for i, group in enumerate(ordered)
        ),
        letters=", ".join(LETTERS[: len(ordered)]),
    )


def answer_block(letter: str, candidate: Candidate) -> str:
    """One answer as the Judge reads it: its query, its columns and its
    first rows -- the representative's, which every member of its group
    agrees with."""
    result = candidate.state.get("result") or QueryResult()
    rows = result.rows[:ROWS_SHOWN]
    table = "\n".join(" | ".join(_cell(value) for value in row) for row in rows) or "(no rows)"
    more = f", first {len(rows)} shown" if len(result.rows) > len(rows) else ""
    return (
        f"Answer {letter}\n"
        f"SQL:\n{(candidate.state.get('sql') or '').strip()}\n"
        f"Rows ({len(result.rows)}{more}):\n"
        f"{' | '.join(result.columns)}\n{table}"
    )


def judge(
    llm: Any,
    question: str,
    contract: AnswerContract,
    groups: Sequence[Group],
    candidates: Sequence[Candidate],
    *,
    knowledge: str = "",
    assumptions: Sequence[str] = (),
) -> list[GroupVerdict]:
    """A verdict on every group's answer, in group order.

    A model that cannot be reached, or answers with something that is not a
    list of rulings, raises; the caller records it and lets the runs vote
    alone.
    """
    ordered = shown(groups)
    answer = llm.with_structured_output(Rulings).invoke(
        messages(question, contract, groups, candidates, knowledge=knowledge, assumptions=assumptions)
    )
    by_letter = {LETTERS[i]: group.index for i, group in enumerate(ordered)}
    verdicts: dict[int, GroupVerdict] = {}
    for ruling in list(getattr(answer, "rulings", None) or []):
        letter = _letter(getattr(ruling, "answer", ""))
        index = by_letter.get(letter)
        if index is None or index in verdicts:
            continue
        why = " ".join((getattr(ruling, "why", "") or "").split())
        verdicts[index] = GroupVerdict(group=index, accepted=bool(ruling.accepted), why=why)
    return [
        verdicts.get(group.index, GroupVerdict(group=group.index, accepted=True, why=NO_VERDICT))
        for group in sorted(groups, key=lambda group: group.index)
    ]


def _letter(text: str) -> str:
    """"B", "b", "Answer B" and "B." all name answer B."""
    words = (text or "").replace(".", " ").replace(":", " ").split()
    return words[-1].upper() if words else ""


def _cell(value: Any) -> str:
    return "NULL" if value is None else str(value)
