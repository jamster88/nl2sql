"""memory.py: a follow-up's restatement held to the words typed (arch8 section 23.3).

`resolve` is a pure function of two strings, so each check is tested on a
pair it must keep and a pair it must discard -- arch8's own examples first
-- and the order of the checks on a restatement that fails two. The
hand-written follow-up set holds it to fifteen standalones a person wrote
(`tests/benchmarks/test_followups.py`).
"""

from __future__ import annotations

import pytest
from nl2sql_agent import memory
from nl2sql_agent.memory import NOT_RESTATED, MemoryNote, Turn, resolve
from nl2sql_agent.state import to_jsonable

# ---------------------------------------------------------------------------
# C4, the empty half: not restated is no failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("standalone", ["", "   ", "And by store.", "and BY store", "And by  store?!"])
def test_the_question_itself_again_is_not_restated(standalone):
    """The model returned the question with a full stop, or nothing: the
    question stands alone, and nothing is resolved."""
    assert resolve("And by store?", standalone) == MemoryNote(reason=NOT_RESTATED)


# ---------------------------------------------------------------------------
# C1 numbers
# ---------------------------------------------------------------------------


def test_a_number_the_restatement_dropped_discards_it():
    assert resolve("the top 5 of those", "the top banners by net sales").reason == "C1 numbers: 5 missing"


def test_a_number_kept_as_a_word_is_kept():
    note = resolve("just the top five of those", "Which 5 banners had the highest net sales in FY2025?")
    assert note.resolved


def test_a_number_the_turns_supplied_may_be_added():
    """Subset, not equality: the year is the turn's, and a restatement
    that writes it out has added what the turns supply."""
    assert resolve("the top 5 of those", "the top 5 banners by net sales in fiscal year 2025").resolved


# ---------------------------------------------------------------------------
# C2 literals
# ---------------------------------------------------------------------------


def test_a_name_the_restatement_dropped_discards_it():
    assert resolve("and for Dairy & Eggs?", "and for dairy products?").reason == "C2 literals: 'dairy & eggs' missing"


def test_a_name_is_kept_whatever_its_case():
    note = resolve("and for Dairy & Eggs?", "What was the gross margin for the dairy & eggs department in FY2025?")
    assert note.resolved


@pytest.mark.parametrize(
    "asked,standalone,literal",
    [
        ("The same for FY2024?", "What were our total net sales in fiscal 2024?", "fy2024"),
        ("Which vendor paid the most in SCAN_BACK?", "Which vendor paid the most in scan-back allowances?", "scan_back"),
        ('and the "Back to School" ones?', "and the back-to-school promotions?", "back to school"),
    ],
)
def test_a_code_a_mixed_token_and_a_quoted_value_are_literals_too(asked, standalone, literal):
    assert resolve(asked, standalone).reason == f"C2 literals: '{literal}' missing"


@pytest.mark.parametrize(
    "asked,standalone",
    [
        ("The same for FY2024?", "What were our total net sales in fiscal year 2024?"),
        ("What about Q3?", "What was the total markdown discount given in fiscal quarter 3 of fiscal year 2025?"),
    ],
)
def test_a_period_written_short_may_be_written_out_in_place(asked, standalone):
    """Grown (arch8, Phase 5, the risks by phase's R1): the Supervisor
    calibrated with turns shown wrote these for B04's and B06's follow-ups,
    the reference reads each as the complete question's contract, and C2
    had discarded both."""
    assert resolve(asked, standalone) == MemoryNote(resolved=True, read_as=standalone)


def test_the_third_quarter_is_not_q3_to_c2():
    """The reference reads "the third quarter of fiscal year 2025" as
    another period than "fiscal Q3 of fiscal year 2025"."""
    note = resolve("What about Q3?", "What was the total markdown discount in the third quarter of fiscal year 2025?")
    assert note.reason == "C2 literals: 'q3' missing"


# ---------------------------------------------------------------------------
# C3 polarity
# ---------------------------------------------------------------------------


def test_the_lowest_is_not_the_highest():
    assert resolve("and the lowest?", "Which department had the highest net sales?").reason == "C3 polarity: low missing"


def test_a_lost_exclusion_discards_it():
    note = resolve("excluding markdowns?", "What were our gross sales in fiscal year 2025, with markdowns?")
    assert note.reason == "C3 polarity: negation missing"


def test_a_direction_the_turns_supplied_may_be_added():
    assert resolve("and by store?", "Which stores had the highest net sales in fiscal year 2025?").resolved


# ---------------------------------------------------------------------------
# C4, the bound
# ---------------------------------------------------------------------------


def test_a_restatement_longer_than_a_question_may_be_discards_it():
    standalone = "What were our net sales by store " + "x" * (memory.MAX_QUESTION_LENGTH - 32)
    assert len(standalone) == memory.MAX_QUESTION_LENGTH + 1
    assert resolve("and by store?", standalone).reason == "C4 shape: longer than 2000 characters"
    assert resolve("and by store?", standalone[:-1]).resolved


def test_the_bound_is_the_apis_own():
    """One home: the longest question a request may ask is the longest a
    restatement may be."""
    from nl2sql_agent.api import models

    assert models.MAX_QUESTION_LENGTH is memory.MAX_QUESTION_LENGTH == 2000


def test_the_bound_can_be_given():
    assert resolve("and by store?", "What were net sales by store?", max_length=10).reason == (
        "C4 shape: longer than 10 characters"
    )


# ---------------------------------------------------------------------------
# Held, and the order
# ---------------------------------------------------------------------------


def test_a_restatement_that_adds_the_turns_period_and_entity_and_keeps_everything_is_held():
    note = resolve(
        "and the bottom 3 for Dairy & Eggs?",
        "Which  3 products in the Dairy & Eggs department had the lowest\nnet sales in fiscal year 2025?",
    )
    assert note == MemoryNote(
        resolved=True,
        read_as="Which 3 products in the Dairy & Eggs department had the lowest net sales in fiscal year 2025?",
    )


def test_the_first_failing_check_is_the_one_named():
    """A restatement that drops a number, a name and a direction is
    discarded for the number: nothing after the first failure is computed."""
    note = resolve("and the bottom 3 for Dairy & Eggs?", "and the top dairy products?")
    assert note.reason == "C1 numbers: 3 missing"
    assert resolve("and the bottom 3 for Dairy & Eggs?", "and the top 3 dairy products?").reason == (
        "C2 literals: 'dairy & eggs' missing"
    )


def test_shown_is_the_callers_to_fill():
    assert resolve("and by store?", "What were net sales by store?").shown == 0


def test_a_note_renders_as_json():
    note = MemoryNote(shown=2, resolved=True, read_as="What were net sales by store?")
    assert to_jsonable(note) == {"shown": 2, "resolved": True, "read_as": "What were net sales by store?", "reason": ""}


def test_a_turn_renders_as_json_its_cells_already_text():
    turn = Turn(index=2, asked="And by banner?", question="Net sales by banner in FY2025?", sql="SELECT 1",
                columns=["banner_name", "net_sales"], rows=[["Mainline", "412300000.00"]], row_count=5,
                answer="Mainline led.", assumptions=["FY2025"], job_id="j1")
    assert to_jsonable(turn) == {
        "index": 2, "asked": "And by banner?", "question": "Net sales by banner in FY2025?", "sql": "SELECT 1",
        "columns": ["banner_name", "net_sales"], "rows": [["Mainline", "412300000.00"]], "row_count": 5,
        "answer": "Mainline led.", "assumptions": ["FY2025"], "job_id": "j1",
    }



# ---------------------------------------------------------------------------
# The turn, made from a finished run (arch8 section 23.2)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8.0.1: the checks' blind spots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "standalone"),
    [("and the second one?", "What were net sales for the Value banner in FY2025?"),
     ("just the first", "What were net sales for the Mainline banner in FY2025?"),
     ("the 2nd of those?", "What were net sales for the Value banner in FY2025?"),
     ("Just the first one, by month", "What were the Mainline banner's net sales by month in FY2025?"),
     ("The same for the fourth fiscal quarter?", "What were net sales by banner in fiscal quarter 4 of FY2025?")],
    ids=["second one", "the first", "2nd of those", "first one, by month", "an ordinal that orders"],
)
def test_an_ordinal_that_points_at_a_row_counts_nothing(asked, standalone):
    """C1's exemption (8.0.1): "the second one" names the second row of the
    last answer, which a restatement names rather than numbers, and the
    "one" after it is no count either. An ordinal that orders something is
    still its number, as the paraphrase set found (B06)."""
    assert resolve(asked, standalone).resolved


@pytest.mark.parametrize(
    ("asked", "standalone", "reason"),
    [("The same for the fourth fiscal quarter?", "What were net sales by banner in the fiscal quarter?",
      "C1 numbers: 4 missing"),
     ("and the top 3 of those?", "What were the top banners by net sales in FY2025?", "C1 numbers: 3 missing"),
     ("and the second one of the top 5?", "What were net sales for the Value banner in FY2025?",
      "C1 numbers: 5 missing")],
    ids=["an ordinal before a noun", "a count", "a count beside a reference"],
)
def test_every_other_number_still_counts(asked, standalone, reason):
    assert resolve(asked, standalone).reason == reason


@pytest.mark.parametrize(
    ("asked", "standalone", "held"),
    [("without Mainline", "What were net sales by banner in FY2025 other than Mainline?", True),
     ("without Mainline", "What were net sales by banner in FY2025 apart from Mainline?", True),
     ("not Mainline", "What were net sales for the banners aside from Mainline in FY2025?", True),
     ("but not Mainline", "What were net sales by banner in FY2025 other than Mainline?", True),
     ("other than Mainline?", "What were net sales for the Mainline banner in FY2025?", False),
     ("without Mainline", "What were net sales for the Mainline banner in FY2025?", False)],
    ids=["other than", "apart from", "aside from", "but not", "a phrase typed and dropped", "dropped"],
)
def test_a_negation_written_as_a_phrase_is_a_negation_on_either_side(asked, standalone, held):
    """C3's phrase pass (8.0.1): the restatement that writes "without" as
    "other than" keeps the exclusion, and one that drops a phrase typed
    loses it."""
    note = resolve(asked, standalone)
    assert note.resolved is held
    if not held:
        assert note.reason == "C3 polarity: negation missing"


def test_a_fiscal_year_written_year_first_is_the_year_typed_and_a_quarter_is_not():
    """C2's allowance grown (8.0.1): "the 2024 fiscal year" is FY2024; "the
    3rd quarter" is still not Q3, which the reference reads as another period."""
    assert resolve("Same for FY2024?", "What were net sales by banner in the 2024 fiscal year?").resolved
    assert resolve("and Q3?", "What were net sales by banner in the 3rd quarter of FY2025?").reason == (
        "C2 literals: 'q3' missing")


from nl2sql_agent.memory import TURNS_HEADING, cell_text, first_sentence, render_turns, turn_from_state  # noqa: E402
from nl2sql_agent.state import QueryResult, new_state  # noqa: E402


def _answered(**fields) -> dict:
    state = new_state("And by banner?")
    state.update({
        "question": "What were our net sales by banner in FY2025?",
        "sql": "SELECT banner_name, SUM(net_sales_amt) FROM x GROUP BY 1",
        "result": QueryResult(columns=["banner_name", "net_sales"],
                              rows=[[f"B{i}", None if i == 1 else 10.5 * i] for i in range(10)]),
        "narrative": "Mainline led with $412.3M. Value came second. The rest trailed.",
        "assumptions": ["FY2025, the latest complete fiscal year"],
    })
    state.update(fields)
    return state


@pytest.mark.parametrize(
    "unanswered",
    [{"verdict": "injection"}, {"verdict": "ambiguous"}, {"error": "Could not produce a valid query"},
     {"sql": ""}, {"result": None}],
    ids=["refused", "asked back", "gave up", "no sql", "no rows"],
)
def test_a_run_that_answered_nothing_is_no_turn(unanswered):
    """Nothing was answered that a follow-up could be about."""
    assert turn_from_state(_answered(**unanswered), index=1) is None


def test_a_turn_holds_the_question_answered_three_rows_as_text_and_a_sentence():
    turn = turn_from_state(_answered(), index=3, job_id="j7")
    assert (turn.index, turn.asked, turn.question, turn.job_id) == (
        3, "And by banner?", "What were our net sales by banner in FY2025?", "j7")
    assert turn.sql.startswith("SELECT banner_name")
    assert turn.columns == ["banner_name", "net_sales"]
    assert turn.rows == [["B0", "0.0"], ["B1", "NULL"], ["B2", "21.0"]], "three rows, every cell text"
    assert turn.row_count == 10
    assert turn.answer == "Mainline led with $412.3M."
    assert turn.assumptions == ["FY2025, the latest complete fiscal year"]


def test_a_turn_is_made_from_a_result_that_crossed_a_boundary_as_a_dict():
    state = _answered(result={"columns": ["n"], "rows": [[12]], "truncated": False})
    turn = turn_from_state(state, index=1)
    assert (turn.columns, turn.rows, turn.row_count) == (["n"], [["12"]], 1)


@pytest.mark.parametrize(
    ("text", "first"),
    [
        ("There are 12 stores. Mainline has 5.", "There are 12 stores."),
        ("Net sales were $1,234.56 in FY2025! Up 3.5% on FY2024.", "Net sales were $1,234.56 in FY2025!"),
        ("Was it Mainline? Yes.", "Was it Mainline?"),
        ("  no   end in sight  ", "no end in sight"),
        ("", ""),
    ],
)
def test_the_first_sentence_ends_at_a_stop_followed_by_a_space_or_the_end(text, first):
    assert first_sentence(text) == first


def test_a_sentence_past_the_bound_is_cut_on_a_word_and_marked_within_it():
    long = "Mainline " * 60 + "led."
    cut = first_sentence(long, limit=50)
    assert len(cut) <= 50 and cut.endswith("...") and cut.startswith("Mainline Mainline")
    assert not cut[:-3].endswith(" ")
    assert first_sentence("x" * 80, limit=20) == "x" * 17 + "..."


def test_no_turns_render_nothing_so_the_prompt_is_7_0s():
    assert render_turns([]) == ""


def test_the_turns_render_as_history_oldest_first_each_once_ending_with_a_blank_line():
    first = turn_from_state(_answered(), index=1)
    second = Turn(index=2, asked="and the lowest?", question="Which banner had the lowest net sales in FY2025?",
                  sql="SELECT 1", columns=["banner_name"], rows=[["Value"]], row_count=1, answer="Value.")
    block = render_turns([first, second])
    assert block.splitlines() == [
        TURNS_HEADING,
        "1. Asked: What were our net sales by banner in FY2025?",
        "   Answered: Mainline led with $412.3M.",
        "   (10 rows, e.g. banner_name=B0, net_sales=0.0; assumed: FY2025, the latest complete fiscal year)",
        "2. Asked: Which banner had the lowest net sales in FY2025?",
        "   Answered: Value.",
        "   (1 row, e.g. banner_name=Value)",
        "",
    ]
    assert block.endswith("\n\n")
    assert "And by banner?" not in block, "the question answered, not the words typed"


def test_a_turn_with_no_rows_and_no_sentence_says_only_how_many():
    bare = Turn(index=1, asked="q", question="q", sql="SELECT 1", row_count=0)
    assert render_turns([bare]).splitlines()[1:] == ["1. Asked: q", "   (0 rows)", ""]


# ---------------------------------------------------------------------------
# 8.0.1: a turn bounded in characters, not only in counts
# ---------------------------------------------------------------------------


def test_a_cell_is_one_line_of_at_most_its_bound_with_the_cut_marked_within_it():
    """arch8's R4 bounded a turn by rows and a sentence; a cell of a text
    column could still be a page, and a line break in one broke the row's
    line in the prompt."""
    page = "word " * 40
    state = _answered(result=QueryResult(columns=["note", "n"], rows=[[page, "a\nb  c"]]))
    [row] = turn_from_state(state, index=1).rows
    assert len(row[0]) == memory.MEMORY_CELL_CHARS and row[0].endswith("...")
    assert row[0].startswith("word word") and "  " not in row[0]
    assert row[1] == "a b c"
    assert (cell_text(None), cell_text(12), cell_text("x" * 80)) == ("NULL", "12", "x" * 80)
    assert cell_text("y" * 81) == "y" * 77 + "..."


def test_the_first_row_shows_at_most_memory_columns_and_says_how_many_more():
    def wide(count: int) -> Turn:
        return Turn(index=1, asked="q", question="q", sql="SELECT 1", columns=[f"c{i}" for i in range(count)],
                    rows=[[str(i) for i in range(count)]], row_count=1)

    assert render_turns([wide(11)]).splitlines()[2] == (
        "   (1 row, e.g. c0=0, c1=1, c2=2, c3=3, c4=4, c5=5, c6=6, c7=7, and 3 more columns)")
    assert render_turns([wide(9)]).splitlines()[2].endswith("c7=7, and 1 more column)")
    assert render_turns([wide(8)]).splitlines()[2].endswith("c7=7)"), "eight is shown whole"
