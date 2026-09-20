"""The Static Validator: what it accepts, and every way it says no.

`test_database_safety.py` pins the *limits* of the regex prefix check -- most
pointedly that it waves through a `WITH ... (DELETE ...) ... SELECT`. This
file is the other side of that: the parse-tree checks that catch what the
prefix cannot, so between the two files the read-only guarantee is covered
statically as well as at execution time.

Two properties matter more than the individual cases. A rejection must be
*right* -- a false positive costs a retry out of a budget of four and can
turn a correct query into no answer, which is exactly the v3 failure this
component was rebuilt to avoid -- and a rejection must carry a message the
Repair Agent can turn into a hint, so the assertions look at message text and
not merely at the count.

No database and no model: `pglast` parses in-process.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.state import STATIC
from nl2sql_agent.validate import FUNCTION_DENYLIST, validate

RETAIL_TABLES = ["fact_pos_retail_sales", "dim_date", "dim_store", "dim_product"]


def messages(sql: str, **kwargs) -> str:
    """Every issue's message joined, lowercased, for substring assertions."""
    return " | ".join(issue.message for issue in validate(sql, **kwargs)).lower()


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select store_name from dim_store",
        "SELECT * FROM dim_store WHERE store_key = 1 ORDER BY store_name LIMIT 10",
        "WITH s AS (SELECT * FROM dim_store) SELECT count(*) FROM s",
        "SELECT a FROM t UNION ALL SELECT b FROM u",
        "```sql\nSELECT 1;\n```",
    ],
)
def test_a_plain_read_only_select_raises_no_issues(sql: str):
    assert validate(sql) == []


def test_a_semicolon_inside_a_string_literal_is_not_a_second_statement():
    """The prefix check counts statements by looking for a semicolon, so it
    rejects this; the parser counts statements and accepts it. A separator
    like `string_agg(name, '; ')` is ordinary analytics SQL, and rejecting it
    would spend a repair attempt on a correct query.
    """
    assert validate("SELECT string_agg(store_name, '; ') FROM dim_store") == []


def test_a_complex_query_with_joins_ctes_and_window_functions_passes_clean():
    """The shape the SQL Generator actually produces for a trend question: a
    CTE, a join on the declared keys, a grouped aggregate, and two window
    functions. None of it may trip any check.
    """
    sql = """
        WITH monthly AS (
            SELECT d.fiscal_year,
                   d.fiscal_month,
                   SUM(f.sales_amount) AS sales
            FROM fact_pos_retail_sales f
            JOIN dim_date d ON d.date_key = f.date_key
            JOIN dim_store s ON s.store_key = f.store_key
            WHERE s.region = 'Pacific Northwest'
            GROUP BY d.fiscal_year, d.fiscal_month
        )
        SELECT fiscal_year,
               fiscal_month,
               sales,
               RANK() OVER (ORDER BY sales DESC) AS sales_rank,
               sales - LAG(sales) OVER (ORDER BY fiscal_year, fiscal_month) AS change
        FROM monthly
        ORDER BY fiscal_year, fiscal_month
    """
    assert validate(sql, allowed_tables=RETAIL_TABLES) == []


# ---------------------------------------------------------------------------
# Issue shape
# ---------------------------------------------------------------------------


def test_every_issue_is_attributed_to_the_static_source():
    issues = validate("DELETE FROM dim_store")
    assert issues and all(issue.source == STATIC for issue in issues)


def test_an_issue_leaves_the_hint_empty_for_the_repair_agent_to_fill():
    (issue,) = validate("DELETE FROM dim_store")
    assert issue.hint == ""


# ---------------------------------------------------------------------------
# Not one read-only statement
# ---------------------------------------------------------------------------


def test_multiple_statements_are_rejected_and_counted():
    assert "exactly one statement" in messages("SELECT 1; SELECT 2")


def test_an_empty_query_is_rejected():
    assert "empty" in messages("   ")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_store",
        "UPDATE dim_store SET store_name = 'x'",
        "INSERT INTO dim_store VALUES (1)",
        "DROP TABLE dim_store",
        "CREATE TABLE backup AS SELECT * FROM dim_store",
        "I'm sorry, I can't answer that from this schema.",
    ],
)
def test_anything_that_is_not_a_select_is_rejected(sql: str):
    assert validate(sql) != []


@pytest.mark.parametrize(
    ("sql", "statement_kind"),
    [
        ("WITH x AS (SELECT 1 AS a) INSERT INTO t SELECT a FROM x", "insertstmt"),
        ("WITH x AS (SELECT 1 AS a) UPDATE t SET b = (SELECT a FROM x)", "updatestmt"),
        ("WITH x AS (SELECT 1 AS a) DELETE FROM t WHERE b IN (SELECT a FROM x)", "deletestmt"),
    ],
)
def test_a_with_clause_in_front_of_a_write_does_not_disguise_it(sql: str, statement_kind: str):
    """These start with `WITH`, so the prefix check passes them; the outer
    statement is the write, and the parser names it.
    """
    assert statement_kind in messages(sql)


# ---------------------------------------------------------------------------
# W4: the data-modifying CTE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d", "delete"),
        ("WITH d AS (INSERT INTO dim_store VALUES (1) RETURNING *) SELECT * FROM d", "insert"),
        ("WITH d AS (UPDATE dim_store SET region = 'x' RETURNING *) SELECT * FROM d", "update"),
        (
            "WITH d AS ("
            "  MERGE INTO dim_store t USING dim_product s ON t.k = s.k"
            "  WHEN MATCHED THEN DELETE RETURNING *"
            ") SELECT * FROM d",
            "merge",
        ),
    ],
)
def test_a_writing_cte_is_rejected_in_all_four_dml_forms(sql: str, kind: str):
    """The check this module exists for. The outer statement is a `SelectStmt`
    in every one of these, so the statement-kind check above sees nothing
    wrong; only the CTE body gives it away. `test_database_safety.py` pins
    that the prefix check lets the DELETE form straight through.
    """
    text = messages(sql)
    assert kind in text
    assert "data-modifying" in text


def test_a_writing_cte_names_the_cte_so_the_hint_can_point_at_it():
    (issue,) = validate("WITH doomed AS (DELETE FROM dim_store RETURNING *) SELECT * FROM doomed")
    assert '"doomed"' in issue.message


def test_a_writing_cte_nested_inside_a_reading_one_is_still_found():
    """Nesting is the reason this walks the tree instead of reading
    `stmt.withClause.ctes` once.
    """
    sql = """
        WITH outer_cte AS (
            WITH inner_cte AS (DELETE FROM dim_store RETURNING *)
            SELECT * FROM inner_cte
        )
        SELECT * FROM outer_cte
    """
    assert '"inner_cte"' in messages(sql)


# ---------------------------------------------------------------------------
# SELECT ... INTO
# ---------------------------------------------------------------------------


def test_select_into_is_rejected_because_it_creates_a_table():
    text = messages("SELECT * INTO backup FROM dim_store")
    assert "into" in text
    assert "backup" in text


def test_select_into_is_rejected_from_inside_a_cte_body_too():
    sql = "WITH s AS (SELECT * INTO backup FROM dim_store) SELECT * FROM s"
    assert "backup" in messages(sql)


# ---------------------------------------------------------------------------
# The function denylist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("function", sorted(FUNCTION_DENYLIST))
def test_every_denylisted_function_is_rejected(function: str):
    assert function in messages(f"SELECT {function}(1)")


def test_a_denylisted_function_is_found_inside_a_subquery():
    """A scan of the target list would miss this; the call is three levels
    down, in the WHERE clause of a subquery in a WHERE clause.
    """
    sql = """
        SELECT store_name
        FROM dim_store
        WHERE store_key IN (
            SELECT store_key FROM fact_pos_retail_sales
            WHERE pg_sleep(10) IS NULL
        )
    """
    assert "pg_sleep" in messages(sql)


def test_a_denylisted_function_is_found_inside_a_cte_body():
    sql = "WITH s AS (SELECT pg_read_file('/etc/passwd') AS secret) SELECT * FROM s"
    assert "pg_read_file" in messages(sql)


def test_schema_qualifying_a_denylisted_function_does_not_hide_it():
    assert "pg_sleep" in messages("SELECT pg_catalog.pg_sleep(5)")


def test_the_denylist_matches_regardless_of_case():
    assert "sleep" in messages("SELECT PG_SLEEP(5)")


def test_an_ordinary_function_whose_name_merely_contains_a_denied_one_is_allowed():
    """The match is on the function name, not on the text of the query, so a
    column or a function called `pg_sleep_summary` is not a denied call.
    """
    assert validate("SELECT pg_sleep_summary(store_key) FROM dim_store") == []


# ---------------------------------------------------------------------------
# The table allowlist
# ---------------------------------------------------------------------------


def test_no_allowlist_means_the_scope_check_is_skipped_entirely():
    """`None` is "the caller has not said", which is not the same as "nothing
    is in scope"; a validator call without `selected_tables` must not reject
    every query in the codebase.
    """
    assert validate("SELECT * FROM some_table_nobody_selected") == []


def test_a_table_outside_the_allowlist_is_rejected():
    assert "not in scope" in messages("SELECT * FROM dim_vendor", allowed_tables=RETAIL_TABLES)


def test_a_near_miss_names_the_closest_allowed_table():
    text = messages("SELECT * FROM fact_ad_perf", allowed_tables=["fact_ad_performance"])
    assert text == "fact_ad_perf is not in scope; did you mean fact_ad_performance?"


def test_a_name_nothing_resembles_is_rejected_without_a_guess():
    """A suggestion below the similarity cutoff is worse than none: it sends
    the generator at a table that has nothing to do with the question.
    """
    text = messages("SELECT * FROM zzz_unrelated", allowed_tables=["fact_ad_performance"])
    assert text == "zzz_unrelated is not in scope."


def test_every_table_in_a_join_is_checked_not_just_the_first():
    text = messages(
        "SELECT * FROM dim_store s JOIN dim_vendor v ON v.k = s.k",
        allowed_tables=["dim_store"],
    )
    assert "dim_vendor" in text
    assert "dim_store is not" not in text


def test_a_table_referenced_only_inside_a_cte_body_is_checked():
    text = messages(
        "WITH s AS (SELECT * FROM dim_vendor) SELECT * FROM s",
        allowed_tables=["dim_store"],
    )
    assert "dim_vendor" in text


def test_a_cte_alias_is_not_itself_treated_as_a_table():
    """The false positive that would break every valid CTE query: `s` is a
    name the query defines, not a relation that has to be in scope.
    """
    sql = "WITH s AS (SELECT * FROM dim_store) SELECT * FROM s"
    assert validate(sql, allowed_tables=["dim_store"]) == []


def test_a_recursive_cte_alias_is_not_treated_as_a_table_either():
    sql = """
        WITH RECURSIVE walk AS (
            SELECT store_key, parent_key FROM dim_store WHERE parent_key IS NULL
            UNION ALL
            SELECT d.store_key, d.parent_key
            FROM dim_store d JOIN walk w ON d.parent_key = w.store_key
        )
        SELECT * FROM walk
    """
    assert validate(sql, allowed_tables=["dim_store"]) == []


def test_a_subquery_alias_is_not_treated_as_a_table():
    sql = "SELECT * FROM (SELECT store_key FROM dim_store) AS inner_rows"
    assert validate(sql, allowed_tables=["dim_store"]) == []


def test_schema_qualifying_an_allowed_table_still_matches_it():
    assert validate("SELECT * FROM public.dim_store", allowed_tables=["dim_store"]) == []


def test_the_allowlist_comparison_ignores_case():
    assert validate("SELECT * FROM DIM_STORE", allowed_tables=["dim_store"]) == []


def test_an_empty_allowlist_puts_nothing_in_scope():
    """Distinct from `None`: an empty `selected_tables` means the retriever
    found nothing, and a query over any table is then out of scope.
    """
    assert "not in scope" in messages("SELECT * FROM dim_store", allowed_tables=[])


def test_a_query_over_no_tables_survives_an_empty_allowlist():
    assert validate("SELECT 1", allowed_tables=[]) == []


# ---------------------------------------------------------------------------
# Syntax errors
# ---------------------------------------------------------------------------


def test_a_syntax_error_reports_the_character_position_and_quotes_the_text():
    """The Repair Agent's classified hint for a syntax error is "quote the
    characters around position n", so the position and the surrounding text
    have to survive into the message.
    """
    (issue,) = validate("SELECT store_name FROM WHERE store_key = 1")
    assert "syntax error" in issue.message.lower()
    assert "character 23" in issue.message
    assert "WHERE store_key = 1" in issue.message


def test_a_syntax_error_near_the_start_does_not_run_off_the_front_of_the_query():
    (issue,) = validate("SELECT ,, FROM dim_store")
    assert "character 7" in issue.message
    assert issue.message.endswith("Here: SELECT >>>,, FROM dim_store")


def test_a_syntax_error_the_parser_cannot_place_still_reports_its_message():
    """`pglast` gives no offset for "at end of input"; the message is still
    the most useful thing the Repair Agent has, so it must survive.
    """
    (issue,) = validate("SELECT * FROM dim_store WHERE")
    assert "end of input" in issue.message
    assert "character" not in issue.message


def test_a_syntax_error_is_the_only_issue_reported():
    """There is no tree to walk after a parse failure, so reporting anything
    alongside it would be a guess.
    """
    assert len(validate("SELECT pg_sleep(1) FROM WHERE", allowed_tables=["dim_store"])) == 1


# ---------------------------------------------------------------------------
# Several problems at once
# ---------------------------------------------------------------------------


def test_independent_problems_are_all_reported_in_one_pass():
    """One repair round should be able to fix everything the validator can
    see, so the checks accumulate rather than stop at the first failure.
    """
    sql = "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT pg_sleep(1) FROM dim_vendor"
    text = messages(sql, allowed_tables=["dim_store"])
    assert "data-modifying" in text
    assert "pg_sleep" in text
    assert "dim_vendor" in text


def test_a_syntax_error_deep_in_a_long_query_is_quoted_with_ellipses():
    """The window is what the Repair Agent shows the generator. On a long
    query it has to say the text was clipped, or the generator reads the
    fragment as the whole statement.
    """
    padding = ", ".join(f"col_{i}" for i in range(60))
    issues = validate(f"SELECT {padding} FROM dim_store WHERE AND x = 1")
    assert len(issues) == 1
    message = issues[0].message
    assert "Syntax error at character" in message
    assert ">>>" in message
    assert message.count("...") >= 1, message


def test_a_syntax_error_near_the_start_is_not_prefixed_with_an_ellipsis():
    issues = validate("SELECT FROM")
    assert issues
    assert "Here: ..." not in issues[0].message
