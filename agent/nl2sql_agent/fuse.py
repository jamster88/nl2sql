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

from typing import Sequence

from .ensemble_state import Agreement, Group, Judgement


def agreement_line(agreement: Agreement, judgement: Judgement | None = None, groups: Sequence[Group] = ()) -> str:
    """The agreement, as the sentence the answer opens with; empty when no
    run could be delivered.

    The counts are the vote's -- the runs whose answer the Judge accepted --
    and the sentence says what the Judge did besides: the answers it set
    aside, the one the runs preferred when it overruled them, its objection
    when it accepted none, and that it could not be asked when it could not
    (arch7.1 section 22.7). `groups` are the vote's groups, for the size of
    the answer it overruled.
    """
    total, agreed = agreement.total, agreement.agreed
    aside = agreement.set_aside
    failed = total - agreement.admissible - aside
    could_not = f"; {failed} could not answer" if failed else ""
    others = f"; the Judge set aside the answer of {aside} other{'s' if aside > 1 else ''}" if aside else ""
    unasked = " The Judge could not be asked." if judgement is not None and judgement.error else ""
    level = agreement.level
    if level == "judged" and judgement is not None:
        overruled = next((g for g in groups if judgement.instead_of in g.members), None)
        size = len(overruled.members) if overruled else aside
        return (
            f"The Judge set aside the answer {size} of {total} runs gave -- {_why(judgement, overruled)} -- "
            f"and accepted this one, which {agreed} gave."
        )
    if level == "unanimous":
        return (
            f"Agreed by {agreed} of {total} independent runs of the question, each worded differently"
            f"{could_not}{others}.{unasked}"
        )
    if level == "majority":
        return f"{agreed} of {total} runs agreed; {agreement.admissible - agreed} answered differently{could_not}{others}.{unasked}"
    if level == "contested":
        if judgement is not None and judgement.verdicts and not any(v.accepted for v in judgement.verdicts):
            chosen = groups[0] if groups else None
            return (
                f"The Judge accepted none of the answers -- {_why(judgement, chosen)} -- "
                f"so this is the runs' own choice, which {agreed} of {total} gave."
            )
        largest = f"the largest group's ({agreed} of {total} runs)"
        return f"The runs disagreed and no answer had a majority; this is {largest}{others}.{unasked}"
    if level == "single":
        if total == 1:
            return f"Asked 1 way; one run answered.{unasked}"
        if aside:
            return f"Asked {total} ways; one run's answer stands{others}{could_not}.{unasked}"
        return f"Asked {total} ways; only one run answered.{unasked}"
    return ""


def _why(judgement: Judgement, group: Group | None) -> str:
    """The Judge's reason about a group's answer, as a clause."""
    verdict = next((v for v in judgement.verdicts if group is not None and v.group == group.index), None)
    return (verdict.why if verdict and verdict.why else "it gave no reason").rstrip(".")
