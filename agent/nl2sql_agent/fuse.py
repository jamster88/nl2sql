"""What the delivered answer is made of, beyond the run chosen (arch7 section 22.8).

Selection -- which run stands for the winning group -- is `agreement.rank`.
Fusion then combines, in code and with no model call, what the group's
other runs found that can be combined without inventing anything:

* **Columns** (`fuse_columns`, `ENSEMBLE_FUSE_COLUMNS`). An attribute of an
  entity the chosen rows identify, which another run of the group carried
  and the chosen one did not, joined onto the chosen rows by that entity's
  key -- only when every chosen row finds exactly one match. The delivered
  SQL does not return such a column, so the answer names the run it came
  from. A column that would not join cleanly is left out, and said.
* **Claims** (`fuse_claims`). The other runs' surviving claims that speak
  of a row the narrative does not yet speak of, each kept only when the
  delivered rows reproduce it, up to `ENSEMBLE_MAX_CLAIMS`; then the whole
  narrative audited again.
* **Dissent** (`dissent`). Each answer that lost, by its key fact, and how
  its query differs from the chosen one, read from the two queries' trees:
  the tables only one of them reads, the columns only one filters on, and
  how each combines the tables it adds up -- rolled up to what before a
  join, and joined on what -- which is where a mistake of grain shows.
* **The line** (`agreement_line`): how many runs there were and how they
  agreed, the sentence every answer the ensemble chooses opens with.

The count in every sentence is the runs made, not the rewordings written:
a rewording the fidelity gate discarded was never asked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Sequence

from .completeness import _Query, entity_tables, read_query
from .contract import LabelMap
from .ensemble_state import Agreement, Candidate, DeclinedColumn, Dissent, Group, JoinedColumn, Judgement
from .present import audit, check_claim, surviving_claims
from .state import AuditReport, Claim, QueryResult


def fuse_columns(
    representative: Candidate,
    members: Sequence[Candidate],
    label_map: LabelMap,
    dimensions: Mapping[str, str],
    *,
    enabled: bool,
) -> tuple[QueryResult, list[JoinedColumn], list[DeclinedColumn]]:
    """The chosen rows, wider by the attributes the group's other runs carried.

    `members` are the group's other runs in `rank` order. For each column
    of theirs the chosen rows lack, in that order, the join rule's steps:
    the column is one of a dimension's (`dimensions`) -- a figure under
    another name, or a column of nothing the label map names, is no
    attribute and is passed over; the dimension is one the chosen rows
    identify, by its key or its label (`completeness.entity_tables`); a key
    of it is in both results; and the member's rows map that key to the
    column with no key repeated, every chosen row's key among them. A step
    that fails is a `DeclinedColumn` saying which -- unless another run's
    copy of the column joined.

    The result is a new `QueryResult`: the representative's own, in its
    candidate's record, is never changed, so each run's record shows what
    that run returned. Off (`enabled=False`), the chosen rows as they are.
    """
    own = representative.state.get("result") or QueryResult()
    if not enabled:
        return own, [], []
    result = QueryResult(columns=list(own.columns), rows=[list(row) for row in own.rows], truncated=own.truncated)
    identified = entity_tables(own, read_query(representative.state.get("sql") or "", label_map), label_map)
    joined: list[JoinedColumn] = []
    declined: list[DeclinedColumn] = []
    for member in members:
        theirs = member.state.get("result") or QueryResult()
        for column in theirs.columns:
            table = dimensions.get(column.lower())
            if table is None or _position(result.columns, column) is not None:
                continue
            why, key, values = _lookup(result, theirs, column, table, identified, label_map, member.index)
            if why:
                declined.append(DeclinedColumn(column=column, from_candidate=member.index, why=why))
                continue
            at = _position(result.columns, key)
            result.columns.append(column)
            for row in result.rows:
                row.append(values[row[at]])
            joined.append(JoinedColumn(column=column, from_candidate=member.index, key=key, table=table))
    taken = {column.column.lower() for column in joined}
    return result, joined, [column for column in declined if column.column.lower() not in taken]


def _lookup(
    result: QueryResult,
    theirs: QueryResult,
    column: str,
    table: str,
    identified: set[str],
    label_map: LabelMap,
    run: int,
) -> tuple[str, str, dict[Any, Any]]:
    """Why `column` of a run's rows cannot be joined onto `result`, or "",
    the key it joins on and each key's value."""
    if table not in identified:
        return f"{table} is not a dimension the chosen rows identify", "", {}
    keys = [label.key for label in label_map.labels() if label.table == table]
    key = next(
        (k for k in keys if _position(result.columns, k) is not None and _position(theirs.columns, k) is not None),
        None,
    )
    if key is None:
        return f"no key of {table} is in both runs' rows", "", {}
    at, value_at, mine = _position(theirs.columns, key), _position(theirs.columns, column), _position(result.columns, key)
    values: dict[Any, Any] = {}
    for row in theirs.rows:
        if row[at] in values:
            return f"{key} {row[at]} repeats in run {run}", "", {}
        values[row[at]] = row[value_at]
    for number, row in enumerate(result.rows, 1):
        if row[mine] not in values:
            return f"row {number} has no match in run {run}", "", {}
    return "", key, values


def _position(columns: Sequence[str], name: str) -> int | None:
    lowered = name.lower()
    return next((i for i, column in enumerate(columns) if column.lower() == lowered), None)


def fuse_claims(
    representative: Candidate,
    members: Sequence[Candidate],
    result: QueryResult,
    *,
    question: str,
    assumptions: Sequence[str],
    cap: int,
) -> tuple[list[Claim], AuditReport, int, int]:
    """The chosen run's narrative, with what the group's other runs said that
    the delivered rows bear out.

    The representative's surviving claims first; then each member's, in
    `rank` order, kept when it speaks of a row the narrative does not yet
    speak of and `present.check_claim` finds every cell it cites in the
    delivered rows and its value in them, until the narrative holds `cap`.
    A claim every row of which a kept claim already cites is a repeat,
    however it is worded and whichever columns it cites: four narrators of
    one top five each say the first row sold the most, and the answer
    should say it once. A claim that cites no cell has nothing the rows
    could bear out, and is dropped. The whole is audited again against the
    rows and the assumptions; the representative's own dropped claims stay
    dropped, so the answer still says how many there were.

    Returns the claims -- the representative's as narrated, then those
    added -- the audit, how many were added, and how many of the members'
    the rows did not bear out.
    """
    own = list(representative.state.get("claims") or [])
    own_report = representative.state.get("audit") or AuditReport()
    kept = surviving_claims(own, own_report)
    spoken = {row for claim in kept for row in _rows(claim)}
    added: list[Claim] = []
    dropped = 0
    for member in members:
        for claim in surviving_claims(member.state.get("claims") or [], member.state.get("audit") or AuditReport()):
            if len(kept) + len(added) >= cap:
                break
            rows = _rows(claim)
            if rows and rows <= spoken:
                continue
            if not rows or check_claim(claim, result, question=question, assumptions=assumptions):
                dropped += 1
                continue
            added.append(claim)
            spoken |= rows
    report = audit(kept + added, result, question=question, assumptions=assumptions)
    report.unsupported_claims[:0] = own_report.unsupported_claims
    report.drop_reasons[:0] = own_report.drop_reasons
    report.passed = report.passed and not own_report.unsupported_claims
    return own + added, report, len(added), dropped


def _rows(claim: Claim) -> set[int]:
    """The rows a claim speaks of, whichever of their columns it cites."""
    return {row for row, _ in claim.cells}


def dissent(chosen: Candidate, losing: Sequence[Group], candidates: Sequence[Candidate]) -> list[Dissent]:
    """Each answer that lost, by its key fact, and how its query differs
    from the chosen one -- in code, from the two queries' trees: the tables
    only one of them reads, the columns only one filters on, and, for each
    table whose values one of them aggregates, how the two combine it where
    they differ: what a nested grouping rolls it up to, and the columns it
    is joined on to the other tables they aggregate (`completeness._shapes`).
    B07's wrong answer reads the tables and filters on the columns the
    right one does; it joins daily sales to monthly costs on the date, row
    by row, where the right one rolls both up to the fiscal month first."""
    runs = {candidate.index: candidate for candidate in candidates}
    mine = read_query(chosen.state.get("sql") or "")
    said = []
    for group in losing:
        theirs = read_query(runs[group.representative].state.get("sql") or "")
        clauses = [
            f"{whose} {difference}"
            for whose, difference in (
                (f"run {group.representative}'s query", _has(theirs, mine)),
                ("the chosen one", _has(mine, theirs)),
            )
            if difference
        ]
        said.append(Dissent(group=group.index, members=list(group.members), signature=group.signature,
                            differs="; ".join(clauses)))
    return said


def _has(query: _Query, other: _Query) -> str:
    """What `query` does that `other` does not: the tables it reads, the
    columns it filters on -- compared by name, so `dim_date.fiscal_year` and
    an unqualified `fiscal_year` are one filter -- and how it combines each
    table it aggregates, where the two differ."""
    tables = sorted(query.relations - other.relations)
    filtered = {name.rsplit(".", 1)[-1] for name in other.filters}
    filters = sorted(name for name in query.filters if name.rsplit(".", 1)[-1] not in filtered)
    parts = []
    if tables:
        parts.append("uses " + ", ".join(tables))
    if filters:
        parts.append("filters on " + ", ".join(filters))
    parts.extend(_combines(query, other))
    return _listed(parts)


def _combines(query: _Query, other: _Query) -> list[str]:
    """How `query` combines each table it aggregates, where `other` does it
    otherwise or not at all: rolled up to which columns before a join, and
    joined on which -- row by row when nothing rolled it up. Tables combined
    alike are said together."""
    shapes: dict[tuple[tuple[str, ...] | None, tuple[str, ...] | None], list[str]] = {}
    for table in sorted(set(query.rolled_up) | set(query.joined_on)):
        shape = (query.rolled_up.get(table), query.joined_on.get(table))
        if shape != (other.rolled_up.get(table), other.joined_on.get(table)):
            shapes.setdefault(shape, []).append(table)
    said = []
    for (rolled, joined), tables in shapes.items():
        named, them = _listed(tables), "them" if len(tables) > 1 else "it"
        if rolled and joined:
            on = "those" if set(joined) == set(rolled) else ", ".join(joined)
            said.append(f"rolls {named} up to {', '.join(rolled)} and joins {them} on {on}")
        elif rolled:
            said.append(f"rolls {named} up to {', '.join(rolled)}")
        else:
            said.append(f"joins {named} on {', '.join(joined or ())}, row by row")
    return said


def _listed(items: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return f"{', '.join(items[:-1])} and {items[-1]}" if len(items) > 1 else "".join(items)


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
