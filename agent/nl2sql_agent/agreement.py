"""Which runs are answers, which agree, and which one stands for them (arch7 sections 22.5, 22.6, 22.8).

The ensemble asks one question several ways and gets several runs back. This
module decides, in code and with no model call, what they amount to:

* **Is each one an answer to its own question?** Five rules, E1 to E5, read
  off the run's state and the anchor contract -- the contract read from the
  original, which every wording was held to. A run that fails one does not
  vote; it is still in the record, with its reason.
* **Do they agree?** Two results agree when either matches the other under
  the benchmark's own scorer (`compare.result_matches`): column names and
  order are presentation, extra columns are allowed, rows are compared in
  order only when the contract ranks them. The runs are grouped as the
  connected components of agreement, by union-find, so a tolerance that
  lets a match b and b match c puts the three together.
* **Who won?** The vote: a strict majority of the admissible runs, never of
  all of them, and two is the smallest group that agrees. When no group has
  one, the level is `open` -- a marker for the graph to route on, never
  delivered: the Judge, or the plurality as `contested`, settles it.
* **Which run stands for the group?** The representative, by a ranking of
  what makes one result the stronger of several that agree: complete over a
  gap, audited over dropped claims, the original's wording over a
  rewording's, fewer attempts, the cheaper plan, the lower index.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .compare import result_matches
from .completeness import NO_ROWS, check_rules, measure_columns, read_query
from .contract import LabelMap
from .ensemble_state import ORIGINAL, Agreement, Candidate, Group
from .state import AnswerContract, AuditReport, CompletenessReport, QueryResult
from .tracing import ANSWERED

#: The levels a vote can end at, before any Judge. `open` is the router's:
#: no group holds a majority and something else must decide.
UNANIMOUS = "unanimous"
MAJORITY = "majority"
SINGLE = "single"
NONE = "none"
OPEN = "open"


# ---------------------------------------------------------------------------
# Tier 1: is each run an answer to its own question?
# ---------------------------------------------------------------------------


def admissible(
    candidate: Candidate,
    contract: AnswerContract,
    label_map: LabelMap,
    *,
    question: str,
    faithful: bool = True,
) -> list[str]:
    """Why the candidate cannot vote, rule by rule; empty when it can.

    `question` is the original's -- E3 asks whether *it* implies rows -- and
    `faithful` is whether the candidate's wording passed the fidelity gate,
    which every run's has by construction: E2 is here so the record says
    every voter was checked.
    """
    state = candidate.state
    if candidate.outcome != ANSWERED:
        attempts = state.get("attempts", 0)
        how = f"gave up after {attempts} attempts" if candidate.outcome == "gave_up" else candidate.outcome
        return [f"E1 answered: {how}"]
    reasons = []
    if not faithful:
        reasons.append("E2 faithful: its wording did not pass the fidelity gate")
    result = state.get("result") or QueryResult()
    gaps, _ = check_rules(
        question=question,
        contract=contract,
        result=result,
        query=read_query(state.get("sql", ""), label_map),
        label_map=label_map,
    )
    if any(gap.column == NO_ROWS for gap in gaps):
        reasons.append("E3 complete enough: no rows, for a question that implies some")
    # E4 is the audit judging the rows wrong. A narrative none of whose
    # claims the audit could trace to a cell is the narrator's failure, not
    # the rows': measured, it was the one reason runs could not vote, and it
    # took the original's right answer out of B09's vote. Such a run votes,
    # and `rank` puts an audited run of its group before it.
    audit = state.get("audit") or AuditReport()
    if audit.semantic_issue:
        reasons.append(f"E4 audited: {audit.semantic_issue}")
    if result.truncated and not contract.ranked:
        reasons.append("E5 comparable: the rows hit the row cap and are not ranked, so they are a sample")
    return reasons


# ---------------------------------------------------------------------------
# Tier 2: do the runs agree?
# ---------------------------------------------------------------------------


def agree(a: QueryResult, b: QueryResult, *, ordered: bool) -> bool:
    """Either result holds the other's answer, by the benchmark's scorer. One
    direction suffices because the scorer lets the wider result carry extra
    columns: a run that returned the brand beside each SKU agrees with one
    that did not, when the SKUs and their sales are the same."""
    return result_matches(a.rows, b.rows, ordered=ordered) or result_matches(b.rows, a.rows, ordered=ordered)


def group(
    candidates: Sequence[Candidate], contract: AnswerContract, label_map: LabelMap
) -> list[Group]:
    """The admissible candidates in groups that agree, largest first.

    Ties go to the group holding the original, then to the one whose lowest
    member is lowest -- so the order never depends on which run finished
    first. Each group's representative is `rank`'s choice of its members.
    """
    voters = [c for c in candidates if c.admissible]
    parent = {c.index: c.index for c in voters}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, left in enumerate(voters):
        for right in voters[i + 1:]:
            if agree(_rows(left), _rows(right), ordered=contract.ranked):
                parent[root(right.index)] = root(left.index)
    components: dict[int, list[Candidate]] = {}
    for candidate in voters:
        components.setdefault(root(candidate.index), []).append(candidate)
    ordered_groups = sorted(
        components.values(),
        key=lambda members: (
            -len(members),
            not any(m.index == 0 for m in members),
            min(m.index for m in members),
        ),
    )
    groups = []
    for index, members in enumerate(ordered_groups):
        representative = rank(members)
        groups.append(
            Group(
                index=index,
                members=sorted(m.index for m in members),
                representative=representative.index,
                signature=signature(_rows(representative), label_map),
            )
        )
    return groups


def signature(result: QueryResult, label_map: LabelMap) -> str:
    """A result's key fact, for the record and the dissent: a single value's
    value; otherwise how many rows, and the first row's names and measure."""
    if not result.rows:
        return "no rows"
    if len(result.rows) == 1 and len(result.columns) == 1:
        return _show(result.rows[0][0])
    first = result.rows[0]
    named = [_show(first[i]) for i, column in enumerate(result.columns) if label_map.is_label(column)]
    measures = measure_columns(result, label_map)
    if measures:
        named.append(_show(first[result.columns.index(measures[0])]))
    shown = ", ".join(named) or ", ".join(_show(value) for value in first[:2])
    return f"{len(result.rows)} rows; first: {shown}"


def vote(groups: Sequence[Group], admissible_count: int, total: int) -> Agreement:
    """arch7 section 22.6's table. The quorum is a strict majority of the
    admissible, never of all the runs: a run that gave up neither agrees nor
    dissents. Two is the smallest group that agrees, so a 1-1-1 split is
    three dissenters and no majority."""
    largest = len(groups[0].members) if groups else 0
    if admissible_count == 0:
        level, why = NONE, f"none of the {total} run(s) could vote"
    elif admissible_count == 1:
        level, why = SINGLE, f"1 of {total} run(s) could vote"
    elif largest == admissible_count:
        level, why = UNANIMOUS, f"all {admissible_count} that could vote agree"
    elif largest > admissible_count / 2 and largest >= 2:
        level, why = MAJORITY, f"{largest} of the {admissible_count} that could vote agree"
    else:
        level, why = OPEN, f"no majority among {admissible_count}: the largest group is {largest}"
    return Agreement(admissible=admissible_count, agreed=largest, total=total, level=level, why=why)


# ---------------------------------------------------------------------------
# Selection: which run stands for the group
# ---------------------------------------------------------------------------


def rank(candidates: Sequence[Candidate]) -> Candidate:
    """The representative (arch7 section 22.8): complete before a gap was
    accepted, audited before claims were dropped, the original's own wording
    before a rewording's, fewer attempts, the cheaper plan, the lower index."""
    return min(candidates, key=_merit)


def _merit(candidate: Candidate) -> tuple[Any, ...]:
    state = candidate.state
    completeness = state.get("completeness") or CompletenessReport()
    audit = state.get("audit") or AuditReport()
    cost = state.get("plan_cost")
    return (
        bool(completeness.accepted_gaps),
        bool(audit.unsupported_claims),
        candidate.origin != ORIGINAL,
        state.get("attempts", 0),
        cost if cost is not None else math.inf,
        candidate.index,
    )


def _rows(candidate: Candidate) -> QueryResult:
    return candidate.state.get("result") or QueryResult()


def _show(value: Any) -> str:
    return "NULL" if value is None else str(value)
