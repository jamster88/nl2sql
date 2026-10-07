"""Validating a SQL snippet: the checks, the probe, and what each kind is asked.

The unit half drives `validate_snippet` with a scripted connection, so every
branch runs on every invocation. The live half, behind `--run-docker`, runs
every snippet in the real document against the real retail database as the
reader role -- the claim the document makes in its first paragraph -- and
shows each kind's own question being asked of real data: a fan-out join, a
filter spelled differently from the data, a measure that is not an
aggregate.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nl2sql_review.snippet_validation import (
    KIND_ROLES,
    KINDS,
    SnippetValidation,
    clean,
    count_for,
    probe_for,
    static_problems,
    tables_named,
    validate_snippet,
)
from tests import live_stores
import psycopg


class Refusal(psycopg.Error):
    """A database refusing a statement, as the driver raises it. `diag` is a
    plain attribute here so a test can say what the server said."""

    diag = None



ROOT = Path(__file__).resolve().parent.parent.parent
KNOWN = ["dim_date", "dim_product", "fact_pos_retail_sales"]


# ---------------------------------------------------------------------------
# Before anything runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,piece,expected",
    [
        ("```sql\nd.is_holiday\n```", "filter", "d.is_holiday"),
        ("WHERE d.is_holiday;", "filter", "d.is_holiday"),
        ("FROM dim_date d", "applies_to", "dim_date d"),
        ("  SUM(x) ; ", "measure", "SUM(x)"),
    ],
)
def test_each_piece_is_cleaned_to_the_fragment_the_probe_places(text, piece, expected):
    assert clean(text, piece) == expected


def test_every_kind_has_a_role_and_an_unknown_one_is_refused():
    assert set(KIND_ROLES) == set(KINDS) == {"join", "filter", "measure", "dimension"}
    assert static_problems("having", "t", "x") == ["'having' is not a kind of snippet; it is one of join, filter, measure, dimension"]


@pytest.mark.parametrize(
    "kind,applies_to,sql,message",
    [
        ("filter", "", "x", "applies to is empty"),
        ("filter", "t", "", "the SQL is empty"),
        ("filter", "t", "x; DROP TABLE t", "contains a semicolon"),
        ("filter", "t", "x -- the rest", "contains a comment"),
        ("filter", "t /* c */", "x", "applies to contains a comment"),
        ("filter", "t", "x ``` y", "contains a ``` fence"),
        ("filter", "t", "(x", "leaves 1 parenthesis(es) open"),
        ("filter", "t", "x) OR (y", "closes a parenthesis it never opened"),
        ("filter", "t", "x = 'open", "has an unclosed ' quote"),
        ("filter", "SELECT * FROM t", "x", "applies to is a FROM clause"),
        ("join", "t a", "u b ON b.k = a.k", "a join snippet starts with JOIN"),
        ("measure", "t", "SELECT sum(x) FROM t", "a measure snippet is an aggregate expression, not a query"),
    ],
)
def test_a_fragment_that_could_change_the_probe_is_refused_before_it_runs(kind, applies_to, sql, message):
    assert any(message in p for p in static_problems(kind, applies_to, sql))


@pytest.mark.parametrize("join", ["JOIN u", "left join u", "LEFT OUTER JOIN u", "inner join u", "CROSS JOIN u", "full outer join u"])
def test_every_kind_of_join_is_a_join(join):
    assert static_problems("join", "t a", f"{join} b ON true") == []


def test_quotes_inside_a_literal_are_not_parentheses_or_comments():
    assert static_problems("filter", "t", "x = 'a (b' AND y = 'it''s'") == []


def test_each_kind_is_checked_inside_the_query_it_plays_its_part_in():
    assert probe_for("join", "t a", "JOIN u b ON b.k = a.k", 5) == "SELECT *\nFROM t a\nJOIN u b ON b.k = a.k\nLIMIT 5"
    assert probe_for("filter", "t", "x", 5) == "SELECT *\nFROM t\nWHERE x\nLIMIT 5"
    assert probe_for("measure", "t", "sum(x)") == "SELECT * FROM (\n  SELECT sum(x) AS value\n  FROM t\n) probe\nLIMIT 2"
    assert probe_for("dimension", "t", "x", 5) == (
        "SELECT x AS value, count(*) AS row_count\nFROM t\nGROUP BY 1\nORDER BY 2 DESC\nLIMIT 5"
    )
    assert "rows_after" in count_for("join", "t a", "JOIN u b ON true")
    assert "count(*) FILTER (WHERE x)" in count_for("filter", "t", "x")
    assert count_for("measure", "t", "sum(x)") is None and count_for("dimension", "t", "x") is None


def test_the_tables_a_snippet_names_are_found_by_word():
    assert tables_named("fact_pos_retail_sales f JOIN dim_date d", KNOWN) == ["dim_date", "fact_pos_retail_sales"]
    assert tables_named("dim_dates", KNOWN) == []


# ---------------------------------------------------------------------------
# Against a scripted connection
# ---------------------------------------------------------------------------


class Cursor:
    def __init__(self, *, columns=None, rows=(), one=None):
        self.description = [SimpleNamespace(name=c) for c in columns] if columns else None
        self._rows = list(rows)
        self._one = one

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, size):
        return self._rows[:size]


class Conn:
    """Answers, by what the statement is: the catalog, the plan, the probe, the count."""

    def __init__(self, *, rows=((1,),), columns=("value",), counts=(10, 10), fail=None, tables=KNOWN):
        self.rows, self.columns, self.counts, self.fail, self.tables = list(rows), list(columns), counts, fail, tables
        self.statements: list[str] = []
        self.rolled_back = self.closed = False

    def execute(self, statement, params=None):
        text = str(statement)
        self.statements.append(text)
        if text.startswith(("SET TRANSACTION", "SET LOCAL")) or "Composed" in text:
            return Cursor()
        if "information_schema.tables" in text:
            return Cursor(rows=[(t,) for t in self.tables])
        if self.fail is not None:
            raise self.fail
        if text.startswith("EXPLAIN"):
            return Cursor(one=([{"Plan": {"Total Cost": 7.5}}],))
        if "rows_before" in text:
            return Cursor(one=self.counts)
        return Cursor(columns=self.columns, rows=self.rows)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def run(kind="filter", applies_to="dim_date d", sql="d.is_holiday", conn=None, **kwargs) -> tuple[SnippetValidation, Conn]:
    conn = conn or Conn()
    result = validate_snippet(kind, applies_to, sql, url="postgresql://r", connect=lambda url, **o: conn, **kwargs)
    return result, conn


def test_a_snippet_that_runs_is_valid_and_reports_what_it_ran():
    result, conn = run(conn=Conn(rows=[(1, "2024-04-01")], columns=("date_key", "calendar_date"), counts=(731, 32)))
    assert result.valid and result.problems == [] and result.warnings == []
    assert result.probe_sql == "SELECT *\nFROM dim_date d\nWHERE d.is_holiday\nLIMIT 20"
    assert (result.rows_before, result.rows_after) == (731, 32)
    assert result.columns == ["date_key", "calendar_date"] and result.rows == [[1, "2024-04-01"]]
    assert result.tables == ["dim_date"] and result.plan_cost == 7.5
    assert conn.statements[0] == "SET TRANSACTION READ ONLY"
    assert conn.rolled_back and conn.closed


def test_the_known_tables_can_be_handed_in_rather_than_read():
    result, conn = run(known_tables=["dim_date"])
    assert result.tables == ["dim_date"]
    assert not any("information_schema" in s for s in conn.statements)


@pytest.mark.parametrize(
    "counts,warning",
    [
        ((10, 50), "the join multiplies rows: 10 become 50 (5.00 per row)"),
        ((10, 7), "the join drops rows: 3 of 10 find no match"),
    ],
)
def test_a_join_that_multiplies_or_drops_rows_says_by_how_much(counts, warning):
    result, _ = run("join", "fact_market_share_weekly m", "JOIN dim_date d ON d.date_key = m.week_key", Conn(counts=counts))
    assert result.valid and any(w.startswith(warning) for w in result.warnings)


def test_a_join_that_keeps_every_row_and_one_from_nothing_say_nothing():
    for counts in ((10, 10), (0, 3)):
        result, _ = run("join", "t a", "RIGHT JOIN u b ON b.k = a.k", Conn(counts=counts))
        assert result.warnings == []


@pytest.mark.parametrize(
    "counts,warning",
    [((10, 0), "the filter matches no rows"), ((10, 10), "the filter matches every one of the 10 rows")],
)
def test_a_filter_that_matches_nothing_or_everything_is_warned_about(counts, warning):
    result, _ = run(conn=Conn(counts=counts))
    assert result.valid and result.warnings[0].startswith(warning)


def test_a_measure_has_to_be_one_value():
    one, _ = run("measure", "fact_pos_retail_sales f", "SUM(f.net_sales_amt)", Conn(rows=[("12.5",)]))
    assert one.valid and one.rows_before is None
    many, _ = run("measure", "fact_pos_retail_sales f", "f.net_sales_amt", Conn(rows=[(1,), (2,)]))
    assert not many.valid
    assert many.problems == [
        "a measure comes back as one value over the rows -- SUM, COUNT, AVG and the like -- "
        "and this returned one value per row"
    ]


def test_a_dimension_over_nothing_is_warned_about():
    grouped, _ = run("dimension", "dim_date d", "d.fiscal_year", Conn(rows=[(2025, 365)], columns=("value", "row_count")))
    assert grouped.valid and grouped.warnings == []
    empty, _ = run("dimension", "dim_date d", "d.fiscal_year", Conn(rows=[]))
    assert empty.warnings == ["the expression grouped no rows; the FROM clause it applies to is empty"]


def test_a_static_problem_never_reaches_the_database():
    opened = []
    result = validate_snippet("filter", "t", "x; DELETE FROM t", url="u", connect=lambda *a, **k: opened.append(1))
    assert not result.valid and opened == [] and result.probe_sql == ""


def test_the_databases_refusal_is_the_problem():
    error = Refusal("x")
    error.diag = SimpleNamespace(message_primary='column d.date_ky does not exist', message_hint="Perhaps you meant d.date_key.")
    result, conn = run(conn=Conn(fail=error))
    assert not result.valid
    assert result.problems == ["the database refused it: column d.date_ky does not exist -- Perhaps you meant d.date_key."]
    assert conn.rolled_back and conn.closed


def test_a_database_that_cannot_be_reached_is_a_problem_not_a_crash():
    def down(url, **options):
        raise OSError("connection refused")

    result = validate_snippet("filter", "t", "x", url="u", connect=down)
    assert result.problems == ["cannot reach the retail database to validate it: connection refused"]


def test_a_validation_serialises_whole():
    result, _ = run()
    assert set(result.as_dict()) == {
        "kind", "valid", "problems", "warnings", "probe_sql", "applies_to", "sql", "columns", "rows",
        "rows_before", "rows_after", "tables", "plan_cost", "elapsed_ms",
    }


# ---------------------------------------------------------------------------
# Live: the real document against the real retail database
# ---------------------------------------------------------------------------

RETAIL_URL = live_stores.url("retail", variable="RETAIL_DB_URL", driver="postgresql")


@pytest.fixture(scope="module")
def live():
    psycopg = pytest.importorskip("psycopg")
    try:
        psycopg.connect(RETAIL_URL, connect_timeout=3).close()
    except psycopg.Error as exc:
        live_stores.unreachable("retail database", RETAIL_URL, exc)
    return RETAIL_URL


def _document():
    import sys

    sys.path.insert(0, str(ROOT / "rag"))
    from ragproc import snippets as parser

    return parser.parse_document(ROOT / "context_questions" / "sql_snippets.md")


@pytest.mark.docker
@pytest.mark.parametrize("snippet", _document(), ids=lambda s: s.snippet_id)
def test_live_every_snippet_in_the_document_runs_and_lists_the_tables_it_uses(live, snippet):
    """The document's first claim: every snippet was run before it was written down."""
    result = validate_snippet(snippet.kind, snippet.applies_to, snippet.sql, url=live)
    assert result.valid, result.problems
    assert set(result.tables) == set(snippet.table_list)
    # A starter snippet that fans out or matches nothing would be teaching a mistake.
    assert not any("multiplies" in w or "matches no rows" in w for w in result.warnings), result.warnings


@pytest.mark.docker
def test_live_the_market_share_fan_out_is_caught(live):
    result = validate_snippet(
        "join", "dim_product p", "JOIN fact_market_share_weekly m ON m.product_key = p.product_key", url=live
    )
    assert result.valid and any("the join multiplies rows" in w for w in result.warnings)


@pytest.mark.docker
def test_live_a_literal_spelled_differently_from_the_data_is_caught(live):
    result = validate_snippet("filter", "dim_product p", "p.department_name = 'Dairy and Eggs'", url=live)
    assert result.valid and result.rows_after == 0
    assert result.warnings[0].startswith("the filter matches no rows")


@pytest.mark.docker
def test_live_the_wrong_date_column_is_refused_in_postgres_words(live):
    result = validate_snippet("join", "fact_pos_retail_sales f", "JOIN dim_date d ON d.date_key = f.date_key", url=live)
    assert not result.valid and "f.date_key does not exist" in result.problems[0]


@pytest.mark.docker
def test_live_a_column_is_not_a_measure_and_an_aggregate_is_not_a_dimension(live):
    column = validate_snippet("measure", "fact_pos_retail_sales f", "f.net_sales_amt", url=live)
    assert not column.valid and "one value per row" in column.problems[0]
    aggregate = validate_snippet("dimension", "fact_pos_retail_sales f", "SUM(f.net_sales_amt)", url=live)
    assert not aggregate.valid and "aggregate functions are not allowed in GROUP BY" in aggregate.problems[0]


@pytest.mark.docker
def test_live_a_snippet_cannot_take_a_lock_or_write(live):
    """The reader may not lock a row, and the transaction may not write: two
    refusals from the server, either of which stops the probe."""
    result = validate_snippet(
        "filter", "dim_date d", "d.date_key IN (SELECT date_key FROM dim_date FOR UPDATE)", url=live
    )
    assert not result.valid
    assert "permission denied" in result.problems[0] or "read-only transaction" in result.problems[0]
