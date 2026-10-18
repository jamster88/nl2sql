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
"""

from __future__ import annotations

from dataclasses import dataclass

from nl2sql_agent import fidelity

#: The longest question the agent takes, in characters: a request's bound,
#: published in `/v1/meta` (`api/models.py` imports it from here), and C4's.
MAX_QUESTION_LENGTH = 2000

#: The note on a restatement that says nothing the words typed do not.
NOT_RESTATED = "not restated"


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
