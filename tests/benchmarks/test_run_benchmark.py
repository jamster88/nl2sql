"""The benchmark CLI: selection, configuration, and scoring one question.

`run_question` is where the reference query, the agent and the scorer meet, so
it is tested against the real database with a stubbed agent -- that exercises
the whole path except the model call, which is the only part a benchmark cannot
fake and still mean anything.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "agent"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks import run_benchmark  # noqa: E402
from benchmarks.questions import by_id  # noqa: E402
from benchmarks.runner import CORRECT, ERROR, FAILED, WRONG, BenchmarkReport, StageTimer  # noqa: E402
from tests.benchmarks.test_runner import result  # noqa: E402

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail"
)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_everything_runs_by_default():
    assert len(run_benchmark.select(run_benchmark.parse_args([]))) == 15


def test_a_subset_can_be_named():
    chosen = run_benchmark.select(run_benchmark.parse_args(["--only", "b07", "B08"]))
    assert [q.id for q in chosen] == ["B07", "B08"]


def test_an_unknown_id_fails_rather_than_silently_running_less():
    with pytest.raises(SystemExit, match="no such question"):
        run_benchmark.select(run_benchmark.parse_args(["--only", "B99"]))


def test_a_category_can_be_run_on_its_own():
    chosen = run_benchmark.select(run_benchmark.parse_args(["--category", "grain"]))
    assert chosen and all(q.category == "grain" for q in chosen)


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------


def test_the_three_configurations_differ_only_in_what_retrieval_is_on():
    """That is what makes a difference between two rows attributable. If they
    differed in the model or the database too, the comparison would mean nothing.
    """
    assert set(run_benchmark.CONFIGURATIONS) == {"schema-only", "knowledge", "multi-shot"}
    keys = [set(c) for c in run_benchmark.CONFIGURATIONS.values()]
    assert all(k == keys[0] for k in keys)
    assert keys[0] == {"rag_enabled", "examples_enabled", "multi_shot_enabled"}


def test_each_configuration_adds_one_stage_to_the_one_before():
    schema, knowledge, multi = (
        run_benchmark.CONFIGURATIONS[name]
        for name in ("schema-only", "knowledge", "multi-shot")
    )
    assert not any(schema.values())
    assert knowledge["rag_enabled"] and not knowledge["examples_enabled"]
    assert all(multi.values())


def test_building_settings_applies_the_configuration_and_the_overrides():
    args = run_benchmark.parse_args(["--model", "other-model", "--base-url", "http://h:1"])
    settings = run_benchmark.build_settings(args, "schema-only")
    assert settings.rag_enabled is False
    assert settings.examples_enabled is False
    assert settings.ollama_model == "other-model"
    assert settings.ollama_base_url == "http://h:1"


def test_the_default_configuration_is_the_full_agent():
    assert run_benchmark.parse_args([]).config == "multi-shot"


# ---------------------------------------------------------------------------
# Scoring one question, against the real database
# ---------------------------------------------------------------------------


class FakeAgent:
    """Returns whatever state a real run would have produced."""

    def __init__(self, state=None, raises=None):
        self._state = state or {}
        self._raises = raises
        self.asked: list[str] = []

    def run(self, question: str):
        self.asked.append(question)
        if self._raises is not None:
            raise self._raises
        return self._state


@pytest.fixture(scope="module")
def database():
    from nl2sql_agent.database import Database

    db = Database(DATABASE_URL, statement_timeout_ms=60000, max_rows=1000)
    try:
        db.run_select("SELECT 1")
    except Exception as exc:
        pytest.skip(f"no reachable retail database at {DATABASE_URL}: {exc}")
    return db


def score(database, question_id: str, sql: str | None, rows, **state):
    """Run one question with a stubbed agent that 'produced' these rows."""
    question = by_id(question_id)
    full = {"sql": sql, "result": {"rows": rows, "row_count": len(rows)}, "attempts": 1}
    full.update(state)
    return run_benchmark.run_question(FakeAgent(full), database, question, StageTimer())


@pytest.mark.docker
def test_the_reference_answer_scores_correct(database):
    """The benchmark has to agree with itself: feeding back the reference rows
    must score as correct, or every real result is measured against nothing.
    """
    for question_id in ("B01", "B03", "B08", "B14"):
        question = by_id(question_id)
        rows = [list(r) for r in database.run_select(question.reference_sql).rows]
        assert score(database, question_id, "SELECT 1", rows).outcome == CORRECT


@pytest.mark.docker
def test_a_differently_shaped_but_equivalent_answer_still_scores_correct(database):
    """Column order and naming are presentation. B03 returns (banner, count);
    an agent returning (count, banner) answered the same question.
    """
    rows = [list(r) for r in database.run_select(by_id("B03").reference_sql).rows]
    flipped = [[count, banner] for banner, count in rows]
    assert score(database, "B03", "SELECT 1", flipped).outcome == CORRECT


@pytest.mark.docker
def test_the_fan_out_answer_scores_wrong(database):
    """B08 exists to catch this. The naive query returns 107.5%, and if the
    scorer let that pass the benchmark would report a broken agent as working.
    """
    assert score(database, "B08", "SELECT 1", [[107.55]]).outcome == WRONG


@pytest.mark.docker
def test_a_calendar_year_answer_scores_wrong(database):
    """B04's trap: reading "fiscal year 2025" as calendar 2025 returns roughly a
    quarter of the real total.
    """
    assert score(database, "B04", "SELECT 1", [[1500000.00]]).outcome == WRONG


@pytest.mark.docker
def test_giving_up_is_recorded_as_failed_not_wrong(database):
    """No SQL at all is a different failure from wrong SQL, and the report
    separates them.
    """
    result = run_benchmark.run_question(
        FakeAgent({"error": "Could not produce a valid query in 3 attempts", "attempts": 3}),
        database, by_id("B01"), StageTimer(),
    )
    assert result.outcome == FAILED
    assert not result.answered


@pytest.mark.docker
def test_sql_the_database_rejected_is_recorded_as_an_error(database):
    result = run_benchmark.run_question(
        FakeAgent({"error": 'column "nope" does not exist', "sql": "SELECT nope", "attempts": 1}),
        database, by_id("B01"), StageTimer(),
    )
    assert result.outcome == ERROR
    assert result.sql == "SELECT nope"


@pytest.mark.docker
def test_a_crash_inside_the_agent_is_a_result_not_a_benchmark_failure(database):
    """One question blowing up must not abandon the other fourteen."""
    result = run_benchmark.run_question(
        FakeAgent(raises=RuntimeError("connection reset")),
        database, by_id("B01"), StageTimer(),
    )
    assert result.outcome == FAILED
    assert "connection reset" in result.error


@pytest.mark.docker
def test_the_result_records_what_retrieval_provided(database):
    """So a wrong answer can be read against the examples it was given."""
    rows = [list(r) for r in database.run_select(by_id("B01").reference_sql).rows]
    result = score(
        database, "B01", "SELECT 1", rows,
        example_pairs=[{"pair_id": "Q01"}, {"pair_id": "Q10"}],
        knowledge_chunks=[{}, {}, {}],
    )
    assert result.examples == ["Q01", "Q10"]
    assert result.knowledge_chunks == 3


@pytest.mark.docker
def test_the_agent_is_asked_the_question_text_and_nothing_else(database):
    """No reference SQL, no table hints -- otherwise the benchmark is grading
    itself.
    """
    agent = FakeAgent({"sql": "SELECT 1", "result": {"rows": [[10]]}, "attempts": 1})
    run_benchmark.run_question(agent, database, by_id("B01"), StageTimer())
    assert agent.asked == ["How many stores are there?"]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_the_json_report_carries_accuracy_speed_and_every_question():
    report = BenchmarkReport(label="multi-shot", results=[
        result("B01", "schema", CORRECT, 1.0, [("generate_sql", 0.8)]),
        result("B02", "schema", WRONG, 2.0, [("generate_sql", 1.5)]),
    ])
    payload = json.loads(json.dumps(run_benchmark.as_json([report]), default=str))
    [config] = payload["configurations"]
    assert config["label"] == "multi-shot"
    assert config["correct"] == 1 and config["total"] == 2
    assert config["accuracy"] == 0.5
    assert config["stage_totals"]["generate_sql"] == pytest.approx(2.3)
    assert [r["id"] for r in config["results"]] == ["B01", "B02"]
    assert config["results"][0]["stages"]["generate_sql"] == 0.8


def test_printing_a_report_mentions_accuracy_then_speed(capsys):
    report = BenchmarkReport(label="multi-shot", results=[
        result("B01", "schema", CORRECT, 1.0, [("generate_sql", 0.9)]),
        result("B02", "grain", WRONG, 4.0, [("generate_sql", 3.5)]),
    ])
    run_benchmark.print_report(report)
    out = capsys.readouterr().out
    assert out.index("ACCURACY") < out.index("SPEED")
    assert "1/2" in out
    assert "generate_sql" in out
    assert "B02" in out, "a question that was not correct should be named"


def test_the_comparison_table_lists_every_configuration(capsys):
    reports = [
        BenchmarkReport(label=name, results=[result("B01", "schema", CORRECT, 1.0)])
        for name in ("schema-only", "knowledge", "multi-shot")
    ]
    run_benchmark.print_comparison(reports)
    out = capsys.readouterr().out
    for name in ("schema-only", "knowledge", "multi-shot"):
        assert name in out
