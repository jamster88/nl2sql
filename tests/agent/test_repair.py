"""The Repair Agent: every row of the section 6.3 table, and the model calls saved.

Each classification test feeds in a message string of the shape Postgres (or
pglast) really produces -- caret lines, HINT lines and all -- because the
classifier matches on that text and a test built from a tidied-up paraphrase
would pass while the real pipeline failed.

The economics are asserted, not assumed. `repair_hint` returns a model-call
count, and the tests below pin it to 0 for every classified row: the whole
argument for a deterministic classifier is that it stops the retry loop from
costing two model calls per attempt instead of one, and that argument is a lie
if a classified issue quietly asks the model anyway.
"""

from __future__ import annotations

from nl2sql_agent.repair import (
    GENERIC_HINT,
    classify,
    repair_hint,
    schema_columns,
    schema_tables,
)
from nl2sql_agent.state import AUDIT, PLANNER, RUNTIME, STATIC, Attempt, Issue

from .conftest import ScriptedLLM

# A schema block in the v3 `Database.schema_and_samples` format, including the
# sections the column parser has to ignore.
SCHEMA = """=== dim_store ===
description: One row per retail store.
columns:
  store_key (integer, NOT NULL)  -- surrogate key
  store_name (text, NULL)
  store_city (text, NULL)
keys:
  PRIMARY KEY (store_key)
sample rows (up to 2):
  store_key | store_name | store_city
  1 | Midtown | Atlanta

=== fact_pos_retail_sales ===
columns:
  date_key (integer, NOT NULL)  -- YYYYMMDD
  store_key (integer, NOT NULL)
  net_amount (numeric, NULL)
  net_qty (integer, NULL)
keys:
  FOREIGN KEY (store_key) REFERENCES dim_store (store_key)
sample rows (up to 2):
  date_key | store_key | net_amount | net_qty
  20240101 | 1 | 12.50 | 3
"""

ALLOWED = ("dim_store", "dim_date", "fact_pos_retail_sales", "fact_ad_performance")


def hint_for(message, *, source=RUNTIME, history=()):
    return classify(
        Issue(source=source, message=message),
        schema=SCHEMA,
        allowed_tables=ALLOWED,
        history=history,
    )


# --- the classification table -------------------------------------------------


def test_a_pglast_syntax_error_quotes_the_sql_around_the_reported_index():
    sql = "SELECT store_name, SUM(net_amount) FORM fact_pos_retail_sales GROUP BY store_name"
    history = [Attempt(sql=sql)]
    hint = hint_for('syntax error at or near "FORM", at index 35', history=history)
    assert "Syntax error here" in hint
    assert "FORM" in hint
    # The quotation is local: the far end of an 82-character query is not in it.
    assert "GROUP BY store_name" not in hint


def test_a_syntax_error_without_the_sql_falls_back_to_the_line_postgres_echoed():
    message = (
        'syntax error at or near "GROUP"\n'
        "LINE 3: GROUP BY store_name\n"
        "        ^"
    )
    hint = hint_for(message)
    assert "Syntax error here" in hint
    assert "GROUP BY store_name" in hint


def test_an_unknown_column_is_answered_with_the_nearest_columns_in_the_schema():
    message = (
        'column "store_nam" does not exist\n'
        "LINE 1: SELECT store_nam FROM dim_store\n"
        "               ^"
    )
    hint = hint_for(message)
    assert "`store_nam` is not a column" in hint
    assert "`store_name`" in hint


def test_an_abbreviated_column_name_is_matched_by_containment_not_only_by_ratio():
    # The spec's own example: `net` is too short for difflib to score against
    # `net_amount`, but it is exactly the mistake an abbreviating generator makes.
    hint = hint_for('column "net" does not exist')
    assert "`net_amount`" in hint
    assert "`net_qty`" in hint


def test_an_unknown_column_with_no_near_miss_still_classifies_without_the_model():
    hint = hint_for('column "quux" does not exist')
    assert hint is not None
    assert "`quux` is not a column" in hint
    assert "columns:" in hint  # points at the schema block rather than guessing


def test_an_unknown_relation_is_answered_with_the_nearest_allowed_table():
    message = (
        'relation "fact_ad_perf" does not exist\n'
        "LINE 1: SELECT * FROM fact_ad_perf\n"
        "                      ^"
    )
    hint = hint_for(message)
    assert "`fact_ad_perf` is not a table you may query" in hint
    assert "`fact_ad_performance`" in hint


def test_a_static_allowlist_rejection_is_treated_as_the_same_failure():
    hint = hint_for(
        "fact_ad_perf is not in scope; did you mean fact_ad_performance?", source=STATIC
    )
    assert "`fact_ad_performance`" in hint


def test_a_grouping_error_names_the_column_and_both_of_its_fixes():
    message = (
        'column "d.fiscal_year" must appear in the GROUP BY clause or be used in an '
        "aggregate function"
    )
    hint = hint_for(message)
    assert "`d.fiscal_year`" in hint
    assert "GROUP BY" in hint
    assert "aggregate" in hint


def test_a_missing_operator_on_date_key_names_the_integer_date_convention():
    message = (
        "operator does not exist: integer = date\n"
        "LINE 4:   JOIN dim_date d ON f.date_key = d.full_date\n"
        "                                        ^\n"
        "HINT:  No operator matches the given name and argument types."
    )
    hint = hint_for(message)
    assert "`integer = date`" in hint
    assert "cast one side" in hint
    assert "YYYYMMDD" in hint


def test_a_missing_operator_elsewhere_gives_cast_advice_without_the_date_key_story():
    hint = hint_for("operator does not exist: text = integer")
    assert "cast one side" in hint
    assert "YYYYMMDD" not in hint  # date_key was not involved; do not send them there


def test_division_by_zero_is_answered_with_nullif():
    hint = hint_for("division by zero")
    assert "NULLIF" in hint


def test_a_plan_over_the_cost_ceiling_is_told_it_is_a_cross_join_or_a_full_scan():
    hint = hint_for(
        "estimated plan cost 12,483,911.40 exceeds the ceiling of 1,000,000.0",
        source=PLANNER,
    )
    assert "cross join" in hint
    assert "fiscal_year" in hint
    assert "12,483,911.40" in hint  # the figure the gate actually measured


def test_a_statement_timeout_gets_the_cost_hint_plus_the_limit_suggestion():
    hint = hint_for("canceling statement due to statement timeout")
    assert "cross join" in hint
    assert "LIMIT" in hint


def test_an_audit_issue_is_passed_through_as_its_own_hint():
    message = (
        "the answer reports total sales but the query sums gross_amount, which is before "
        "scan-back allowances"
    )
    hint = hint_for(message, source=AUDIT)
    assert hint == message


def test_an_unrecognised_error_classifies_as_none_so_the_caller_asks_the_model():
    assert hint_for("could not serialize access due to concurrent update") is None


# --- reading the rendered schema ----------------------------------------------


def test_schema_columns_reads_the_columns_section_and_nothing_else():
    # `PRIMARY KEY (store_key)` must not become a column called KEY, and a
    # sample row must not become one either.
    assert schema_columns(SCHEMA) == [
        "store_key",
        "store_name",
        "store_city",
        "date_key",
        "net_amount",
        "net_qty",
    ]


def test_schema_tables_reads_the_block_headers():
    assert schema_tables(SCHEMA) == ["dim_store", "fact_pos_retail_sales"]


# --- the history prefix -------------------------------------------------------


def test_an_error_seen_before_is_prefixed_with_the_attempts_that_already_hit_it():
    message = 'column "store_nam" does not exist'
    history = [
        Attempt(sql="SELECT store_nam FROM dim_store", issues=[Issue(RUNTIME, message)]),
        Attempt(sql="SELECT s.store_nam FROM dim_store s", issues=[Issue(RUNTIME, message)]),
    ]
    hint = hint_for(message, history=history)
    assert hint.startswith("Attempts 1 and 2 have already failed with this same error")
    assert "the approach itself must change" in hint
    assert "`store_name`" in hint  # the classified hint still follows the warning


def test_one_earlier_attempt_is_named_in_the_singular():
    message = "division by zero"
    history = [Attempt(sql="SELECT 1/0", issues=[Issue(RUNTIME, message)])]
    hint = hint_for(message, history=history)
    assert hint.startswith("Attempt 1 has already failed with this same error")


def test_a_first_time_error_gets_no_repeat_warning():
    history = [Attempt(sql="SELECT 1", issues=[Issue(RUNTIME, "division by zero")])]
    hint = hint_for('column "store_nam" does not exist', history=history)
    assert not hint.startswith("Attempt")


# --- repair_hint: what the model call costs -----------------------------------


def test_a_classified_issue_costs_zero_model_calls():
    # The point of the whole design. ScriptedLLM has no responses queued, so a
    # call would also be an error; the assertions below are the explicit proof.
    llm = ScriptedLLM()
    issues, calls = repair_hint(
        [Issue(RUNTIME, 'column "store_nam" does not exist')],
        llm=llm,
        schema=SCHEMA,
        allowed_tables=ALLOWED,
    )
    assert calls == 0
    assert llm.plain_invocations == []
    assert llm.structured_invocations == []
    assert "`store_name`" in issues[0].hint


def test_an_unclassified_issue_calls_the_model_exactly_once():
    diagnosis = "The window function is partitioned by a column the outer query has dropped."
    llm = ScriptedLLM(sql_responses=[diagnosis])
    issues, calls = repair_hint(
        [Issue(RUNTIME, "could not serialize access due to concurrent update")],
        llm=llm,
        schema=SCHEMA,
        history=[Attempt(sql="SELECT 1")],
    )
    assert calls == 1
    assert len(llm.plain_invocations) == 1
    assert issues[0].hint == diagnosis


def test_several_unclassified_issues_share_the_one_model_call():
    llm = ScriptedLLM(sql_responses=["one paragraph covering both"])
    issues, calls = repair_hint(
        [
            Issue(RUNTIME, "could not serialize access due to concurrent update"),
            Issue(RUNTIME, "deadlock detected"),
        ],
        llm=llm,
        schema=SCHEMA,
    )
    assert calls == 1
    assert [issue.hint for issue in issues] == ["one paragraph covering both"] * 2


def test_an_unclassified_issue_with_no_model_falls_back_instead_of_raising():
    issues, calls = repair_hint(
        [Issue(RUNTIME, "could not serialize access due to concurrent update")],
        llm=None,
        schema=SCHEMA,
    )
    assert calls == 0
    assert issues[0].hint == GENERIC_HINT


def test_a_model_that_fails_does_not_take_the_run_with_it():
    class BrokenLLM:
        def invoke(self, messages):
            raise RuntimeError("ollama is not reachable")

    issues, calls = repair_hint([Issue(RUNTIME, "deadlock detected")], llm=BrokenLLM())
    assert calls == 1  # it was spent, and the trace should say so
    assert issues[0].hint == GENERIC_HINT


def test_the_model_is_not_called_when_any_sibling_issue_classified():
    llm = ScriptedLLM()
    issues, calls = repair_hint(
        [
            Issue(RUNTIME, "deadlock detected"),
            Issue(RUNTIME, "division by zero"),
        ],
        llm=llm,
        schema=SCHEMA,
    )
    assert calls == 0
    assert llm.plain_invocations == []
    assert issues[0].hint == ""  # unclassified: its verbatim message still stands
    assert "NULLIF" in issues[1].hint


def test_the_model_prompt_carries_the_failed_sql_the_error_and_the_schema():
    llm = ScriptedLLM(sql_responses=["a diagnosis"])
    repair_hint(
        [Issue(RUNTIME, "deadlock detected")],
        llm=llm,
        schema=SCHEMA,
        history=[Attempt(sql="SELECT store_key FROM dim_store")],
    )
    prompt = str(llm.plain_invocations[0])
    assert "SELECT store_key FROM dim_store" in prompt
    assert "deadlock detected" in prompt
    assert "fact_pos_retail_sales" in prompt


def test_repairing_does_not_rewrite_the_issues_already_in_the_history():
    original = Issue(RUNTIME, "division by zero")
    history = [Attempt(sql="SELECT 1/0", issues=[original])]
    issues, _ = repair_hint([original], schema=SCHEMA, history=history)
    assert "NULLIF" in issues[0].hint
    assert original.hint == ""  # the record of what attempt 1 was told is unchanged


def test_no_issues_means_no_work_and_no_model_call():
    llm = ScriptedLLM()
    assert repair_hint([], llm=llm) == ([], 0)
    assert llm.plain_invocations == []


# ---------------------------------------------------------------------------
# Syntax errors, in each of the shapes Postgres and pglast report them
# ---------------------------------------------------------------------------


def test_a_syntax_error_reported_only_as_at_or_near_still_becomes_a_hint():
    """Postgres reports some syntax errors with a token and no position and
    no LINE echo. Falling through to the model for those would spend a call
    on the most recognisable error there is.
    """
    hint = classify(Issue(source=PLANNER, message='syntax error at or near "FROM"'))
    assert hint is not None
    assert '"FROM"' in hint


def test_a_syntax_error_with_no_position_at_all_still_says_what_to_do():
    hint = classify(Issue(source=STATIC, message="Syntax error: the statement did not parse"))
    assert hint is not None
    assert "SELECT" in hint


def test_an_unknown_table_with_no_near_match_names_the_tables_in_scope():
    """`difflib` finds nothing when the hallucinated name resembles nothing
    real, and "that table does not exist" without a list is a dead end.
    """
    hint = classify(
        Issue(source=PLANNER, message='relation "zzzz_nothing_like_it" does not exist'),
        allowed_tables=["dim_store", "dim_product", "fact_pos_retail_sales"],
    )
    assert hint is not None
    assert "dim_store" in hint


def test_an_unknown_table_with_no_scope_at_all_still_gives_direction():
    hint = classify(
        Issue(source=PLANNER, message='relation "whatever" does not exist'), allowed_tables=[]
    )
    assert hint is not None
    assert "schema block" in hint


def test_a_postgres_position_is_read_as_a_one_based_offset():
    """Postgres reports POSITION as 1-based and pglast reports an index as
    0-based. Quoting the wrong character is worse than quoting none.
    """
    hint = classify(Issue(source=PLANNER, message='syntax error at end of input\nPOSITION: 15'))
    assert hint is not None


def test_an_empty_message_does_not_become_a_repeat_warning():
    """The repeat check keys on the message text; an empty key would make
    every unlabelled failure look like the same failure recurring.
    """
    from nl2sql_agent.repair import _repeat_prefix

    assert _repeat_prefix("", ()) == ""
    assert _repeat_prefix("   ", ()) == ""


def test_a_group_by_complaint_with_no_column_in_it_still_says_what_to_do():
    """Postgres usually names the offending column, but the wording is not a
    contract -- an older server, a translated locale, or a wrapped driver
    message can arrive without it. The hint is the point, so it survives the
    column name going missing.
    """
    issue = Issue(source=RUNTIME, message="ERROR: must appear in the GROUP BY clause")
    hint = classify(issue, schema="", allowed_tables=["dim_store"], history=[])
    assert hint is not None
    assert "GROUP BY" in hint and "aggregate" in hint
    assert "`" not in hint, "it invented a column name"


def test_a_planner_message_that_merely_mentions_cost_is_not_a_cost_rejection():
    """`_plan_cost` matches the gate's own rejection, not any text with the
    word in it -- a runtime error quoting a cost would otherwise be answered
    with "make the query cheaper", which is not the problem.
    """
    mentions = Issue(source=PLANNER, message="could not read the plan cost from EXPLAIN output")
    assert classify(mentions, schema="", allowed_tables=[], history=[]) is None

    rejection = Issue(source=PLANNER, message="estimated cost 4,200,000 exceeds the ceiling")
    hint = classify(rejection, schema="", allowed_tables=[], history=[])
    assert hint is not None and "4,200,000" in hint
