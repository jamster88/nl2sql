"""The benchmark CLI: selection, configuration, and scoring one question.

`run_question` is where the reference query, the agent and the scorer meet, so
it is tested against the real database with a stubbed agent -- that exercises
the whole path except the model call, which is the only part a benchmark cannot
fake and still mean anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "agent"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks import run_benchmark  # noqa: E402
from benchmarks.paraphrases import PARAPHRASES, wordings  # noqa: E402
from benchmarks.questions import by_id  # noqa: E402
from benchmarks.runner import CORRECT, ERROR, FAILED, WRONG, BenchmarkReport, StageTimer  # noqa: E402
from tests.benchmarks.test_runner import result  # noqa: E402
from tests import live_stores

DATABASE_URL = live_stores.url("retail", variable="TEST_DATABASE_URL")


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


#: What a configuration may switch: the four retrieval stages, and the ensemble.
SWITCHES = {"rag_enabled", "examples_enabled", "multi_shot_enabled", "snippets_enabled", "ensemble_enabled"}


def test_the_five_configurations_differ_only_in_which_stages_are_on():
    """That is what makes a difference between two rows attributable. If they
    differed in the model or the database too, the comparison would mean nothing.
    """
    assert list(run_benchmark.CONFIGURATIONS) == ["schema-only", "knowledge", "multi-shot", "snippets", "ensemble"]
    assert all(set(c) == SWITCHES for c in run_benchmark.CONFIGURATIONS.values())


def test_each_configuration_adds_one_stage_to_the_one_before():
    schema, knowledge, multi, snippets, ensemble = run_benchmark.CONFIGURATIONS.values()
    assert not any(schema.values())
    assert knowledge["rag_enabled"] and not knowledge["examples_enabled"]
    assert not knowledge["snippets_enabled"]
    assert multi["examples_enabled"] and multi["multi_shot_enabled"] and not multi["snippets_enabled"]
    assert snippets == {**multi, "snippets_enabled": True}
    assert ensemble == {**snippets, "ensemble_enabled": True}


def test_only_the_ensemble_configuration_asks_several_ways():
    """ENSEMBLE_ENABLED is on by default, so a configuration that left it out
    would be the ensemble in every row and the retrieval ladder would stop
    measuring retrieval."""
    on = [name for name, c in run_benchmark.CONFIGURATIONS.items() if c["ensemble_enabled"]]
    assert on == ["ensemble"]


def test_every_retrieval_switch_the_agent_has_is_set_by_each_configuration():
    """A switch left out is the agent's default in every row -- on -- and the
    schema-only row quietly stops being schema-only."""
    from nl2sql_agent.config import Settings

    switches = {name for name in vars(Settings()) if name.endswith("_enabled")}
    assert SWITCHES <= switches
    for configuration in run_benchmark.CONFIGURATIONS.values():
        assert set(configuration) == SWITCHES


def test_building_settings_applies_the_configuration_and_the_overrides():
    args = run_benchmark.parse_args(["--model", "other-model", "--base-url", "http://h:1"])
    settings = run_benchmark.build_settings(args, "schema-only")
    assert settings.rag_enabled is False
    assert settings.examples_enabled is False
    assert settings.ollama_model == "other-model"
    assert settings.ollama_base_url == "http://h:1"


def test_a_host_default_gives_way_to_the_environment(monkeypatch, tmp_path):
    """The defaults are for running from the host against the compose ports;
    inside a container, or against another stack, the environment says where
    the database is and is believed."""
    monkeypatch.setattr(run_benchmark, "SECRETS", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://r:r@elsewhere:5432/retail")
    monkeypatch.delenv("VECTOR_DB_URL", raising=False)
    settings = run_benchmark.build_settings(run_benchmark.parse_args([]), "schema-only")
    assert settings.database_url == "postgresql+psycopg://r:r@elsewhere:5432/retail"
    assert settings.vector_db_url == run_benchmark.HOST_DEFAULTS["vector_db_url"][1]


def test_a_host_default_takes_the_password_the_stack_generated(monkeypatch, tmp_path):
    """Since 6.1 every password is generated, and since 6.3 each is a file in
    secrets/: the default's own password reaches nothing on such a stack."""
    monkeypatch.setattr(run_benchmark, "SECRETS", tmp_path)
    (tmp_path / "postgres_reader_password").write_text("g/en@rated\n")
    (tmp_path / "vector_db_password").write_text("")
    for variable in ("DATABASE_URL", "VECTOR_DB_URL", "EMBED_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    settings = run_benchmark.build_settings(run_benchmark.parse_args([]), "schema-only")
    assert settings.database_url == "postgresql+psycopg://nl2sql_reader:g%2Fen%40rated@localhost:5432/nl2sql_retail"
    # An empty file, or none, leaves the default as it is; a setting with no
    # password is never touched.
    assert settings.vector_db_url == run_benchmark.HOST_DEFAULTS["vector_db_url"][1]
    assert settings.context_db_url == run_benchmark.HOST_DEFAULTS["context_db_url"][1]
    assert settings.embed_base_url == "http://localhost:11434"


def test_the_default_configuration_is_every_retrieval_stage_asked_once():
    """One run a question: the measurement every earlier release's numbers
    are, and a quarter of the ensemble's cost once it asks four ways."""
    assert run_benchmark.parse_args([]).config == "snippets"
    assert run_benchmark.CONFIGURATIONS["snippets"] == {**dict.fromkeys(SWITCHES, True), "ensemble_enabled": False}


def test_the_snippet_store_is_found_on_its_published_port(monkeypatch, tmp_path):
    """The runtime stores' port since 6.3, where all four databases are."""
    monkeypatch.setattr(run_benchmark, "SECRETS", tmp_path)
    monkeypatch.delenv("SNIPPET_DB_URL", raising=False)
    settings = run_benchmark.build_settings(run_benchmark.parse_args([]), "snippets")
    assert settings.snippet_db_url.endswith("@localhost:5435/nl2sql_snippets")
    assert settings.snippets_enabled is True


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
        live_stores.unreachable("retail database", DATABASE_URL, exc)
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
def test_a_run_records_which_model_answered_each_call(database):
    from types import SimpleNamespace

    trace = [SimpleNamespace(node="generate_sql", ms=800.0, model_calls=1, model="qwen3.8-256k:latest",
                             rung="light", hops=[]),
             SimpleNamespace(node="execute_query", ms=5.0, model_calls=0, model="", rung="", hops=[])]
    result = score(database, "B01", "SELECT count(*) FROM dim_store", [[10]], trace=trace,
                   complexity=SimpleNamespace(rung="light"))
    assert result.rung == "light"
    assert result.routes == [{"node": "generate_sql", "model": "qwen3.8-256k:latest", "rung": "light",
                              "ms": 800.0, "hops": []}]


@pytest.mark.docker
def test_the_agent_is_asked_the_question_text_and_nothing_else(database):
    """No reference SQL, no table hints -- otherwise the benchmark is grading
    itself.
    """
    agent = FakeAgent({"sql": "SELECT 1", "result": {"rows": [[10]]}, "attempts": 1})
    run_benchmark.run_question(agent, database, by_id("B01"), StageTimer())
    assert agent.asked == ["How many stores are there?"]


class _Reference:
    """A database that knows one thing: the reference answer."""

    def __init__(self, rows) -> None:
        self._rows = rows

    def run_select(self, sql: str):
        from types import SimpleNamespace

        return SimpleNamespace(rows=self._rows)


def test_a_rewording_is_asked_in_its_own_words_and_scored_against_its_questions_reference():
    """A rewording asks the same question, so it has the same answer: the
    agent hears the rewording, and the reference is the question's."""
    text = PARAPHRASES["B01"][1]
    agent = FakeAgent({"sql": "SELECT 1", "result": {"rows": [[10]]}, "attempts": 1})
    result = run_benchmark.run_question(agent, _Reference([[10]]), by_id("B01"), StageTimer(), asked=text, wording=2)
    assert agent.asked == [text]
    assert (result.question, result.wording, result.label, result.outcome) == (text, 2, "B01.2", CORRECT)

    crashed = run_benchmark.run_question(
        FakeAgent(raises=RuntimeError("connection reset")), _Reference([[10]]), by_id("B01"), StageTimer(),
        asked=text, wording=2,
    )
    assert (crashed.question, crashed.wording, crashed.outcome) == (text, 2, FAILED)


def test_an_ensemble_answer_is_scored_from_what_it_delivered_and_timed_from_every_run():
    """arch7: the rows and SQL scored are the delivered ones; the attempts,
    the examples and the rung are the delivered run's; the time and the model
    calls are every run's and the ensemble's own nodes', the node that ran
    the candidates left out so its time is not counted twice."""
    from types import SimpleNamespace

    from nl2sql_agent.ensemble_state import Candidate, Decision, new_ensemble_state
    from nl2sql_agent.state import TraceEntry, new_state

    run = {**new_state("q"), "attempts": 3, "example_pairs": [{"pair_id": "Q07"}], "knowledge_chunks": [{}],
           "complexity": SimpleNamespace(rung="standard"),
           "trace": [TraceEntry(node="generate_sql", ms=800.0, model_calls=1, model="m", rung="standard")]}
    state = {
        **new_ensemble_state("q"),
        "candidates": [Candidate(index=0, wording="q", origin="original", wave=1, state=run, outcome="answered")],
        "decision": Decision(chosen=0),
        "sql": "SELECT 10", "result": {"rows": [[10]]},
        "trace": [TraceEntry(node="screen", ms=100.0, model_calls=1, model="m", rung="light"),
                  TraceEntry(node="answer", ms=900.0), TraceEntry(node="deliver", ms=1.0)],
    }
    result = run_benchmark.run_question(FakeAgent(state), _Reference([[10]]), by_id("B01"), StageTimer())

    assert (result.outcome, result.sql, result.attempts) == (CORRECT, "SELECT 10", 3)
    assert (result.examples, result.knowledge_chunks, result.rung) == (["Q07"], 1, "standard")
    assert (result.agreement, result.candidates, result.candidate_rungs) == ("none", 1, ["standard"])
    assert result.rejections == {} and result.state_bytes > 0
    assert result.timing.as_dict() == {"screen": 0.1, "deliver": 0.001, "generate_sql": 0.8}
    assert result.model_calls == {"screen": 1, "generate_sql": 1}
    assert [route["node"] for route in result.routes] == ["screen", "generate_sql"]


def test_the_ensemble_configuration_asks_through_the_ensemble(monkeypatch, capsys):
    from types import SimpleNamespace

    import benchmarks.run_benchmark as rb
    import nl2sql_agent.ensemble as ensemble_module

    built = []

    class _Ensemble:
        def __init__(self, settings, *, agent, on_progress):
            built.append((agent, on_progress))
            self.inner = agent

        def run(self, question, **kwargs):
            return self.inner.run(question, **kwargs)

        tracer = property(lambda self: self.inner.tracer)
        settings = property(lambda self: self.inner.settings)

    monkeypatch.setattr(ensemble_module, "EnsembleAgent", _Ensemble)
    monkeypatch.setattr(rb, "build_settings", lambda args, configuration: SimpleNamespace(
        **{**vars(_settings()), "ensemble_enabled": configuration == "ensemble"}))
    code, agents = _drive(monkeypatch, {"sql": "SELECT 1", "result": {"rows": [[10]]}, "attempts": 1},
                          argv=["--only", "B01", "--config", "ensemble"], keep_settings=True)
    assert code == 0
    [(agent, timer)] = built
    assert agent is agents[0] and isinstance(timer, StageTimer)


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


def _routed(qid, outcome):
    r = result(qid, "schema", outcome, 1.0, [("generate_sql", 0.9)])
    r.rung = "standard"
    r.routes = [{"node": "generate_sql", "model": "qwen3.8-256k:latest", "rung": "standard", "ms": 900.0,
                 "hops": ["gemma4:12b-mlx: an empty answer"]}]
    return r


def test_the_report_says_which_model_answered_each_agent_at_each_rung(capsys):
    report = BenchmarkReport(label="multi-shot", results=[_routed("B01", CORRECT), _routed("B02", WRONG)])
    run_benchmark.print_report(report)
    out = capsys.readouterr().out

    assert "ROUTING" in out and "generator's task scored   standard 2" in out
    row = next(line for line in out.splitlines() if line.strip().startswith("generate_sql") and "256k" in line)
    assert row.split() == ["generate_sql", "standard", "qwen3.8-256k:latest", "2", "1/2", "0.90s", "(2", "hop(s))"]

    payload = run_benchmark.as_json([report])["configurations"][0]
    assert payload["rungs"] == {"standard": 2}
    assert payload["by_model"][0]["model"] == "qwen3.8-256k:latest"
    assert payload["results"][0]["routes"][0]["hops"] == ["gemma4:12b-mlx: an empty answer"]
    assert payload["results"][0]["rung"] == "standard"


def test_an_unrouted_report_has_no_routing_section(capsys):
    run_benchmark.print_report(BenchmarkReport(results=[result("B01", "schema", CORRECT, 1.0)]))
    assert "ROUTING" not in capsys.readouterr().out


def test_the_comparison_table_lists_every_configuration(capsys):
    reports = [
        BenchmarkReport(label=name, results=[result("B01", "schema", CORRECT, 1.0)])
        for name in ("schema-only", "knowledge", "multi-shot", "snippets")
    ]
    run_benchmark.print_comparison(reports)
    out = capsys.readouterr().out
    for name in ("schema-only", "knowledge", "multi-shot", "snippets"):
        assert name in out
    assert "stable" not in out


def test_comparing_the_paraphrase_set_adds_the_stable_questions(capsys):
    reworded = result("B01", "schema", WRONG, 1.0)
    reworded.wording = 1
    reports = [
        BenchmarkReport(label="snippets", results=[result("B01", "schema", CORRECT, 1.0), reworded]),
        BenchmarkReport(label="knowledge", results=[result("B01", "schema", CORRECT, 1.0)]),
    ]
    run_benchmark.print_comparison(reports)
    lines = capsys.readouterr().out.splitlines()
    assert "stable" in next(line for line in lines if line.strip().startswith("configuration"))
    assert next(line for line in lines if line.strip().startswith("snippets")).split()[-1] == "0/1"
    assert next(line for line in lines if line.strip().startswith("knowledge")).split()[-1] == "1/1"


# ---------------------------------------------------------------------------
# The narrative score: whether the user was told the truth
# ---------------------------------------------------------------------------


class _Report:
    def __init__(self, unsupported: list[str]) -> None:
        self.unsupported_claims = unsupported


def _state(claims: int, unsupported: list[str] | None = None) -> dict:
    return {
        "claims": [object()] * claims,
        "audit": _Report(unsupported or []),
    }


def test_a_narrative_the_audit_traced_in_full_scores_one():
    assert run_benchmark.narrative_score(_state(3)) == 1.0


def test_a_dropped_claim_lowers_the_score():
    """Execution accuracy says whether the SQL was right. This says whether
    the answer's numbers could be traced back to a cell, which is a
    different failure and one nothing in the v3 harness could see.
    """
    assert run_benchmark.narrative_score(_state(4, ["Sales rose 40%."])) == 0.75


def test_a_narrative_where_nothing_survived_scores_zero():
    assert run_benchmark.narrative_score(_state(2, ["a", "b"])) == 0.0


def test_a_run_that_narrated_nothing_scores_none_rather_than_zero():
    """A question with no narration is not a question narrated badly, and
    averaging the two together would say it was.
    """
    assert run_benchmark.narrative_score({"claims": []}) is None
    assert run_benchmark.narrative_score({}) is None


def test_the_score_survives_a_run_with_no_audit_report():
    assert run_benchmark.narrative_score({"claims": [object()]}) == 1.0


# ---------------------------------------------------------------------------
# The driver: run_configuration and main
# ---------------------------------------------------------------------------


def _settings():
    """Only the fields `run_configuration` reads before the agent is built."""
    from types import SimpleNamespace

    return SimpleNamespace(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
        db_schema="public",
        statement_timeout_ms=30000,
        max_rows=50,
        ensemble_enabled=False,
    )


class _StubAgent:
    """An agent that answers every question with the same canned state."""

    def __init__(self, state: dict, tracer=None) -> None:
        from nl2sql_agent.config import Settings
        from nl2sql_agent.tracing import Tracer

        self._state = state
        self.asked: list[str] = []
        self.settings = Settings()
        # Untraced unless a test hands it a tracer: MLFLOW_TRACKING_URI unset.
        self.tracer = tracer or Tracer(self.settings)

    def run(self, question: str, **kwargs) -> dict:
        self.asked.append(question)
        # As the real agent does: a trace per run, when there is a tracer
        # that can open one, and its id on the state.
        with self.tracer.run(question) as trace:
            state = dict(self._state)
            if trace is not None:
                state["trace_id"] = trace.trace_id
        return state


def _drive(monkeypatch, state: dict, *, argv: list[str], expected_rows=None, tracer=None, keep_settings=False):
    """Run `main` with the agent, the database and the reference rows faked."""
    import benchmarks.run_benchmark as rb

    monkeypatch.setattr(rb, "reference_rows", lambda db, q: expected_rows or [[10]])
    if not keep_settings:
        monkeypatch.setattr(rb, "build_settings", lambda args, configuration: _settings())

    class _FakeDatabase:
        def __init__(self, *a, **k) -> None:
            pass

    import nl2sql_agent.database as db_module
    import nl2sql_agent.graph as graph_module

    monkeypatch.setattr(db_module, "Database", _FakeDatabase)
    agents: list[_StubAgent] = []

    def _build(settings, **kwargs):
        agent = _StubAgent(state, tracer)
        agents.append(agent)
        return agent

    monkeypatch.setattr(graph_module, "Nl2SqlAgent", _build)
    code = rb.main(argv)
    return code, agents


def test_a_run_where_every_answer_is_right_exits_zero(monkeypatch, capsys):
    """CI gates on the exit code, so it has to mean what it says."""
    code, agents = _drive(
        monkeypatch,
        {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False},
         "attempts": 1},
        argv=["--only", "B01"],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "execution accuracy   1/1" in out
    assert agents[0].asked == ["How many stores are there?"]
    assert "STABILITY" not in out


def test_a_wrong_answer_exits_non_zero_and_is_named(monkeypatch, capsys):
    code, _ = _drive(
        monkeypatch,
        {"sql": "SELECT 1", "result": {"rows": [[999]], "columns": ["n"], "truncated": False},
         "attempts": 1},
        argv=["--only", "B01"],
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "not correct" in out
    assert "B01" in out


def test_an_agent_that_gave_up_is_reported_as_failed(monkeypatch, capsys):
    code, _ = _drive(
        monkeypatch, {"error": "Could not produce a valid query", "attempts": 4},
        argv=["--only", "B01"],
    )
    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_a_refusal_is_scored_rather_than_crashing_the_run(monkeypatch, capsys):
    """No error and no rows means the Supervisor stopped before any SQL. A
    screen that refuses real questions has to show up as lost accuracy.
    """
    code, _ = _drive(
        monkeypatch, {"verdict": "out_of_domain", "attempts": 0}, argv=["--only", "B01"]
    )
    assert code == 1
    assert "refused before generating SQL" in capsys.readouterr().out


def test_comparing_configurations_runs_each_one_and_prints_the_table(monkeypatch, capsys):
    code, agents = _drive(
        monkeypatch,
        {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False},
         "attempts": 1},
        argv=["--only", "B01", "--compare"],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert len(agents) == 5, "one agent per configuration"
    assert "comparison" in out
    for configuration in ("schema-only", "knowledge", "multi-shot", "snippets", "ensemble"):
        assert configuration in out


def test_the_json_file_carries_every_question_and_the_trace(monkeypatch, capsys, tmp_path):
    out_path = tmp_path / "results.json"
    _drive(
        monkeypatch,
        {
            "sql": "SELECT 1",
            "result": {"rows": [[10]], "columns": ["n"], "truncated": False},
            "attempts": 1,
            "trace": [{"node": "generate_sql", "ms": 1500.0, "model_calls": 1}],
            "claims": [object()],
        },
        argv=["--only", "B01", "--json", str(out_path)],
    )
    assert f"wrote {out_path}" in capsys.readouterr().out
    payload = json.loads(out_path.read_text())
    [result] = payload["configurations"][0]["results"]
    assert result["id"] == "B01"
    assert result["wording"] == 0
    assert payload["configurations"][0]["stability"] is None
    assert result["model_calls"] == {"generate_sql": 1}
    assert result["narrative_score"] == 1.0
    assert payload["configurations"][0]["stage_totals"]["generate_sql"] == 1.5


RIGHT = {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False}, "attempts": 1}


def test_the_paraphrase_set_asks_each_question_its_own_way_then_three_others(monkeypatch, capsys):
    code, agents = _drive(monkeypatch, RIGHT, argv=["--only", "B01", "B02", "--paraphrase-set"])
    out = capsys.readouterr().out
    assert code == 0
    assert agents[0].asked == [*wordings(by_id("B01")), *wordings(by_id("B02"))]
    assert "  B01.3 [schema] " in out, "each wording is named as it is asked"
    assert "execution accuracy   8/8" in out
    assert "stable questions     2/2  (100.0%)" in out
    assert "s for 8 wordings" in out


def test_one_wrong_rewording_makes_its_question_unstable_and_fails_the_run(monkeypatch, capsys, tmp_path):
    """Every wording scored against the question's one reference; the report
    names the wording that missed, per question and in the misses."""
    import benchmarks.run_benchmark as rb
    import nl2sql_agent.database as db_module
    import nl2sql_agent.graph as graph_module

    missed = PARAPHRASES["B01"][1]
    wrong = {**RIGHT, "result": {"rows": [[11]], "columns": ["n"], "truncated": False}}

    class _ByWording(_StubAgent):
        def run(self, question: str, **kwargs) -> dict:
            self._state = wrong if question == missed else RIGHT
            return super().run(question, **kwargs)

    monkeypatch.setattr(rb, "reference_rows", lambda db, q: [[10]])
    monkeypatch.setattr(rb, "build_settings", lambda args, configuration: _settings())
    monkeypatch.setattr(db_module, "Database", lambda *a, **k: None)
    monkeypatch.setattr(graph_module, "Nl2SqlAgent", lambda settings, **kwargs: _ByWording(RIGHT))
    out_path = tmp_path / "paraphrase.json"

    code = rb.main(["--only", "B01", "B02", "--paraphrase-set", "--json", str(out_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "stable questions     1/2  (50.0%)" in out
    assert "own wordings         2/2" in out and "rewordings           5/6" in out
    assert "B01 schema     3/4  ok ok WRONG ok" in out
    assert "    B01.2 wrong" in out

    payload = json.loads(out_path.read_text())["configurations"][0]
    assert payload["stability"] == {
        "fraction": 0.5, "stable": 1, "questions": 2,
        "by_question": {"B01": [CORRECT, CORRECT, WRONG, CORRECT], "B02": [CORRECT] * 4},
    }
    assert [(r["id"], r["wording"]) for r in payload["results"][:4]] == [("B01", w) for w in range(4)]
    assert payload["results"][2]["question"] == missed


def test_verbose_puts_each_agent_step_on_stderr(monkeypatch, capsys):
    """Progress belongs on stderr so the report on stdout stays pipeable."""
    import benchmarks.run_benchmark as rb

    monkeypatch.setattr(rb, "reference_rows", lambda db, q: [[10]])
    monkeypatch.setattr(rb, "build_settings", lambda args, configuration: _settings())
    import nl2sql_agent.database as db_module
    import nl2sql_agent.graph as graph_module

    monkeypatch.setattr(db_module, "Database", lambda *a, **k: None)

    def _build(settings, *, on_progress=None, **kwargs):
        on_progress("generate_sql", "SELECT 1")
        return _StubAgent(
            {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False},
             "attempts": 1}
        )

    monkeypatch.setattr(graph_module, "Nl2SqlAgent", _build)
    rb.main(["--only", "B01", "-v"])
    assert "[generate_sql] SELECT 1" in capsys.readouterr().err


def test_without_verbose_the_agent_steps_are_not_printed(monkeypatch, capsys):
    import benchmarks.run_benchmark as rb

    monkeypatch.setattr(rb, "reference_rows", lambda db, q: [[10]])
    monkeypatch.setattr(rb, "build_settings", lambda args, configuration: _settings())
    import nl2sql_agent.database as db_module
    import nl2sql_agent.graph as graph_module

    monkeypatch.setattr(db_module, "Database", lambda *a, **k: None)

    def _build(settings, *, on_progress=None, **kwargs):
        on_progress("generate_sql", "SELECT 1")
        return _StubAgent(
            {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False},
             "attempts": 1}
        )

    monkeypatch.setattr(graph_module, "Nl2SqlAgent", _build)
    rb.main(["--only", "B01"])
    assert "[generate_sql]" not in capsys.readouterr().err


def test_an_unreachable_model_host_stops_the_run_with_a_clean_message(monkeypatch, capsys):
    """A benchmark that reported 0/15 because Ollama was down would look
    exactly like a catastrophic regression.
    """
    import benchmarks.run_benchmark as rb
    import nl2sql_agent.database as db_module
    import nl2sql_agent.graph as graph_module
    from nl2sql_agent.llm import LlmUnavailableError

    monkeypatch.setattr(rb, "build_settings", lambda args, configuration: _settings())
    monkeypatch.setattr(db_module, "Database", lambda *a, **k: None)

    def _explode(settings, **kwargs):
        raise LlmUnavailableError("Cannot reach Ollama at http://127.0.0.1:1")

    monkeypatch.setattr(graph_module, "Nl2SqlAgent", _explode)
    with pytest.raises(SystemExit, match="Cannot reach Ollama"):
        rb.main(["--only", "B01"])


def test_naming_a_question_that_does_not_exist_says_which_one():
    """A typo in an id would otherwise run the whole set, or none of it,
    with no indication which.
    """
    with pytest.raises(SystemExit, match="no such question"):
        run_benchmark.main(["--only", "B99_does_not_exist"])


def test_a_category_with_no_questions_stops_rather_than_reporting_a_perfect_score():
    """An empty run would otherwise print 0/0 and exit zero, which reads as
    everything having passed.
    """
    import benchmarks.run_benchmark as rb

    args = rb.parse_args([])
    args.category = "no-such-category"
    with pytest.raises(SystemExit, match="no questions selected"):
        rb.select(args)


def test_the_database_url_flag_overrides_the_settings(monkeypatch):
    import benchmarks.run_benchmark as rb

    args = rb.parse_args(["--database-url", "postgresql+psycopg://u:p@elsewhere/db"])
    settings = rb.build_settings(args, "multi-shot")
    assert settings.database_url == "postgresql+psycopg://u:p@elsewhere/db"


# ---------------------------------------------------------------------------
# Running it as a script
# ---------------------------------------------------------------------------


def test_the_script_puts_the_repository_on_the_path_for_itself():
    """`python benchmarks/run_benchmark.py` is how this is actually invoked,
    and then nothing has arranged the imports: `benchmarks`, `agent` and the
    `common` package the agent imports are not on `sys.path` and the first
    import fails. The bootstrap at the top of the file is the fix, and under
    pytest it never runs -- conftest has already done the same job -- so it
    is exercised here with the path put back the way a bare interpreter
    leaves it.
    """
    import runpy

    script = REPO_ROOT / "benchmarks" / "run_benchmark.py"
    stripped = {str(REPO_ROOT), str(REPO_ROOT / "agent"), str(REPO_ROOT / "common")}
    # Every occurrence, not the first: pytest inserts the rootdir and so does
    # this module, so removing one entry leaves the bootstrap still satisfied
    # and half of it unexercised.
    saved_path = list(sys.path)
    saved_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "benchmarks" or name.startswith("benchmarks.")
    }
    sys.path[:] = [p for p in sys.path if p not in stripped]
    try:
        namespace = runpy.run_path(str(script), run_name="not_main")
        assert str(REPO_ROOT) in sys.path
        assert str(REPO_ROOT / "agent") in sys.path
        assert str(REPO_ROOT / "common") in sys.path
    finally:
        sys.path[:] = saved_path
        sys.modules.update(saved_modules)

    assert callable(namespace["main"])


@pytest.mark.parametrize("script,then", [
    ("benchmarks/run_benchmark.py", "import nl2sql_agent.config"),
    ("models/calibrate.py", "pass"),
])
def test_a_bare_interpreter_can_reach_the_agent_through_the_scripts_own_bootstrap(script, then, tmp_path):
    """Since 6.2 the agent imports `nl2sql_common`, which the images install
    and a checkout does not, and both scripts that run the agent on the host
    left `common/` off the path: each stopped at its first import of the
    agent with "No module named 'nl2sql_common'" -- found by the first live
    run of the paraphrase set. Every test above runs where conftest has put
    `common/` on the path already, so only an interpreter of its own sees
    what a person typing the command gets."""
    import os
    import subprocess

    code = f"import runpy; runpy.run_path({str(REPO_ROOT / script)!r}, run_name='bootstrap'); {then}"
    environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    ran = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=environment,
                         capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0, ran.stderr[-1500:]


def test_running_it_as_a_script_calls_main_and_exits_with_its_code():
    """The `if __name__ == "__main__"` line: one statement, and the one that
    decides whether a CI job sees a failing benchmark as a failure.
    """
    import runpy

    script = REPO_ROOT / "benchmarks" / "run_benchmark.py"
    saved_argv = sys.argv
    sys.argv = ["run_benchmark.py", "--help"]
    try:
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = saved_argv
    assert raised.value.code == 0


def test_what_the_ensemble_did_with_a_question_is_measured():
    """arch7 section 11 and the risks document: the agreement, the rewordings
    discarded by check, the rung each run was scored at, the state's size."""
    from types import SimpleNamespace

    from nl2sql_agent.ensemble_state import Agreement, Candidate, Paraphrase, new_ensemble_state
    from nl2sql_agent.state import new_state

    def run(index, rung):
        return Candidate(index=index, wording="w", origin="original", wave=1, outcome="answered",
                         state={**new_state("w"), "complexity": SimpleNamespace(rung=rung)})

    state = {
        **new_ensemble_state("q"),
        "agreement": Agreement(admissible=3, agreed=3, total=3, level="unanimous"),
        "paraphrases": [Paraphrase(1, "a", "x", status="faithful"),
                        Paraphrase(2, "b", "y", status="discarded", reason="F4 contract: period"),
                        Paraphrase(3, "c", "z", status="discarded", reason="F1 numbers: 5 added"),
                        Paraphrase(4, "d", "z", status="discarded", reason="F4 verdict: injection")],
        "candidates": [run(2, "standard"), run(0, "light"), run(1, "light")],
    }
    measured = run_benchmark.ensemble_measures(state)
    assert (measured["agreement"], measured["agreed"], measured["candidates"]) == ("unanimous", 3, 3)
    assert measured["rejections"] == {"F4": 2, "F1": 1}
    assert measured["rewordings"][1] == {"index": 2, "text": "b", "changed": "y", "status": "discarded",
                                         "reason": "F4 contract: period"}
    assert [(run["index"], run["outcome"], run["chosen"]) for run in measured["runs"]] == [
        (0, "answered", False), (1, "answered", False), (2, "answered", False)]
    assert measured["candidate_rungs"] == ["light", "light", "standard"]
    assert measured["state_bytes"] > 0
    assert (measured["judged"], measured["verdicts"]) == ("not asked", [])
    assert run_benchmark.ensemble_measures(new_state("q")) == {}


def test_the_judges_verdicts_are_measured_beside_each_run():
    from nl2sql_agent.ensemble_state import Candidate, GroupVerdict, Judgement, new_ensemble_state
    from nl2sql_agent.state import new_state

    def run(index, group):
        return Candidate(index=index, wording="w", origin="original", wave=1, outcome="answered",
                         state=new_state("w"), group=group)

    state = {
        **new_ensemble_state("q"),
        "candidates": [run(0, 1), run(1, 0), run(2, None)],
        "judgement": Judgement(verdicts=[GroupVerdict(0, False, "wrong grain"), GroupVerdict(1, True, "right")],
                               set_aside=[1], overruled=True, instead_of=1),
    }
    measured = run_benchmark.ensemble_measures(state)
    assert measured["judged"] == "overruled"
    assert measured["verdicts"] == [{"group": 0, "accepted": False, "why": "wrong grain"},
                                    {"group": 1, "accepted": True, "why": "right"}]
    assert [run["accepted"] for run in measured["runs"]] == [True, False, None]


def test_the_answer_the_runs_alone_would_have_chosen_is_scored_when_the_judge_overrules_them():
    from nl2sql_agent.ensemble_state import Candidate, Judgement, new_ensemble_state
    from nl2sql_agent.state import QueryResult, new_state

    def run(index, value):
        return Candidate(index=index, wording="w", origin="original", wave=1, outcome="answered",
                         state={**new_state("w"), "result": QueryResult(columns=["n"], rows=[[value]])})

    state = {**new_ensemble_state("q"), "candidates": [run(0, 10), run(1, 99)],
             "judgement": Judgement(overruled=True, instead_of=1)}
    assert run_benchmark.without_judge(state, [[10]], False, CORRECT) == WRONG
    assert run_benchmark.without_judge(state, [[99]], False, WRONG) == CORRECT
    gone = {**state, "judgement": Judgement(overruled=True, instead_of=7)}
    assert run_benchmark.without_judge(gone, [[10]], False, CORRECT) == FAILED
    kept = {**state, "judgement": Judgement()}
    assert run_benchmark.without_judge(kept, [[10]], False, CORRECT) == CORRECT
    assert run_benchmark.without_judge(new_state("q"), [[10]], False, CORRECT) == ""


def test_the_report_and_its_json_say_what_the_ensemble_did(capsys):
    from tests.benchmarks.test_runner import _ensembled

    report = BenchmarkReport(label="ensemble", results=[
        _ensembled("B01", CORRECT, "unanimous", rejections={"F4": 2}, rungs=("light", "standard"), size=2048),
        _ensembled("B02", WRONG, "majority", size=1024, runs=3, judged="overruled", without=CORRECT),
    ])
    run_benchmark.print_report(report)
    out = capsys.readouterr().out
    assert "ENSEMBLE" in out
    assert "agreement            majority 1, unanimous 1" in out
    assert "agreed, but wrong    1" in out
    assert "runs per question    4 (median), 3 to 4" in out
    assert "rewordings discarded 2: F4 2" in out
    assert "runs at mixed rungs  1 of 2 question(s)" in out
    assert "state, as JSON       2.0 KB median, 2.0 KB largest" in out
    assert "the Judge            accepted 1, overruled 1" in out
    assert "overruled the runs   1: wrong -> right 0, right -> wrong 1" in out
    [config] = run_benchmark.as_json([report])["configurations"]
    assert config["ensemble"]["agreement"] == {"unanimous": 1, "majority": 1}
    assert config["ensemble"]["judge"] == {"accepted": 1, "overruled": 1, "fixed": 0, "broke": 1}
    assert config["results"][0]["candidate_rungs"] == ["light", "standard"]
    assert (config["results"][1]["judged"], config["results"][1]["without_judge"]) == ("overruled", CORRECT)
    plain = BenchmarkReport(results=[result("B01", "schema", CORRECT, 1.0, [])])
    assert run_benchmark.as_json([plain])["configurations"][0]["ensemble"] is None


def test_a_report_with_no_state_measured_says_nothing_of_its_size(capsys):
    from tests.benchmarks.test_runner import _ensembled

    run_benchmark.print_report(
        BenchmarkReport(label="ensemble", results=[_ensembled("B01", CORRECT, "single", size=0, judged="")])
    )
    out = capsys.readouterr().out
    assert "ENSEMBLE" in out and "state, as JSON" not in out
    assert "the Judge" not in out, "a run with no Judge says nothing of one"
