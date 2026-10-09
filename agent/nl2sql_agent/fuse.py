"""What the delivered answer says about the runs behind it (arch7 section 22.8).

Selection -- which run stands for the winning group -- is `agreement.rank`.
What is built here so far is the sentence every answer the ensemble chooses
carries first: how many runs there were and how they agreed, so a reader
knows how much the answer was tested before they read it. The rest of
fusion -- columns other agreeing runs carried, joined on the entity's key;
their claims, checked against the delivered rows and re-audited; the
outvoted groups, named by their key fact -- arrives with the second wave,
and until it does the delivered answer is the representative's own.

The count in every sentence is the runs made, not the rewordings written:
a rewording the fidelity gate discarded was never asked.
"""

from __future__ import annotations

from .ensemble_state import Agreement, Judgement


def agreement_line(agreement: Agreement, judgement: Judgement | None = None) -> str:
    """The agreement, as the sentence the answer opens with; empty when no
    run could be delivered."""
    total, agreed = agreement.total, agreement.agreed
    failed = total - agreement.admissible
    could_not = f"; {failed} could not answer" if failed else ""
    level = agreement.level
    if level == "unanimous":
        return f"Agreed by {agreed} of {total} independent runs of the question, each worded differently{could_not}."
    if level == "majority":
        return f"{agreed} of {total} runs agreed; {agreement.admissible - agreed} answered differently{could_not}."
    if level == "judged" and judgement is not None:
        return f"The runs disagreed; the Judge chose this answer: {judgement.why.rstrip('.')}."
    if level == "contested":
        largest = f"the largest group's ({agreed} of {total} runs)"
        if judgement is not None:
            return f"The runs disagreed and the Judge could not say which was right; this is {largest}."
        return f"The runs disagreed and no answer had a majority; this is {largest}."
    if level == "single":
        return "Asked 1 way; one run answered." if total == 1 else f"Asked {total} ways; only one run answered."
    return ""
