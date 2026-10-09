"""Fusion (`fuse.py`; arch7 section 22.8): the columns joined onto the chosen
rows, the claims the group's other runs add, the dissent, and the agreement
line every chosen answer opens with. The count in the line is the runs made,
not the rewordings written; what the Judge did is said beside it."""

from __future__ import annotations

import pytest
from nl2sql_agent.contract import Label, LabelMap
from nl2sql_agent.ensemble_state import (
    Agreement,
    Candidate,
    DeclinedColumn,
    Dissent,
    Group,
    GroupVerdict,
    JoinedColumn,
    Judgement,
)
from nl2sql_agent.fuse import agreement_line, dissent, fuse_claims, fuse_columns
from nl2sql_agent.state import AuditReport, Claim, QueryResult


@pytest.mark.parametrize(("agreement", "line"), [
    (Agreement(4, 4, 4, "unanimous"), "Agreed by 4 of 4 independent runs of the question, each worded differently."),
    (Agreement(3, 3, 4, "unanimous"),
     "Agreed by 3 of 4 independent runs of the question, each worded differently; 1 could not answer."),
    (Agreement(4, 3, 4, "majority"), "3 of 4 runs agreed; 1 answered differently."),
    (Agreement(3, 2, 4, "majority"), "2 of 4 runs agreed; 1 answered differently; 1 could not answer."),
    (Agreement(4, 2, 4, "contested"),
     "The runs disagreed and no answer had a majority; this is the largest group's (2 of 4 runs)."),
    (Agreement(1, 1, 1, "single"), "Asked 1 way; one run answered."),
    (Agreement(1, 1, 4, "single"), "Asked 4 ways; only one run answered."),
    (Agreement(0, 0, 4, "none"), ""),
])
def test_every_agreement_has_its_sentence(agreement, line):
    assert agreement_line(agreement) == line


def _groups(*members) -> list[Group]:
    return [Group(index=i, members=list(m), representative=m[0], signature="") for i, m in enumerate(members)]


@pytest.mark.parametrize(("agreement", "line"), [
    (Agreement(3, 3, 4, "unanimous", set_aside=1),
     "Agreed by 3 of 4 independent runs of the question, each worded differently; the Judge set aside the answer of "
     "1 other."),
    (Agreement(2, 2, 4, "unanimous", set_aside=1),
     "Agreed by 2 of 4 independent runs of the question, each worded differently; 1 could not answer; the Judge set "
     "aside the answer of 1 other."),
    (Agreement(3, 2, 5, "majority", set_aside=2),
     "2 of 5 runs agreed; 1 answered differently; the Judge set aside the answer of 2 others."),
    (Agreement(2, 1, 4, "contested", set_aside=2),
     "The runs disagreed and no answer had a majority; this is the largest group's (1 of 4 runs); the Judge set aside "
     "the answer of 2 others."),
    (Agreement(1, 1, 4, "single", set_aside=3), "Asked 4 ways; one run's answer stands; the Judge set aside the answer "
     "of 3 others."),
])
def test_the_answers_the_judge_set_aside_are_counted_apart_from_the_runs_that_failed(agreement, line):
    assert agreement_line(agreement, Judgement(set_aside=list(range(agreement.set_aside)))) == line


def test_when_the_judge_overrules_the_runs_the_line_says_what_it_set_aside_and_why():
    judgement = Judgement(
        verdicts=[GroupVerdict(0, False, "It joins daily sales to monthly costs on the date."), GroupVerdict(1, True)],
        set_aside=[1, 2, 3], overruled=True, instead_of=1,
    )
    line = agreement_line(Agreement(1, 1, 4, "judged", set_aside=3), judgement, _groups([1, 2, 3], [0]))
    assert line == (
        "The Judge set aside the answer 3 of 4 runs gave -- It joins daily sales to monthly costs on the date -- "
        "and accepted this one, which 1 gave."
    )


def test_when_the_judge_accepts_none_the_runs_own_choice_is_delivered_with_its_objection():
    judgement = Judgement(verdicts=[GroupVerdict(0, False, "It filters to two channels."), GroupVerdict(1, False)],
                          set_aside=[0, 1, 2, 3])
    line = agreement_line(Agreement(4, 3, 4, "contested"), judgement, _groups([0, 1, 3], [2]))
    assert line == (
        "The Judge accepted none of the answers -- It filters to two channels -- "
        "so this is the runs' own choice, which 3 of 4 gave."
    )
    # A verdict with no reason still makes a sentence.
    judgement = Judgement(verdicts=[GroupVerdict(0, False)], set_aside=[0])
    assert "-- it gave no reason --" in agreement_line(Agreement(1, 1, 1, "contested"), judgement, _groups([0]))


def test_a_judge_that_could_not_be_asked_is_said_so():
    failed = Judgement(error="ConnectionError: refused")
    assert agreement_line(Agreement(4, 4, 4, "unanimous"), failed) == (
        "Agreed by 4 of 4 independent runs of the question, each worded differently. The Judge could not be asked."
    )
    assert agreement_line(Agreement(1, 1, 1, "single"), failed).endswith("one run answered. The Judge could not be asked.")


# ---------------------------------------------------------------------------
# Columns: the join rule, step by step
# ---------------------------------------------------------------------------

LABELS = LabelMap([Label("dim_store", "store_key", "store_name"), Label("dim_product", "product_key", "product_name")])
DIMENSIONS = {
    "store_key": "dim_store", "store_name": "dim_store", "region_name": "dim_store",
    "product_key": "dim_product", "product_name": "dim_product", "brand_name": "dim_product",
}
STORES_SQL = (
    "SELECT s.store_key, s.store_name, SUM(f.net_sales_amt) AS sales FROM fact_pos_retail_sales f "
    "JOIN dim_store s ON s.store_key = f.store_key GROUP BY 1, 2"
)


def run(index, columns, rows, *, sql=STORES_SQL, claims=(), audit=None, assumptions=()) -> Candidate:
    return Candidate(
        index=index, wording=f"wording {index}", origin="original" if index == 0 else "paraphrase", wave=1,
        state={"sql": sql, "result": QueryResult(columns=list(columns), rows=[list(row) for row in rows]),
               "claims": list(claims), "audit": audit or AuditReport(), "assumptions": list(assumptions)},
        outcome="answered",
    )


def chosen() -> Candidate:
    return run(0, ["store_key", "store_name", "sales"], [[1, "North", 10.0], [2, "South", 20.0]])


def test_a_column_another_run_carried_is_joined_on_the_entitys_key_and_its_run_named():
    representative = chosen()
    member = run(2, ["store_key", "region_name", "sales"], [[2, "Coast", 20.0], [1, "Hills", 10.0]])
    result, joined, declined = fuse_columns(representative, [member], LABELS, DIMENSIONS, enabled=True)

    assert result.columns == ["store_key", "store_name", "sales", "region_name"]
    assert result.rows == [[1, "North", 10.0, "Hills"], [2, "South", 20.0, "Coast"]], "by the key, not the row"
    assert joined == [JoinedColumn(column="region_name", from_candidate=2, key="store_key", table="dim_store")]
    assert declined == []


def test_the_representatives_own_record_is_never_widened():
    """S4: candidate k's record shows what candidate k's query returned."""
    representative = chosen()
    member = run(1, ["store_key", "region_name"], [[1, "Hills"], [2, "Coast"]])
    result, _, _ = fuse_columns(representative, [member], LABELS, DIMENSIONS, enabled=True)

    assert result is not representative.state["result"]
    assert representative.state["result"].columns == ["store_key", "store_name", "sales"]
    assert representative.state["result"].rows == [[1, "North", 10.0], [2, "South", 20.0]]


def test_a_figure_under_another_name_is_no_attribute_and_is_passed_over_unsaid():
    member = run(1, ["store_key", "store_name", "net_sales"], [[1, "North", 10.0], [2, "South", 20.0]])
    result, joined, declined = fuse_columns(chosen(), [member], LABELS, DIMENSIONS, enabled=True)
    assert (result.columns, joined, declined) == (["store_key", "store_name", "sales"], [], [])


@pytest.mark.parametrize(("representative", "member", "why"), [
    (chosen(), run(1, ["store_key", "brand_name"], [[1, "Acme"], [2, "Best"]]),
     "dim_product is not a dimension the chosen rows identify"),
    (run(0, ["store_name", "sales"], [["North", 10.0]]), run(1, ["store_name", "region_name"], [["North", "Hills"]]),
     "no key of dim_store is in both runs' rows"),
    (chosen(), run(1, ["store_key", "region_name"], [[1, "Hills"], [1, "Vale"], [2, "Coast"]]),
     "store_key 1 repeats in run 1"),
    (chosen(), run(1, ["store_key", "region_name"], [[1, "Hills"]]), "row 2 has no match in run 1"),
])
def test_each_step_of_the_join_rule_declines_on_its_own_and_says_which(representative, member, why):
    result, joined, declined = fuse_columns(representative, [member], LABELS, DIMENSIONS, enabled=True)
    assert result.columns == representative.state["result"].columns and joined == []
    assert declined == [DeclinedColumn(column=member.state["result"].columns[-1], from_candidate=1, why=why)]


def test_with_column_fusion_off_the_chosen_rows_are_delivered_as_they_are():
    representative = chosen()
    member = run(1, ["store_key", "region_name"], [[1, "Hills"], [2, "Coast"]])
    result, joined, declined = fuse_columns(representative, [member], LABELS, DIMENSIONS, enabled=False)
    assert (result, joined, declined) == (representative.state["result"], [], [])


def test_a_column_one_run_could_not_join_and_the_next_could_is_joined_and_not_declined():
    repeats = run(1, ["store_key", "region_name"], [[1, "Hills"], [1, "Vale"], [2, "Coast"]])
    clean = run(3, ["store_key", "region_name"], [[1, "Hills"], [2, "Coast"]])
    _, joined, declined = fuse_columns(chosen(), [repeats, clean], LABELS, DIMENSIONS, enabled=True)
    assert [(c.column, c.from_candidate) for c in joined] == [("region_name", 3)] and declined == []


def test_a_column_is_joined_once_from_the_first_run_in_rank_order():
    first = run(2, ["store_key", "region_name"], [[1, "Hills"], [2, "Coast"]])
    second = run(1, ["store_key", "region_name"], [[1, "Other"], [2, "Other"]])
    result, joined, _ = fuse_columns(chosen(), [first, second], LABELS, DIMENSIONS, enabled=True)
    assert [c.from_candidate for c in joined] == [2] and result.rows[0][-1] == "Hills"


def test_runs_that_both_could_not_join_a_column_are_both_named():
    one = run(1, ["store_key", "region_name"], [[1, "Hills"]])
    other = run(2, ["store_key", "region_name"], [[2, "Coast"]])
    _, _, declined = fuse_columns(chosen(), [one, other], LABELS, DIMENSIONS, enabled=True)
    assert [(c.from_candidate, c.why) for c in declined] == [
        (1, "row 2 has no match in run 1"), (2, "row 1 has no match in run 2"),
    ]


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

ROWS = QueryResult(columns=["store_key", "store_name", "sales", "region_name"],
                   rows=[[1, "North", 10.0, "Hills"], [2, "South", 20.0, "Coast"]])
NORTH = Claim(text="North sold 10.", value=10.0, cells=[(0, "sales")])


def fused(member_claims, *, own=(NORTH,), audit=None, cap=8, assumptions=()):
    representative = run(0, ROWS.columns[:3], [row[:3] for row in ROWS.rows], claims=own, audit=audit)
    members = [run(i, ROWS.columns[:3], [row[:3] for row in ROWS.rows], claims=claims)
               for i, claims in enumerate(member_claims, 1)]
    return fuse_claims(representative, members, ROWS, question="sales by store", assumptions=assumptions, cap=cap)


def test_another_runs_claim_the_delivered_rows_reproduce_is_added_and_one_they_do_not_is_dropped():
    wrong = Claim(text="South sold 99.", value=99.0, cells=[(1, "sales")])
    region = Claim(text="South is in Coast.", cells=[(1, "region_name")])
    claims, report, added, dropped = fused([[wrong], [region]])

    assert [c.text for c in claims] == ["North sold 10.", "South is in Coast."]
    assert (added, dropped) == (1, 1), "the joined column's claim stands; the 99 is in no cell"
    assert report.passed and report.unsupported_claims == []


def test_a_claim_citing_a_column_the_delivered_rows_lack_is_dropped():
    brand = Claim(text="South sells Acme.", cells=[(1, "brand_name")])
    _, _, added, dropped = fused([[brand]])
    assert (added, dropped) == (0, 1)


def test_a_claim_about_rows_already_spoken_of_is_a_repeat_however_it_is_worded():
    """Measured on B10 and B03: four narrators of one result each restate
    its rows, citing different columns of them, and a narrator that said
    two rows in one sentence was restated row by row. A claim every row of
    which the narrative already speaks of adds nothing."""
    same_row = Claim(text="North (store 1) had sales of 10.", cells=[(0, "store_name"), (0, "store_key"), (0, "sales")])
    both_rows = Claim(text="South sold 10 more than North.", value=10.0, cells=[(1, "sales"), (0, "sales")],
                      formula="cells[0] - cells[1]")
    south = Claim(text="South sold 20.", value=20.0, cells=[(1, "sales")])
    claims, _, added, dropped = fused([[same_row, both_rows, south]])
    assert ([c.text for c in claims], added, dropped) == (["North sold 10.", "South sold 10 more than North."], 1, 0)


def test_a_claim_that_cites_no_cell_is_never_added():
    """Measured on B03: a narrator's fragment, "Corner Fresh Grocers has",
    cited nothing and so failed no check."""
    _, _, added, dropped = fused([[Claim(text="Corner Fresh Grocers has")]])
    assert (added, dropped) == (0, 1)


def test_the_fused_narrative_stops_at_its_cap():
    more = [Claim(text=f"Store {i} sold {v}.", value=v, cells=[(i - 1, "sales")]) for i, v in ((2, 20.0),)]
    region = Claim(text="North is in Hills.", cells=[(0, "region_name")])
    claims, _, added, _ = fused([more, [region]], cap=2)
    assert len(claims) == 2 and added == 1


def test_the_representatives_own_dropped_claims_stay_dropped_and_said():
    bad = Claim(text="North sold 12.", value=12.0, cells=[(0, "sales")])
    own_audit = AuditReport(passed=False, unsupported_claims=[bad.text], drop_reasons=[f"{bad.text} -- states 12"])
    claims, report, _, _ = fused([[]], own=(NORTH, bad), audit=own_audit)

    assert [c.text for c in claims] == ["North sold 10.", "North sold 12."], "as narrated"
    assert report.unsupported_claims == [bad.text] and report.drop_reasons == [f"{bad.text} -- states 12"]
    assert report.passed is False


def test_an_assumption_another_runs_claim_states_is_no_longer_missing():
    stated = Claim(text="In fiscal year 2025, South sold 20.", value=20.0, cells=[(1, "sales")])
    assumptions = ["fiscal year 2025"]
    _, before, _, _ = fused([[]], assumptions=assumptions)
    _, after, _, _ = fused([[stated]], assumptions=assumptions)
    assert before.missing_assumptions == assumptions and after.missing_assumptions == []


# ---------------------------------------------------------------------------
# Dissent
# ---------------------------------------------------------------------------

FISCAL = (
    "SELECT SUM(f.net_sales_amt) FROM fact_pos_retail_sales f JOIN dim_date d ON d.date_key = f.sales_date_key "
    "WHERE d.fiscal_year = 2025"
)
CALENDAR = (
    "SELECT SUM(f.net_sales_amt) FROM fact_pos_retail_sales f JOIN dim_date dd ON dd.date_key = f.sales_date_key "
    "WHERE dd.calendar_year = 2025"
)
STORES = (
    "SELECT SUM(f.net_sales_amt) FROM fact_pos_retail_sales f JOIN dim_date d ON d.date_key = f.sales_date_key "
    "JOIN dim_store s ON s.store_key = f.store_key WHERE fiscal_year = 2025 AND s.region_name = 'West'"
)


def _losing(sql: str) -> list[Dissent]:
    picked = run(0, ["n"], [[1]], sql=FISCAL)
    other = run(2, ["n"], [[2]], sql=sql)
    return dissent(picked, [Group(index=1, members=[2, 3], representative=2, signature="2")], [picked, other])


@pytest.mark.parametrize(("sql", "differs"), [
    (CALENDAR, "run 2's query filters on dim_date.calendar_year; the chosen one filters on dim_date.fiscal_year"),
    (STORES, "run 2's query uses dim_store and filters on dim_store.region_name"),
    ("SELECT SUM(net_sales_amt) FROM fact_pos_retail_sales",
     "the chosen one uses dim_date and filters on dim_date.fiscal_year"),
    (FISCAL, ""),
])
def test_each_losing_answer_is_named_with_how_its_query_differs(sql, differs):
    [lost] = _losing(sql)
    assert (lost.group, lost.members, lost.signature, lost.differs) == (1, [2, 3], "2", differs)
