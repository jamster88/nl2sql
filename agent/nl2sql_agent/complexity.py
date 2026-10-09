"""How hard the task in hand is: the rung each model call is routed at.

Section 15.2 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md`. Every
agent that calls a model has a rung computed for it -- light, standard or
heavy -- and the Model Router (`router.py`) turns the rung into a model. None
of this calls a model: the rung is read from state the pipeline already
holds, since a router that asked a model how hard a question is would spend
what it set out to save.

| Agent | Rung on the first pass | Climbs when |
|---|---|---|
| Supervisor | light; standard if the pre-screen flags the question | never: one call |
| SQL Generator | by the Context Aggregator's score | every repair, but a rules gap in completeness retries once in place |
| Completeness reflection | the generation's, capped at standard | with the generation |
| Insight Narrator | light or standard, by the result's size | an audit send-back |
| Repair diagnosis | one above the generator, standard at least | with the generator |

Nothing lowers a rung within a run.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .state import AnswerContract, Complexity, CompletenessReport, Issue, LiteralMatch, QueryResult

LIGHT, STANDARD, HEAVY = "light", "standard", "heavy"
RUNGS = (LIGHT, STANDARD, HEAVY)

#: Points per intent. A comparison or a trend is two queries' worth of shape
#: in one; a lookup is the floor.
INTENT_POINTS = {"lookup": 0, "aggregate": 1, "narrative": 1, "compare": 2, "trend": 2}
#: A retrieved rule whose heading names one of the traps this data is built
#: around: grain, fan-out, the fiscal calendar -- among the few chunks nearest
#: the question, since a trap section ranked twelfth was retrieved because
#: every question mentions sales, not because this one falls into it.
TRAP_HEADING = re.compile(r"\bgrains?\b|fan-?out|\bfiscal calendar\b|\bdual calendar\b|\btrap\b", re.I)
TRAP_CHUNKS = 3
#: A measure that is only a count needs no fact table behind it.
COUNT_MEASURE = re.compile(r"^\s*(?:count|number|how many)\b", re.I)
#: The nearest worked example's question similarity (cosine, bge-m3) at or
#: above which the draft is an adaptation of a verified query.
NEAR_EXEMPLAR = 0.85
LONG_QUESTION_WORDS = 40
#: Score thresholds: 1 or less is light, 2 or 3 standard, 4 or more heavy.
STANDARD_FROM, HEAVY_FROM = 2, 4

#: The Supervisor's pre-screen: free, never the only line, and there so the
#: questions most likely to be injections meet a model the calibration has
#: shown catches them.
PRESCREEN_WORDS = 60
SYSTEM_WORDS = re.compile(
    r"\b(?:ignore|instructions?|system prompt|reveal|execute)\b"
    r"|\brun (?:this|the following|a query|the query|sql|a command)\b",
    re.I,
)
SQL_TEXT = re.compile(
    r"```|;\s*$|\bselect\b[\s\S]+\bfrom\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b"
    r"|\bdrop\s+(?:table|schema|database|role)\b|\balter\s+(?:table|role)\b"
    r"|\bcreate\s+(?:table|role|user)\b|\bgrant\s+\w+\s+on\b|\btruncate\s+\w+",
    re.I,
)

#: The narrator's light rung: a scalar or a few rows of a few numbers.
NARRATOR_LIGHT_ROWS = 5
NARRATOR_LIGHT_NUMBERS = 3


def climb(rung: str, steps: int = 1) -> str:
    """`steps` rungs up the ladder, stopping at heavy."""
    return RUNGS[min(len(RUNGS) - 1, RUNGS.index(rung) + steps)]


def cap(rung: str, ceiling: str) -> str:
    return RUNGS[min(RUNGS.index(rung), RUNGS.index(ceiling))]


def at_least(rung: str, floor: str) -> str:
    return RUNGS[max(RUNGS.index(rung), RUNGS.index(floor))]


def rung_for_score(score: int) -> str:
    if score >= HEAVY_FROM:
        return HEAVY
    return STANDARD if score >= STANDARD_FROM else LIGHT


# --- the SQL Generator ------------------------------------------------------


def tables_needed(contract: AnswerContract | None) -> int:
    """How many tables the answer needs, estimated from the answer contract:
    one per entity the rows are about, one for the fact behind a measure
    that is not a plain count, one for the calendar when a period applies.

    Not the tables in scope. The scope is the union of four retrievers'
    proposals, closed and capped, and measured on the benchmark it is seven
    to ten tables for every question -- "how many stores are there?"
    included -- so it says how much was retrieved, not how hard the query
    is. The contract's estimate is within one table of every reference
    query's.
    """
    if contract is None:
        return 1
    measure = 1 if contract.measure and not COUNT_MEASURE.match(contract.measure) else 0
    return max(1, len(contract.entities) + measure + (1 if contract.period else 0))


def score_generation(
    *,
    question: str,
    intent: str,
    contract: AnswerContract | None,
    knowledge_chunks: Iterable[dict[str, Any]] = (),
    literal_map: Iterable[LiteralMatch] = (),
    example_pairs: Iterable[dict[str, Any]] = (),
) -> Complexity:
    """The generator's task, scored by the Context Aggregator -- the one node
    that has seen everything the generator will be shown."""
    score, signals = 0, []

    def add(points: int, signal: str) -> None:
        nonlocal score
        score += points
        signals.append(f"{signal} {points:+d}")

    needed = tables_needed(contract)
    if needed >= 3:
        add(2 if needed >= 5 else 1, f"about {needed} tables")
    if INTENT_POINTS.get(intent, 1):
        add(INTENT_POINTS.get(intent, 1), f"intent {intent or 'unknown'}")
    if contract is not None and contract.ranked and contract.measure:
        add(1, f"ranked by {contract.measure}")
    traps = [
        chunk.get("heading_path", "").split(">")[-1].strip()
        for chunk in list(knowledge_chunks)[:TRAP_CHUNKS]
        if TRAP_HEADING.search(chunk.get("heading_path", ""))
    ]
    if traps:
        add(1, f"rule: {traps[0]}")
    # A value the question names exactly, found in more than one column:
    # "Dairy & Eggs" as a department and a category. A word such as "year"
    # found inside a promotion's name is not a choice the generator has.
    columns: dict[str, set[tuple[str, str]]] = {}
    for match in literal_map:
        if match.value.casefold() == match.phrase.casefold():
            columns.setdefault(match.phrase.lower(), set()).add((match.table, match.column))
    ambiguous = sorted(phrase for phrase, found in columns.items() if len(found) > 1)
    if ambiguous:
        add(1, f'"{ambiguous[0]}" matches {len(columns[ambiguous[0]])} columns')
    words = len(question.split())
    if words > LONG_QUESTION_WORDS:
        add(1, f"{words} words")
    nearest = max(
        (pair for pair in example_pairs if pair.get("similarity") is not None),
        key=lambda pair: pair["similarity"],
        default=None,
    )
    if nearest is not None and nearest["similarity"] >= NEAR_EXEMPLAR:
        add(-2, f"near exemplar {nearest.get('pair_id', '?')} ({nearest['similarity']:.2f})")
    return Complexity(score=score, signals=signals, rung=rung_for_score(score))


def describe(complexity: Complexity) -> str:
    signals = ", ".join(complexity.signals) or "nothing to add"
    return f"score {complexity.score} ({signals}) -> {complexity.rung}"


def next_generation_rung(
    rung: str, holds: int, issues: Sequence[Issue], report: CompletenessReport | None
) -> tuple[str, int, str]:
    """The rung the next generation runs at, the holds spent, and why.

    Every repair climbs one rung, with one exception: a gap the completeness
    rules found names the exact column to add, so the fix is mechanical and
    the rung was not what went wrong -- that retries once where it stands. A
    second one climbs like anything else.
    """
    rules_gap = (
        bool(issues)
        and all(issue.source == "completeness" for issue in issues)
        and report is not None
        and bool(report.missing)
        and all(gap.rule != "reflection" for gap in report.missing)
    )
    if rules_gap and holds == 0:
        return rung, 1, f"{rung}, held once for a rules gap"
    raised = climb(rung)
    return raised, holds, f"{rung} -> {raised}" if raised != rung else f"{rung}, the top of the ladder"


# --- the other agents -----------------------------------------------------------


def prescreen(question: str) -> str | None:
    """Why the Supervisor should meet this question on the standard rung, or None."""
    words = len(question.split())
    if words > PRESCREEN_WORDS:
        return f"{words} words"
    match = SYSTEM_WORDS.search(question)
    if match:
        return f"system vocabulary ({match.group(0).lower()})"
    if SQL_TEXT.search(question):
        return "code or SQL in the question"
    return None


def supervisor_rung(question: str) -> tuple[str, str]:
    flag = prescreen(question)
    if flag:
        return STANDARD, f"pre-screen: {flag} -> standard"
    return LIGHT, "pre-screen clear -> light"


def paraphraser_rung(question: str) -> tuple[str, str]:
    """The Paraphraser goes where the Supervisor goes (arch7 section 22.10):
    light when the pre-screen is clear, standard when it flags the question."""
    rung, why = supervisor_rung(question)
    return rung, f"as the Supervisor: {why}"


def judge_rung() -> tuple[str, str]:
    """Heavy, always: the Judge runs once a question, before the vote, and
    decides which answers are counted at all (arch7.1), so it is where the
    strongest model costs least per decision."""
    return HEAVY, "the Judge: heavy, always"


def reflection_rung(generation_rung: str) -> tuple[str, str]:
    rung = cap(generation_rung, STANDARD)
    return rung, f"the generation's {generation_rung}, capped at standard -> {rung}"


def narrator_rung(result: QueryResult | None, *, rewrite: bool = False) -> tuple[str, str]:
    """Light for a scalar or a few rows of a few numbers, standard for more;
    an audit send-back runs one rung up. Never heavy on a first pass: the
    Audit Checker verifies every number, so a slip costs a retry, not a
    wrong answer."""
    rows = result.row_count if result is not None else 0
    numbers = _numeric_columns(result)
    small = rows <= NARRATOR_LIGHT_ROWS and numbers <= NARRATOR_LIGHT_NUMBERS
    rung = LIGHT if small else STANDARD
    why = f"{rows} row(s), {numbers} numeric column(s) -> {rung}"
    if rewrite:
        raised = climb(rung)
        return raised, f"{why}; audit send-back -> {raised}"
    return rung, why


def repair_rung(generation_rung: str) -> tuple[str, str]:
    """One above the generator's current rung, standard at least: a model
    that has just failed to write the query is not the one to ask why."""
    rung = at_least(climb(generation_rung), STANDARD)
    return rung, f"one above the generator's {generation_rung} -> {rung}"


def _numeric_columns(result: QueryResult | None) -> int:
    if result is None or not result.rows:
        return 0
    return sum(
        1 for value in result.rows[0] if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    )
