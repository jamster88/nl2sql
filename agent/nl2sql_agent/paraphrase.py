"""The Paraphraser: the question, reworded without changing what it asks (arch7 section 22.3).

Temperature is zero everywhere, so the only independent second draw the
pipeline has is a different wording of the same question. One structured
call writes them -- up to `ENSEMBLE_MAX_PARAPHRASES`, most different first,
each with a few words on what it varied -- from the question and what its
answer may not change, and nothing else: no schema, no retrieved text, no
rows. Its one untrusted input, the question, was screened before it got
here; what it writes is screened again, every rewording, before any is run
(the fidelity gate in `ensemble.py`).

The rewordings are kept in the order the model wrote them, because that is
the order the waves spend them in. A rewording the gate discards can be
replaced once: the retry is told which ones failed and why.
"""

from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, Field

from . import contract as answer_contract
from .ensemble_state import Paraphrase
from .prompts import PARAPHRASE_PROMPT, paraphrase_retry_block
from .state import AnswerContract


class Rewording(BaseModel):
    text: str = Field(description="the question, reworded: the same question in other words")
    changed: str = Field(
        default="", description="a few words on what was varied: form, verb, order, register, synonym"
    )


class Rewordings(BaseModel):
    """The Paraphraser's structured output."""

    rewordings: list[Rewording] = Field(default_factory=list)


def invariants(contract: AnswerContract) -> dict[str, str]:
    """What the Paraphraser may not change, in the contract's words.

    The measure is named only as the generator is told it: the default the
    contract supplies, or "the figure the question asks for". The
    Supervisor's own words for a measure are for the trace -- read into a
    prompt they are a paraphrase of their own, and B13's "prices lowest
    relative to us", given to the Paraphraser as "average price difference",
    came back as rewordings asking for a difference (`stated_measure`).
    """
    words = [entity.word for entity in contract.entities if entity.word]
    return {
        "entities": ", ".join(words) or "the single figure it asks for",
        "measure": answer_contract.stated_measure(contract) or "the figure the question asks for, in its own words",
        "period": contract.period or "none named",
        "limit": str(contract.limit) if contract.limit else "none asked for",
    }


def messages(
    question: str, contract: AnswerContract, *, count: int, failed: Sequence[tuple[str, str]] = ()
) -> list[Any]:
    return PARAPHRASE_PROMPT.format_messages(
        **invariants(contract),
        question=question,
        count=count,
        failed=paraphrase_retry_block(list(failed)),
    )


def paraphrase(
    llm: Any,
    question: str,
    contract: AnswerContract,
    *,
    count: int,
    failed: Sequence[tuple[str, str]] = (),
    start: int = 1,
    written: Sequence[str] = (),
) -> list[Paraphrase]:
    """Up to `count` rewordings, numbered from `start` in the order written.

    Each is normalised -- its spacing collapsed, a blank one dropped -- and
    none is kept twice, nor one already `written`: a retry that hands back a
    rewording the gate has already judged would be judged again for nothing.
    A model that answers with nothing is an empty list; a model that cannot
    be reached raises, and the caller records it.
    """
    answer = llm.with_structured_output(Rewordings).invoke(
        messages(question, contract, count=count, failed=failed)
    )
    seen = {text.casefold() for text in written}
    out: list[Paraphrase] = []
    for item in list(getattr(answer, "rewordings", None) or []):
        text = " ".join((getattr(item, "text", "") or "").split())
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        changed = " ".join((getattr(item, "changed", "") or "").split())
        out.append(Paraphrase(index=start + len(out), text=text, changed=changed))
        if len(out) == count:
            break
    return out
