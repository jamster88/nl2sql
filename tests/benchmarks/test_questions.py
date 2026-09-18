"""The benchmark's ground truth.

A benchmark is only as good as its reference answers, and a reference query that
silently returns nothing scores every agent as wrong. So every one of the 15 is
executed against the shipped dataset, and its row count checked against what the
question says to expect.

The structural checks are offline; executing the references needs the retail
container (`pytest --run-docker`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "agent"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks.questions import (  # noqa: E402
    CATEGORIES,
    QUESTIONS,
    BenchmarkQuestion,
    by_category,
    by_id,
)

EXPECTED_COUNT = 15
DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail"
)


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------


def test_there_are_fifteen_questions_with_unique_ids():
    assert len(QUESTIONS) == EXPECTED_COUNT
    assert len({q.id for q in QUESTIONS}) == EXPECTED_COUNT
    assert [q.id for q in QUESTIONS] == [f"B{i:02d}" for i in range(1, EXPECTED_COUNT + 1)]


def test_every_category_is_represented():
    """A benchmark that is all easy questions reports a number that cannot
    move; one that is all hard ones cannot show a regression in the basics.
    """
    for category in CATEGORIES:
        assert by_category(category), f"no questions in the {category} category"


def test_the_hard_categories_are_not_a_token_presence():
    """Grain and fan-out are what retrieval exists for. If they were one
    question each, the headline accuracy would barely move when retrieval broke.
    """
    assert len(by_category("grain")) >= 3
    assert len(by_category("fan-out")) >= 1


def test_every_question_is_phrased_as_a_question():
    """A question mark somewhere, not necessarily last: a real user follows the
    question with an instruction ("...and give me the SKU") often enough that
    requiring it at the end would only push the set toward artificial phrasing.
    """
    for q in QUESTIONS:
        assert "?" in q.question, f"{q.id} does not read as a question"
        assert len(q.question) > 20, f"{q.id} is too terse to be realistic"


def test_no_question_leaks_its_own_sql():
    """The agent is given the question text. A question naming its tables or
    quoting its own SQL would be grading the benchmark, not the agent.
    """
    for q in QUESTIONS:
        lowered = q.question.lower()
        assert "select " not in lowered
        assert not re.search(r"\b(fact_|dim_)\w+", lowered), f"{q.id} names a table outright"


def test_no_benchmark_question_is_a_golden_pair_verbatim():
    """The agent retrieves from those 45. Reusing one measures lookup, not
    generalisation.
    """
    document = (REPO_ROOT / "context_questions" / "translated_questions.md").read_text()
    golden = set(re.findall(r'\*\*Question:\*\* "(.*?)"', document))
    assert golden, "could not read the golden pairs to compare against"
    for q in QUESTIONS:
        assert q.question not in golden, f"{q.id} is golden pair text verbatim"


def test_every_question_carries_reference_sql_that_reads_like_a_query():
    for q in QUESTIONS:
        assert q.reference_sql.strip().upper().startswith(("SELECT", "WITH"))
        assert ";" not in q.reference_sql, f"{q.id} has a statement terminator"


def test_the_hard_questions_record_the_trap_they_are_built_around():
    """A failure report that can name the trap is worth far more than one that
    says a number was wrong.
    """
    for q in QUESTIONS:
        if q.category in ("grain", "fan-out"):
            assert q.trap, f"{q.id} is a {q.category} question with no trap recorded"


def test_ordered_questions_are_the_ones_that_ask_for_an_order():
    """Row order is only part of the answer when the question asks for it, and
    the scorer keys off this flag.
    """
    for q in QUESTIONS:
        asks = any(word in q.question.lower() for word in ("highest", "lowest", "top", "which 5"))
        if q.ordered:
            assert asks, f"{q.id} is marked ordered but does not ask for an order"


def test_a_question_that_admits_two_right_answers_is_pinned_down():
    """Each of these cost a correct agent a mark until the question said which
    form it wanted. That is a defect in the benchmark, not in the agent, and it
    is the kind that quietly makes a system look worse than it is.
    """
    assert "and what were those sales" in by_id("B05").question
    assert "as a percentage" in by_id("B08").question
    assert "short code" in by_id("B09").question
    assert "type of advertising channel" in by_id("B15").question


def test_lookup_helpers():
    assert by_id("B01").category == "schema"
    with pytest.raises(KeyError):
        by_id("B99")


# ---------------------------------------------------------------------------
# The reference answers, against the real dataset
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    from nl2sql_agent.database import Database

    db = Database(DATABASE_URL, statement_timeout_ms=60000, max_rows=1000)
    try:
        db.run_select("SELECT 1")
    except Exception as exc:
        pytest.skip(f"no reachable retail database at {DATABASE_URL}: {exc}")
    return db


@pytest.mark.docker
@pytest.mark.parametrize("question", QUESTIONS, ids=[q.id for q in QUESTIONS])
def test_every_reference_query_runs_and_returns_the_expected_shape(
    database, question: BenchmarkQuestion
):
    """The check that keeps the benchmark honest. A reference returning zero
    rows would mark every agent wrong and look like a model problem.
    """
    result = database.run_select(question.reference_sql)
    assert result.rows, f"{question.id}: the reference query returned no rows"
    if question.expected_rows is not None:
        assert len(result.rows) == question.expected_rows, (
            f"{question.id}: reference returned {len(result.rows)} rows, "
            f"the question records {question.expected_rows}"
        )


@pytest.mark.docker
def test_the_reference_answers_are_stable_across_runs(database):
    """Non-determinism in a reference answer would make the benchmark score
    drift for reasons that have nothing to do with the agent.
    """
    for question in QUESTIONS:
        first = [list(r) for r in database.run_select(question.reference_sql).rows]
        second = [list(r) for r in database.run_select(question.reference_sql).rows]
        assert first == second, f"{question.id} is not deterministic"


@pytest.mark.docker
def test_the_market_share_reference_avoids_the_fan_out(database):
    """B08 exists because the naive query returns over 100%. If the reference
    ever stopped de-duplicating, the benchmark would be grading against the bug.
    """
    [[share]] = [list(r) for r in database.run_select(by_id("B08").reference_sql).rows]
    assert 0 < float(share) < 100, f"market share of {share}% means the fan-out is not handled"


@pytest.mark.docker
def test_the_grain_reference_reconciles_rather_than_joining_on_date_key(database):
    """B07's whole point. The naive join matches only month-start days and
    yields a margin computed from a sliver of the sales.
    """
    naive = (
        "SELECT ROUND(100.0 * (SUM(s.net_sales_amt) - SUM(s.quantity_sold * c.net_item_cost))"
        "       / NULLIF(SUM(s.net_sales_amt), 0), 2) "
        "FROM fact_pos_retail_sales s "
        "JOIN fact_item_cogs c ON c.date_key = s.sales_date_key "
        "  AND c.product_key = s.product_key AND c.store_key = s.store_key "
        "JOIN dim_product p ON p.product_key = s.product_key "
        "JOIN dim_date d ON d.date_key = s.sales_date_key "
        "WHERE p.department_name = 'Dairy & Eggs' AND d.fiscal_year = 2025 "
        "  AND d.fiscal_month_num = 12"
    )
    correct = [list(r) for r in database.run_select(by_id("B07").reference_sql).rows]
    wrong = [list(r) for r in database.run_select(naive).rows]
    # Both produce a percentage; what differs is how much sales they are computed
    # over, so the reference must not simply be the naive query in disguise.
    assert correct != wrong, "the reference and the naive join agree, so B07 tests nothing"
