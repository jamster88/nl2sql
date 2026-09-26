"""Stage 4: the chart lookup, the narrator's one call, and the audit.

What this file proves, in the order section 7 of the architecture asks for it:

* the chart is a **lookup on shape**, so every row of the 7.1 table is pinned
  here, including the two ties `intent` settles and the date column this
  warehouse hides inside an integer key;
* a claim is only published when the rows reproduce it -- the value is a
  cell, or a formula over cited cells gives the value -- and a number in the
  sentence that nothing backs costs the claim;
* a column tagged sensitive in the catalog never reaches a sentence or the
  table, only its aggregates;
* the audit raises `semantic_issue` when the rows say the *SQL* is wrong, so
  the graph can spend one shared retry rather than narrate nonsense;
* `formula` is model output and is **refused, not executed**, when it is
  anything but arithmetic over `cells`;
* everything rendered is HTML-escaped, which is where the XSS concern in the
  security table belongs.

No database and no model: the rows are hand-built `QueryResult`s and the
narrator is a scripted fake.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from nl2sql_agent.present import (
    CellRef,
    ChartSpec,
    FormulaError,
    NarratedClaim,
    Narrative,
    assumptions_block,
    audit,
    audit_issue,
    check_claim,
    choose_chart,
    describe_chart,
    evaluate_formula,
    missing_assumptions,
    narrate,
    redact,
    render_answer,
    render_table,
    states_assumption,
    surviving_claims,
    tagged_sensitive_columns,
)
from nl2sql_agent.state import AUDIT, AuditReport, Claim, CompletenessReport, MissingColumn, QueryResult

from .conftest import ScriptedLLM

# A formula must not be able to call this, or anything else with a name.
EXECUTED: list[str] = []


def boom() -> float:
    EXECUTED.append("boom")
    return 0.0


def prompt_text(llm: ScriptedLLM) -> str:
    """What the narrator was actually shown, as one string."""
    _, messages = llm.structured_invocations[-1]
    return "\n".join(getattr(m, "content", str(m)) for m in messages)


def margins() -> QueryResult:
    """The spec's own worked example, as rows."""
    return QueryResult(
        columns=["department_name", "fiscal_month", "gross_margin_pct", "fy_avg_pct"],
        rows=[
            ["Dairy & Eggs", 12, Decimal("31.4"), Decimal("29.3")],
            ["Bakery", 12, Decimal("22.0"), Decimal("21.0")],
        ],
    )


# ---------------------------------------------------------------------------
# 7.1 Visual Formatter: every row of the shape table
# ---------------------------------------------------------------------------


def test_one_row_and_one_column_is_a_scalar():
    chart = choose_chart(QueryResult(columns=["net_sales"], rows=[[Decimal("1234.56")]]))
    assert chart.kind == "scalar"
    assert chart.y == ["net_sales"]


def test_one_category_and_one_measure_is_a_bar():
    result = QueryResult(
        columns=["department_name", "net_sales"],
        rows=[["Dairy & Eggs", 10.0], ["Bakery", 8.0]],
    )
    chart = choose_chart(result)
    assert (chart.kind, chart.x, chart.y) == ("bar", "department_name", ["net_sales"])


def test_a_date_column_and_one_measure_is_a_line():
    result = QueryResult(
        columns=["business_date", "net_sales"],
        rows=[[date(2025, 1, 1), 10.0], [date(2025, 1, 2), 11.0]],
    )
    chart = choose_chart(result)
    assert (chart.kind, chart.x) == ("line", "business_date")


def test_an_integer_date_key_is_still_a_date_column():
    # date_key is YYYYMMDD in an integer column: by value it is a measure, and
    # only the name says otherwise. Getting this wrong draws a trend as a
    # scatter of two numbers, which is the whole reason the name is checked.
    result = QueryResult(
        columns=["date_key", "net_sales"],
        rows=[[20250101, 10.0], [20250102, 11.0]],
    )
    chart = choose_chart(result)
    assert (chart.kind, chart.x, chart.y) == ("line", "date_key", ["net_sales"])


def test_a_fiscal_week_number_is_a_date_column_too():
    result = QueryResult(
        columns=["fiscal_week", "market_share_pct"],
        rows=[[1, 12.0], [2, 13.0]],
    )
    assert choose_chart(result).kind == "line"


def test_one_category_and_two_measures_is_a_grouped_bar():
    result = QueryResult(
        columns=["department_name", "net_sales", "units"],
        rows=[["Dairy & Eggs", 10.0, 3], ["Bakery", 8.0, 2]],
    )
    chart = choose_chart(result)
    assert chart.kind == "grouped_bar"
    assert (chart.x, chart.y) == ("department_name", ["net_sales", "units"])


def test_two_measures_and_no_category_is_a_scatter():
    result = QueryResult(
        columns=["basket_size", "net_sales"],
        rows=[[3.0, 10.0], [4.0, 14.0]],
    )
    chart = choose_chart(result)
    assert (chart.kind, chart.x, chart.y) == ("scatter", "basket_size", ["net_sales"])


def test_more_than_thirty_rows_is_a_table_whatever_its_shape():
    result = QueryResult(
        columns=["sku_id", "net_sales"],
        rows=[[f"sku-{i}", float(i)] for i in range(31)],
    )
    assert choose_chart(result).kind == "table"


def test_thirty_rows_is_still_a_chart():
    result = QueryResult(
        columns=["sku_id", "net_sales"],
        rows=[[f"sku-{i}", float(i)] for i in range(30)],
    )
    assert choose_chart(result).kind == "bar"


def test_anything_else_is_a_table():
    result = QueryResult(
        columns=["department_name", "state_code", "net_sales"],
        rows=[["Dairy & Eggs", "WA", 10.0]],
    )
    assert choose_chart(result).kind == "table"


def test_an_empty_result_is_a_table_and_not_a_crash():
    assert choose_chart(QueryResult(columns=["net_sales"], rows=[])).kind == "table"
    assert choose_chart(QueryResult()).kind == "table"


def test_trend_prefers_a_line_over_a_bar():
    # A trend question produced this axis, so the categorical is ordered even
    # though its name never says so.
    result = QueryResult(
        columns=["fy_label", "net_sales"],
        rows=[["FY2024", 10.0], ["FY2025", 12.0]],
    )
    assert choose_chart(result, intent="aggregate").kind == "bar"
    assert choose_chart(result, intent="trend").kind == "line"


def test_compare_prefers_a_grouped_bar_over_a_line():
    result = QueryResult(
        columns=["week_key", "our_sales", "market_sales"],
        rows=[[1, 10.0, 100.0], [2, 11.0, 105.0]],
    )
    assert choose_chart(result, intent="trend").kind == "line"
    assert choose_chart(result, intent="compare").kind == "grouped_bar"


# ---------------------------------------------------------------------------
# 7.1 Visual Formatter: the markdown table
# ---------------------------------------------------------------------------


def test_render_table_writes_a_header_a_divider_and_every_row():
    lines = render_table(margins()).splitlines()
    assert lines[0].startswith("| department_name |")
    assert set(lines[1]) <= set("| -")
    assert "Bakery" in lines[3]


def test_render_table_caps_the_rows_and_says_how_many_it_hid():
    result = QueryResult(columns=["n"], rows=[[i] for i in range(40)])
    rendered = render_table(result, max_rows=30)
    assert "| 29 |" in rendered
    assert "| 30 |" not in rendered
    assert "first 30 of 40 rows" in rendered


def test_render_table_warns_when_the_executor_truncated_the_result():
    result = QueryResult(columns=["n"], rows=[[1]], truncated=True)
    assert "sample" in render_table(result)


def test_render_table_escapes_html_in_a_cell():
    # The answer renders as markdown, which passes raw HTML through: a product
    # name out of the database is untrusted text at that point.
    result = QueryResult(columns=["name"], rows=[["<script>alert(1)</script>"]])
    rendered = render_table(result)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_table_escapes_a_pipe_so_a_cell_cannot_split_the_row():
    result = QueryResult(columns=["name"], rows=[["a|b"]])
    assert r"a\|b" in render_table(result)


# ---------------------------------------------------------------------------
# 7.2 Insight Narrator
# ---------------------------------------------------------------------------


def scripted_narrative(**kwargs) -> Narrative:
    defaults = {
        "text": "Dairy & Eggs ran a 31.4% gross margin.",
        "value": 31.4,
        "cells": [CellRef(row=0, column="gross_margin_pct")],
        "formula": None,
    }
    defaults.update(kwargs)
    return Narrative(claims=[NarratedClaim(**defaults)])


def test_narrate_returns_claims_addressed_by_row_and_column():
    llm = ScriptedLLM(narration=scripted_narrative())
    claims = narrate(llm, "how did margins do?", margins())
    assert [c.text for c in claims] == ["Dairy & Eggs ran a 31.4% gross margin."]
    assert claims[0].cells == [(0, "gross_margin_pct")]
    assert claims[0].value == 31.4


def test_narrate_shows_the_question_the_rows_and_the_chart():
    llm = ScriptedLLM(narration=scripted_narrative())
    result = QueryResult(
        columns=["department_name", "gross_margin_pct"],
        rows=[["Dairy & Eggs", Decimal("31.4")]],
    )
    narrate(llm, "how did margins do?", result, chart=choose_chart(result))
    text = prompt_text(llm)
    assert "how did margins do?" in text
    assert "Dairy &amp; Eggs" in text  # escaped on the way in, as everywhere
    assert "Chart chosen for this result: bar" in text
    assert "| row |" in text  # cells are addressed by a row number it can see


def test_narrate_passes_the_top_exemplars_reasoning_target():
    llm = ScriptedLLM(narration=scripted_narrative())
    narrate(
        llm,
        "q",
        margins(),
        reasoning_target="The totals repeat once per competitor.",
    )
    assert "The totals repeat once per competitor." in prompt_text(llm)


def test_narrate_tells_the_narrator_when_the_result_was_truncated():
    """A claim about "the total" is a lie when it is the total of a sample."""
    result = margins()
    result.truncated = True
    llm = ScriptedLLM(narration=scripted_narrative())
    narrate(llm, "q", result)
    text = prompt_text(llm)
    assert "WARNING" in text
    assert "sample" in text
    assert "at least 2 rows" in text


def test_narrate_warns_when_it_is_shown_fewer_rows_than_the_query_returned():
    result = QueryResult(columns=["n"], rows=[[i] for i in range(10)])
    llm = ScriptedLLM(narration=scripted_narrative(cells=[CellRef(row=0, column="n")], value=0.0))
    narrate(llm, "q", result, max_rows=3)
    text = prompt_text(llm)
    assert "shown 3 of 10 rows" in text
    assert "| 2 | 2 |" in text  # and it really is shown only three of them
    assert "| 3 | 3 |" not in text


def test_narrate_says_nothing_about_truncation_when_it_sees_everything():
    llm = ScriptedLLM(narration=scripted_narrative())
    narrate(llm, "q", margins())
    assert "WARNING" not in prompt_text(llm)


def test_narrate_treats_a_null_formula_string_as_no_formula():
    # Models write "null" into an optional string field often enough that
    # taking it literally would fail the claim on a formula it did not write.
    llm = ScriptedLLM(narration=scripted_narrative(formula="null"))
    assert narrate(llm, "q", margins())[0].formula is None


# ---------------------------------------------------------------------------
# 7.3 Audit Checker: claims against cells
# ---------------------------------------------------------------------------


def test_a_claim_that_matches_its_cell_survives():
    claim = Claim(
        text="Dairy & Eggs ran a 31.4% gross margin in fiscal month 12.",
        value=31.4,
        cells=[(0, "gross_margin_pct")],
    )
    report = audit([claim], margins(), question="how did margins do?")
    assert report.passed
    assert surviving_claims([claim], report) == [claim]


def test_a_claim_whose_formula_reproduces_its_value_survives():
    claim = Claim(
        text="That is 2.1 points above the department's FY2025 average.",
        value=2.1,
        cells=[(0, "gross_margin_pct"), (0, "fy_avg_pct")],
        formula="cells[0] - cells[1]",
    )
    report = audit([claim], margins())
    assert report.unsupported_claims == []


def test_a_claim_whose_formula_does_not_reproduce_its_value_is_dropped_and_reported():
    claim = Claim(
        text="That is 9.9 points above the average.",
        value=9.9,
        cells=[(0, "gross_margin_pct"), (0, "fy_avg_pct")],
        formula="cells[0] - cells[1]",
    )
    result = margins()
    report = audit([claim], result)
    assert not report.passed
    assert surviving_claims([claim], report) == []
    assert report.unsupported_claims == [claim.text]
    # The report lists the claim; the reason a retry would need is its own
    # call, because an entry decorated with it would stop matching the text.
    assert "2.1" in check_claim(claim, result)  # what the cells actually give


def test_a_claim_that_matches_no_cell_it_cites_is_dropped():
    claim = Claim(text="Margin was 45.0%.", value=45.0, cells=[(0, "gross_margin_pct")])
    assert audit([claim], margins()).unsupported_claims


def test_a_number_with_no_cell_to_check_it_against_is_dropped():
    claim = Claim(text="Margin was 31.4%.", value=31.4)
    assert audit([claim], margins()).unsupported_claims == [claim.text]
    assert "no cell" in check_claim(claim, margins())


def test_a_claim_citing_a_cell_that_is_not_in_the_result_is_dropped():
    claim = Claim(text="Margin was 31.4%.", value=31.4, cells=[(7, "gross_margin_pct")])
    assert audit([claim], margins()).unsupported_claims == [claim.text]
    assert "not in the result" in check_claim(claim, margins())


def test_two_decimals_is_close_enough():
    # The reference queries round for legibility and agents do not: 48.6076
    # against 48.61 is the same number, and the benchmark grades it that way.
    result = QueryResult(columns=["gross_margin_pct"], rows=[[Decimal("48.6076")]])
    claim = Claim(text="The margin was 48.61%.", value=48.61, cells=[(0, "gross_margin_pct")])
    assert audit([claim], result).unsupported_claims == []


def test_a_stray_number_in_the_text_fails_the_claim():
    claim = Claim(
        text="Margin was 31.4%, up 7 points on last year.",
        value=31.4,
        cells=[(0, "gross_margin_pct")],
    )
    report = audit([claim], margins())
    assert surviving_claims([claim], report) == []
    assert "says 7" in check_claim(claim, margins())


def test_a_number_that_appears_in_a_cited_row_is_not_stray():
    """The spec's own example sentence has a 12 in it that is not its value."""
    claim = Claim(
        text="Dairy & Eggs ran a 31.4% gross margin in fiscal month 12.",
        value=31.4,
        cells=[(0, "gross_margin_pct")],
    )
    assert audit([claim], margins()).unsupported_claims == []


def test_a_year_an_ordinal_a_date_and_a_row_count_are_labels_not_claims():
    claim = Claim(
        text="Ranked 1st of the top 2 departments on 2025-01-15, FY2025 margin was 31.4%.",
        value=31.4,
        cells=[(0, "gross_margin_pct")],
    )
    assert audit([claim], margins()).unsupported_claims == []


def test_a_sentence_with_no_number_at_all_needs_no_cell():
    claim = Claim(text="Every department improved on last year.")
    assert audit([claim], margins()).unsupported_claims == []


def test_a_sentence_with_no_value_may_still_not_invent_a_number():
    claim = Claim(text="Roughly 400 stores improved.")
    assert audit([claim], margins()).unsupported_claims


# ---------------------------------------------------------------------------
# 7.3 Audit Checker: the sensitive-column policy
# ---------------------------------------------------------------------------


def salaries() -> QueryResult:
    """This schema has no sensitive column, so the mechanism is tested on one."""
    return QueryResult(
        columns=["employee_name", "salary", "avg_salary"],
        rows=[["Ann Roberts", 90000, 72000], ["Ben Shah", 61000, 72000]],
    )


def test_tagged_sensitive_columns_reads_the_tag_out_of_the_catalog_comment():
    schema = (
        "=== dim_employee ===\n"
        "columns:\n"
        "  employee_name (text, NOT NULL)  -- display name\n"
        "  salary (numeric, NULL)  -- [sensitive] annual base pay\n"
        "  avg_salary (numeric, NULL)  -- department average\n"
    )
    assert tagged_sensitive_columns(schema) == ("salary",)


def test_tagged_sensitive_columns_finds_nothing_in_the_retail_catalog():
    schema = "=== dim_store ===\ncolumns:\n  store_name (text, NOT NULL)  -- store banner\n"
    assert tagged_sensitive_columns(schema) == ()


def test_a_claim_quoting_a_sensitive_column_is_dropped():
    claim = Claim(text="Ann Roberts earns 90000.", value=90000.0, cells=[(0, "salary")])
    report = audit([claim], salaries(), sensitive_columns=["salary"])
    assert surviving_claims([claim], report) == []
    assert "sensitive" in check_claim(claim, salaries(), sensitive_columns=["salary"])


def test_an_aggregate_of_a_sensitive_column_still_gets_through():
    # The tag is on the catalog column; an aggregate arrives under its own
    # alias, which is not a catalog column, so it is not withheld.
    claim = Claim(
        text="The department average is 72000.", value=72000.0, cells=[(0, "avg_salary")]
    )
    report = audit([claim], salaries(), sensitive_columns=["salary"])
    assert report.unsupported_claims == []


def test_a_sensitive_column_is_dropped_from_the_table_too():
    report = audit([], salaries(), sensitive_columns=["salary"])
    assert report.redactions == ["salary"]
    answer = render_answer("who earns what?", salaries(), [], None, report)
    assert "90000" not in answer
    assert "72000" in answer  # the aggregate stays
    assert "Withheld as sensitive" in answer


def test_redact_removes_the_column_and_leaves_the_rest_of_the_row():
    trimmed = redact(salaries(), ["salary"])
    assert trimmed.columns == ["employee_name", "avg_salary"]
    assert trimmed.rows[0] == ["Ann Roberts", 72000]


# ---------------------------------------------------------------------------
# 7.3 Audit Checker: the semantic signals (W3)
# ---------------------------------------------------------------------------


def test_a_percentage_over_a_hundred_is_the_sql_being_wrong_not_the_prose():
    result = QueryResult(columns=["market_share_pct"], rows=[[Decimal("142.7")]])
    report = audit([], result, question="what is our market share?")
    assert report.semantic_issue is not None
    assert "market_share_pct" in report.semantic_issue
    assert not report.passed


def test_a_claim_stating_an_impossible_percentage_raises_a_semantic_issue():
    result = QueryResult(columns=["share"], rows=[[Decimal("142.7")]])
    claim = Claim(text="Our share was 142.7%.", value=142.7, cells=[(0, "share")])
    assert "percentage" in audit([claim], result).semantic_issue


def test_an_empty_result_raises_a_semantic_issue_when_the_question_implies_rows():
    empty = QueryResult(columns=["department_name"], rows=[])
    report = audit([], empty, question="which departments grew?")
    assert "no rows" in report.semantic_issue


def test_an_existence_question_may_legitimately_return_nothing():
    report = audit(
        [],
        QueryResult(columns=["department_name"], rows=[]),
        question="Are there any departments over 50% margin?",
    )
    assert report.semantic_issue is None


def test_a_negative_count_raises_a_semantic_issue():
    result = QueryResult(columns=["basket_count"], rows=[[-4]])
    assert "cannot be negative" in audit([], result, question="how many baskets?").semantic_issue


def test_a_negative_amount_does_not():
    # Returns, markdowns and credits are negative money. Flagging them would
    # spend a retry on a correct query, which is the failure mode W3 is for.
    result = QueryResult(columns=["net_sales_amt"], rows=[[Decimal("-412.50")]])
    assert audit([], result, question="what were net sales?").semantic_issue is None


def test_a_clean_result_raises_nothing_and_passes():
    report = audit([], margins(), question="how did margins do?")
    assert report.passed
    assert report.semantic_issue is None


def test_audit_issue_routes_a_semantic_verdict_into_the_shared_retry_budget():
    result = QueryResult(columns=["market_share_pct"], rows=[[Decimal("142.7")]])
    issue = audit_issue(audit([], result, question="what is our share?"))
    assert issue.source == AUDIT
    assert "market_share_pct" in issue.message


def test_audit_issue_is_none_when_the_rows_look_right():
    assert audit_issue(audit([], margins(), question="q")) is None


# ---------------------------------------------------------------------------
# The formula evaluator: a security boundary
# ---------------------------------------------------------------------------


def test_the_arithmetic_a_narrator_actually_needs_works():
    cells = [10.0, 4.0, 2.0]
    assert evaluate_formula("cells[0] - cells[1]", cells) == 6.0
    assert evaluate_formula("cells[0] / cells[1]", cells) == 2.5
    assert evaluate_formula("(cells[0] - cells[1]) * 100", cells) == 600.0
    assert evaluate_formula("sum(cells)", cells) == 16.0
    assert evaluate_formula("max(cells) - min(cells)", cells) == 8.0
    assert evaluate_formula("round(cells[0] / cells[2], 2)", cells) == 5.0
    assert evaluate_formula("abs(cells[1] - cells[0])", cells) == 6.0


@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os').system('echo pwned')",
        "open('/tmp/nl2sql-pwned', 'w')",
        "cells[0].__class__.__mro__[1].__subclasses__()",
        "().__class__.__bases__",
        "[c for c in cells]",
        "(lambda: 1)()",
        "globals()",
        "9**9**9",
        "cells[0] if cells else 0",
        "'a' * 10",
    ],
)
def test_a_hostile_formula_is_refused_rather_than_executed(hostile):
    with pytest.raises(FormulaError):
        evaluate_formula(hostile, [1.0, 2.0])


def test_a_formula_cannot_reach_a_name_in_any_namespace():
    """There is no namespace to reach: nothing is handed to the interpreter."""
    EXECUTED.clear()
    with pytest.raises(FormulaError):
        evaluate_formula("boom()", [1.0])
    assert EXECUTED == []


def test_a_hostile_formula_touches_nothing_on_the_filesystem(tmp_path):
    target = tmp_path / "pwned.txt"
    hostile = f"__import__('pathlib').Path({str(target)!r}).write_text('x')"
    with pytest.raises(FormulaError):
        evaluate_formula(hostile, [1.0])
    assert not target.exists()


def test_a_hostile_formula_inside_a_claim_only_drops_the_claim(tmp_path):
    target = tmp_path / "pwned.txt"
    claim = Claim(
        text="The gap is 2.1 points.",
        value=2.1,
        cells=[(0, "gross_margin_pct"), (0, "fy_avg_pct")],
        formula=f"__import__('pathlib').Path({str(target)!r}).write_text('x')",
    )
    report = audit([claim], margins())
    assert not target.exists()
    assert "refused" in check_claim(claim, margins())
    assert surviving_claims([claim], report) == []


def test_division_by_zero_is_refused_rather_than_raised_at_the_caller():
    with pytest.raises(FormulaError):
        evaluate_formula("cells[0] / cells[1]", [1.0, 0.0])


def test_a_cell_index_outside_the_citation_is_refused():
    with pytest.raises(FormulaError):
        evaluate_formula("cells[4]", [1.0, 2.0])


def test_a_formula_longer_than_the_cap_is_refused_before_it_is_parsed():
    with pytest.raises(FormulaError):
        evaluate_formula(" + ".join(["cells[0]"] * 60), [1.0])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_the_answer_is_the_surviving_claims_and_then_the_table():
    good = Claim(text="Dairy & Eggs led on 31.4%.", value=31.4, cells=[(0, "gross_margin_pct")])
    result = margins()
    report = audit([good], result, question="how did margins do?")
    answer = render_answer("how did margins do?", result, [good], choose_chart(result), report)
    assert answer.index("Dairy &amp; Eggs led on 31.4%.") < answer.index("| department_name |")


def test_a_dropped_claim_never_reaches_the_answer():
    good = Claim(text="Dairy & Eggs led on 31.4%.", value=31.4, cells=[(0, "gross_margin_pct")])
    bad = Claim(text="Sales grew 12000 baskets.", value=12000.0, cells=[(0, "fy_avg_pct")])
    result = margins()
    report = audit([good, bad], result)
    answer = render_answer("q", result, [good, bad], choose_chart(result), report)
    assert "12000" not in answer
    assert "led on 31.4%" in answer
    assert "dropped" in answer


def test_the_answer_escapes_html_from_the_cells_and_from_the_question():
    result = QueryResult(columns=["name", "n"], rows=[["<b onclick=x>Dairy</b>", 1]])
    answer = render_answer("<script>alert(1)</script>", result, [], choose_chart(result))
    assert "<b onclick" not in answer
    assert "<script>" not in answer


def test_the_answer_escapes_html_a_model_put_in_a_claim():
    claim = Claim(
        text="<b onclick=steal()>Dairy</b> ran a 31.4% margin.",
        value=31.4,
        cells=[(0, "gross_margin_pct")],
    )
    result = margins()
    report = audit([claim], result)
    answer = render_answer("q", result, [claim], choose_chart(result), report)
    assert "<b onclick" not in answer
    assert "&lt;b onclick" in answer


def test_a_scalar_answer_is_a_sentence_and_not_a_one_cell_table():
    result = QueryResult(columns=["net_sales"], rows=[[Decimal("1234.56")]])
    answer = render_answer("What were net sales?", result, [], choose_chart(result))
    assert "1234.56" in answer
    assert "What were net sales:" in answer
    assert "| --- |" not in answer


def test_the_answer_flags_a_semantic_issue_rather_than_quietly_reporting_it():
    result = QueryResult(columns=["market_share_pct", "week_key"], rows=[[Decimal("142.7"), 1]])
    report = audit([], result, question="what is our share?")
    answer = render_answer("what is our share?", result, [], choose_chart(result), report)
    assert "may not answer the question" in answer


def test_render_answer_works_with_no_chart_and_no_audit_report():
    assert "| department_name |" in render_answer("q", margins(), [])


# ---------------------------------------------------------------------------
# A number echoed from the question is not an invented number
# ---------------------------------------------------------------------------


def test_a_qualifier_from_the_question_does_not_fail_the_claim():
    """"Gross margin in fiscal month 12 of FY2025" filters on 12; it does not
    select it, so 12 is in no cell of a one-column result. The stray-number
    rule caught it anyway, on nearly every question, costing a rewrite each
    time. The reader wrote that number, so they can check it.
    """
    from decimal import Decimal

    result = QueryResult(columns=["gross_margin_pct"], rows=[[Decimal("34.20")]])
    claim = Claim(
        text="Gross margin for Dairy & Eggs in fiscal month 12 of FY2025 was 34.20%.",
        value=34.2,
        cells=[(0, "gross_margin_pct")],
    )
    question = "What was the gross margin for Dairy & Eggs in fiscal month 12 of FY2025?"
    assert check_claim(claim, result, question=question) is None


def test_a_number_in_neither_the_rows_nor_the_question_still_fails():
    """The exemption must not become a hole: the rule exists for figures the
    reader has no way to check, and a comparison to last year is one.
    """
    from decimal import Decimal

    result = QueryResult(columns=["gross_margin_pct"], rows=[[Decimal("34.20")]])
    claim = Claim(
        text="Margin was 34.20%, up 9 points on last year.",
        value=34.2,
        cells=[(0, "gross_margin_pct")],
    )
    reason = check_claim(claim, result, question="What was the gross margin?")
    assert reason is not None
    assert "9" in reason


def test_the_audit_passes_the_question_through_to_every_claim():
    from decimal import Decimal

    result = QueryResult(columns=["pct"], rows=[[Decimal("34.20")]])
    claims = [Claim(text="In fiscal month 12 it was 34.20%.", value=34.2, cells=[(0, "pct")])]
    assert audit(claims, result, question="gross margin in fiscal month 12?").passed
    assert not audit(claims, result, question="gross margin?").passed


# ---------------------------------------------------------------------------
# The safe formula evaluator, refusal by refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("formula", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("cells[0] +", "not a valid expression"),
        ("-" * 80 + "cells[0]", "too complex"),
        ("cells[0] + " + " + ".join(["1"] * 200), "longer than"),
        ("x[0]", "only cells"),
        ("cells[a]", "literal integer"),
        ("round(cells[0], ndigits=2)", "keyword arguments"),
        ("min()", "could not be applied"),
    ],
)
def test_a_formula_the_checker_cannot_trust_is_refused_by_name(formula: str, reason: str):
    """Each refusal says which rule it broke, because the message goes into
    the retry prompt and "invalid" teaches the narrator nothing.
    """
    with pytest.raises(FormulaError, match=reason):
        evaluate_formula(formula, [10.0, 5.0])


def test_a_negated_cell_is_evaluated():
    """Unary minus is how a claim says a gap runs the other way."""
    assert evaluate_formula("-cells[0]", [4.0]) == -4.0
    assert evaluate_formula("+cells[0]", [4.0]) == 4.0


def test_addition_and_the_named_functions_all_work():
    assert evaluate_formula("cells[0] + cells[1]", [1.5, 2.5]) == 4.0
    assert evaluate_formula("min(cells)", [3.0, 1.0]) == 1.0
    assert evaluate_formula("max(cells)", [3.0, 1.0]) == 3.0


# ---------------------------------------------------------------------------
# Rendering the pieces that have nothing to render
# ---------------------------------------------------------------------------


def test_a_null_cell_renders_as_null_rather_than_none():
    """`None` in a markdown table reads as a Python object escaping into the
    answer.
    """
    table = render_table(QueryResult(columns=["dept", "total"], rows=[["Produce", None]]))
    assert "NULL" in table
    assert "None" not in table


def test_an_empty_result_renders_no_table_at_all():
    assert render_table(QueryResult()) == ""


def test_a_grouped_chart_names_its_series_when_it_has_one():
    spec = ChartSpec(kind="grouped_bar", x="dept", y=["fy24", "fy25"], series="year")
    assert "series = year" in describe_chart(spec)


def test_a_narration_the_model_declined_to_write_is_an_empty_claim_list():
    """A model that returns nothing is not an error; the answer falls back
    to the table, which is what v3 always showed.
    """

    class _Silent:
        def with_structured_output(self, schema):
            class _B:
                def invoke(self, messages):
                    return None

            return _B()

    assert narrate(_Silent(), "q", QueryResult(columns=["n"], rows=[[1]])) == []


# ---------------------------------------------------------------------------
# Numbers that look like numbers but are not
# ---------------------------------------------------------------------------


def test_a_claim_computing_over_a_cell_that_is_not_a_number_is_dropped():
    result = QueryResult(columns=["dept", "total"], rows=[["Produce", 10.0]])
    claim = Claim(
        text="The difference is 5.",
        value=5.0,
        cells=[(0, "dept"), (0, "total")],
        formula="cells[1] - cells[0]",
    )
    reason = check_claim(claim, result)
    assert reason is not None
    assert "not a number" in reason


def test_a_date_in_a_cited_row_backs_a_date_in_the_sentence():
    """A row whose cell is a date legitimises the date in the prose without
    the narrator having to cite it as a value.
    """
    result = QueryResult(columns=["week", "total"], rows=[["2025-03-14", 10.0]])
    claim = Claim(text="In the week of 2025-03-14 the total was 10.", value=10.0,
                  cells=[(0, "total")])
    assert check_claim(claim, result) is None


def test_a_cell_that_cannot_be_read_as_a_number_is_simply_not_backing():
    result = QueryResult(columns=["note", "total"], rows=[["n/a", 10.0]])
    claim = Claim(text="The total was 10.", value=10.0, cells=[(0, "total")])
    assert check_claim(claim, result) is None


def test_a_real_formula_is_kept_while_the_words_models_use_for_none_are_not():
    """The field is optional, and a model asked for an optional string often
    writes "none" rather than leaving it out. Treating that as a formula
    would drop every claim that has no arithmetic in it.
    """
    from nl2sql_agent.present import _clean_formula

    for spelling in ("null", "None", "n/a", "NA", "", "   "):
        assert _clean_formula(spelling) is None, spelling
    assert _clean_formula(None) is None
    assert _clean_formula(" cells[0] * 2 ") == "cells[0] * 2"

    # And the cleaning really is applied to what the narrator returns.
    result = QueryResult(columns=["n"], rows=[[10.0]])
    llm = ScriptedLLM(
        narration=Narrative(
            claims=[NarratedClaim(
                text="The count is 10.", value=10.0,
                cells=[CellRef(row=0, column="n")], formula="none",
            )]
        )
    )
    [claim] = narrate(llm, "how many?", result)
    assert claim.formula is None
    assert check_claim(claim, result) is None


def test_a_refusal_inside_a_function_argument_is_not_reported_as_an_arity_error():
    """`min(cells[99])` fails on the index, and saying "min() could not be
    applied" would send the narrator looking at the wrong thing.
    """
    with pytest.raises(FormulaError, match="outside the 1 cited cells"):
        evaluate_formula("min(cells[99])", [1.0])


def test_a_date_cell_backs_the_numbers_inside_it():
    """A row whose cell is a real date, not a string, still legitimises the
    year and month the sentence repeats from it.
    """
    from datetime import date

    result = QueryResult(columns=["day", "total"], rows=[[date(2025, 3, 14), 10.0]])
    claim = Claim(text="On 2025-03-14 the total was 10.", value=10.0, cells=[(0, "total")])
    assert check_claim(claim, result) is None


def test_an_empty_question_is_treated_as_one_that_implied_rows():
    """The semantic check needs a default, and "no rows" is more often a bug
    than an answer.
    """
    empty = QueryResult(columns=["n"], rows=[])
    assert audit([], empty, question="").semantic_issue
    assert audit([], empty, question="are there any stores in Alabama?").semantic_issue is None


# ---------------------------------------------------------------------------
# The two escapes in the stray-number rule
# ---------------------------------------------------------------------------


def test_a_year_in_the_sentence_is_not_an_invented_number():
    """A year is a period name, not a figure the reader has to check. It is
    almost never a cell of the result -- FY2025 is a filter, not a projection
    -- so without this the audit would reject the natural phrasing of nearly
    every answer this database is asked for.
    """
    result = QueryResult(columns=["net_sales"], rows=[[719279.97]])
    claim = Claim(
        text="Net sales in fiscal year 2025 were 719279.97.",
        value=719279.97,
        cells=[(0, "net_sales")],
    )
    assert check_claim(claim, result) is None


def test_a_number_outside_the_year_range_is_still_checked():
    """The escape is a range, not "any four-digit number": 4321 buys nothing
    and would let a real invention through.
    """
    result = QueryResult(columns=["net_sales"], rows=[[719279.97]])
    claim = Claim(
        text="Net sales were 719279.97, up from 4321 the year before.",
        value=719279.97,
        cells=[(0, "net_sales")],
    )
    assert "4321" in (check_claim(claim, result) or "")


def test_a_cited_row_that_is_not_in_the_result_backs_no_numbers():
    """`_backing_numbers` is reached with cells a language model chose, and
    it is part of the audit -- the one component whose whole job is to fail
    safely. `check_claim` rejects an out-of-range row before this runs, so
    the guard is what keeps that ordering from being load-bearing.
    """
    from nl2sql_agent.present import _backing_numbers

    result = QueryResult(columns=["n"], rows=[[42]])
    claim = Claim(text="The count is 42.", value=None, cells=[(0, "n"), (7, "n")])
    assert _backing_numbers(claim, result) == [42.0]


def test_check_claim_rejects_the_out_of_range_row_before_it_gets_that_far():
    """Which is the message the narrator's retry is given, and the reason the
    guard above never fires in a real run.
    """
    result = QueryResult(columns=["n"], rows=[[42]])
    claim = Claim(text="The count is 42.", cells=[(7, "n")])
    assert "not in the result" in (check_claim(claim, result) or "")


# ---------------------------------------------------------------------------
# arch5: every assumption is stated (sections 7.2 and 7.3 rule 5)
# ---------------------------------------------------------------------------

FY_DEFAULT = (
    "FY2025 (2024-04-01 to 2025-03-31), the latest complete fiscal year, "
    "since the question did not name a period"
)


def top_stores() -> QueryResult:
    return QueryResult(
        columns=["store_id", "store_name", "net_sales"],
        rows=[["S-01", "Midtown", Decimal("812345.10")], ["S-02", "Uptown", Decimal("790001.00")]],
    )


def test_the_narrator_is_told_each_assumption_and_to_state_it():
    llm = ScriptedLLM(narration=scripted_narrative())
    narrate(llm, "top ten stores", top_stores(), assumptions=[FY_DEFAULT, "  "])
    text = prompt_text(llm)
    assert "State each one in plain words" in text
    assert f"  - {FY_DEFAULT}" in text


def test_no_assumptions_means_no_block():
    assert assumptions_block([]) == ""
    assert assumptions_block(["", "   "]) == ""
    llm = ScriptedLLM(narration=scripted_narrative())
    narrate(llm, "q", margins())
    assert "State each one" not in prompt_text(llm)


@pytest.mark.parametrize(
    "sentence",
    [
        "These figures cover FY2025, the latest complete fiscal year.",
        "Totals are for FY 2025.",
        "Sales are for fiscal year 2025, the last full year of data.",
        "All of FY-2025.",
    ],
)
def test_a_fiscal_year_assumption_is_stated_however_the_year_is_written(sentence):
    assert states_assumption(FY_DEFAULT, sentence)


@pytest.mark.parametrize(
    "sentence",
    ["Sales for 2025 were strong.", "These figures cover FY2024.", ""],
)
def test_a_year_that_is_not_named_as_the_fiscal_year_does_not_count(sentence):
    assert not states_assumption(FY_DEFAULT, sentence)


def test_any_other_assumption_is_matched_on_its_wording():
    assert states_assumption("net sales, not gross", "Ranked by Net  Sales, not gross.")
    assert not states_assumption("net sales, not gross", "Ranked by net sales.")
    assert states_assumption("", "anything")


def test_missing_assumptions_reads_the_claims_together():
    claims = [Claim(text="Midtown led."), Claim(text="These cover FY2025.")]
    assert missing_assumptions([FY_DEFAULT, ""], claims) == []
    assert missing_assumptions([FY_DEFAULT], claims[:1]) == [FY_DEFAULT]


def test_the_audit_fails_a_narrative_that_leaves_an_assumption_out():
    result = top_stores()
    claims = [Claim(text="Midtown led with 812345.10.", value=812345.10, cells=[(0, "net_sales")])]
    report = audit(claims, result, question="top ten stores", assumptions=[FY_DEFAULT])
    assert report.missing_assumptions == [FY_DEFAULT]
    assert not report.passed
    assert report.unsupported_claims == []


def test_an_assumption_said_only_by_a_dropped_claim_is_still_missing():
    result = top_stores()
    claims = [Claim(text="In FY2025 Midtown sold 999.", value=999.0, cells=[(0, "net_sales")])]
    report = audit(claims, result, assumptions=[FY_DEFAULT])
    assert report.unsupported_claims and report.missing_assumptions == [FY_DEFAULT]


def test_the_numbers_inside_an_assumption_are_not_invented_numbers():
    """Rule 2: an assumption's numbers are exempt, as the question's are --
    including a date's day and month, however the narrator spells the date."""
    claim = Claim(text="These cover FY2025, April 1, 2024 to March 31, 2025.")
    result = top_stores()
    assert check_claim(claim, result) is not None
    assert check_claim(claim, result, assumptions=[FY_DEFAULT]) is None
    report = audit([claim], result, assumptions=[FY_DEFAULT])
    assert report.passed


def test_the_answer_states_an_assumption_the_narrative_did_not():
    result = top_stores()
    answer = render_answer("top ten stores", result, [Claim(text="Midtown led.")],
                           assumptions=[FY_DEFAULT])
    assert "*Assumed: FY2025 (2024-04-01 to 2025-03-31), the latest complete fiscal year" in answer

    stated = render_answer("top ten stores", result, [Claim(text="These cover FY2025.")],
                           assumptions=[FY_DEFAULT])
    assert "Assumed:" not in stated


def test_the_answer_names_a_gap_the_reviewer_could_not_close():
    report = CompletenessReport(
        passed=False,
        accepted_gaps=[MissingColumn(column="dim_product.brand_name", why="x", rule="reflection")],
    )
    answer = render_answer("top 10 SKUs", top_stores(), [], completeness=report)
    assert "could not be completed with dim_product.brand_name in the attempts allowed" in answer
    assert "could not be completed" not in render_answer(
        "q", top_stores(), [], audit_report=AuditReport(), completeness=CompletenessReport()
    )
