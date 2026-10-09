"""nl2sql_agent.__main__: argument parsing, row formatting, and the
answer()/main() glue -- exercised without a real Agent (answer() is given a
tiny stub with the same .run() contract; main()'s LlmUnavailableError path is
exercised against Nl2SqlAgent construction directly).
"""

from __future__ import annotations

import argparse
import json

import pytest

from nl2sql_agent import __main__ as cli
from nl2sql_agent.llm import LlmUnavailableError


class StubAgent:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.questions: list[str] = []

    def run(self, question: str, *, principal: str | None = None) -> dict:
        self.questions.append(question)
        return self._state


# ---------------------------------------------------------------------------
# parse_args / settings_from_args
# ---------------------------------------------------------------------------


def test_parse_args_defaults_come_from_settings(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    args = cli.parse_args(["some", "question"])
    assert args.question == ["some", "question"]
    assert args.model  # falls back to Settings.from_env() default
    assert args.max_attempts >= 1


def test_parse_args_overrides():
    args = cli.parse_args(["--model", "llama3", "--base-url", "http://x:11434", "--max-rows", "5", "q1", "q2"])
    assert args.model == "llama3"
    assert args.base_url == "http://x:11434"
    assert args.max_rows == 5
    assert args.question == ["q1", "q2"]


def test_parse_args_question_is_optional_for_interactive_mode():
    args = cli.parse_args([])
    assert args.question == []


def test_settings_from_args_maps_every_field():
    args = cli.parse_args(
        ["--model", "m", "--base-url", "http://h:11434", "--database-url", "postgresql://x", "--max-rows", "7", "--max-attempts", "2", "--sample-rows", "4", "--reasoning"]
    )
    settings = cli.settings_from_args(args)
    assert settings.ollama_model == "m"
    assert settings.ollama_base_url == "http://h:11434"
    assert settings.database_url == "postgresql://x"
    assert settings.max_rows == 7
    assert settings.max_attempts == 2
    assert settings.sample_rows == 4
    assert settings.reasoning is True


def test_retrieval_flags_map_onto_settings():
    args = cli.parse_args(
        [
            "--vector-db-url", "postgresql+psycopg://v:v@host/vectors",
            "--embed-model", "bge-m3",
            "--embed-url", "http://embedhost:11434",
            "--rag-top-k", "6",
        ]
    )
    settings = cli.settings_from_args(args)
    assert settings.rag_enabled is True
    assert settings.vector_db_url == "postgresql+psycopg://v:v@host/vectors"
    assert settings.embed_model == "bge-m3"
    assert settings.embed_base_url == "http://embedhost:11434"
    assert settings.rag_top_k == 6


def test_snippet_flags_map_onto_settings():
    settings = cli.settings_from_args(
        cli.parse_args(["--no-snippets", "--snippet-db-url", "postgresql+psycopg://r@h/s", "--snippets-top-k", "2"])
    )
    assert settings.snippets_enabled is False
    assert settings.snippet_db_url == "postgresql+psycopg://r@h/s"
    assert settings.snippets_top_k == 2


def test_no_rag_flag_disables_retrieval():
    settings = cli.settings_from_args(cli.parse_args(["--no-rag", "q"]))
    assert settings.rag_enabled is False


def test_rag_is_on_by_default(monkeypatch):
    monkeypatch.delenv("RAG_ENABLED", raising=False)
    settings = cli.settings_from_args(cli.parse_args(["q"]))
    assert settings.rag_enabled is True


# ---------------------------------------------------------------------------
# format_rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "setting"),
    [
        ("--rag", "RAG_ENABLED"),
        ("--examples", "EXAMPLES_ENABLED"),
        ("--multi-shot", "MULTI_SHOT_ENABLED"),
        ("--snippets", "SNIPPETS_ENABLED"),
    ],
)
@pytest.mark.parametrize("value", ["true", "false", None])
def test_each_switchs_help_says_the_default_the_parser_really_has(monkeypatch, flag, setting, value):
    """`--multi-shot` said "(default: off)" from v3 until 5.5.1, while the
    setting behind it defaulted on from the same commit."""
    if value is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, value)
    parsers: list[argparse.ArgumentParser] = []
    parse = argparse.ArgumentParser.parse_args

    def keep(self, *args, **kwargs):
        parsers.append(self)
        return parse(self, *args, **kwargs)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", keep)
    cli.parse_args([])
    [action] = [a for a in parsers[0]._actions if flag in a.option_strings]
    assert action.help.endswith(f"(default: {'on' if action.default else 'off'})")
    assert action.default is (value != "false")


def test_format_rows_empty():
    assert cli.format_rows({"columns": ["a"], "rows": [], "truncated": False}) == "(no rows)"


def test_format_rows_aligns_columns_and_handles_null():
    result = {"columns": ["id", "name"], "rows": [[1, "Alice"], [222, None]], "truncated": False}
    text = cli.format_rows(result)
    lines = text.splitlines()
    header_id_field = lines[0].split(" | ")[0]
    assert header_id_field.rstrip() == "id"
    assert len(header_id_field) == len("222")  # left-padded to the widest value in that column
    assert "NULL" in text
    assert "truncated" not in text


def test_format_rows_notes_truncation():
    result = {"columns": ["id"], "rows": [[1]], "truncated": True}
    text = cli.format_rows(result)
    assert "truncated at 1 rows" in text


# ---------------------------------------------------------------------------
# answer()
# ---------------------------------------------------------------------------


def test_answer_prints_rows_and_returns_zero(capsys):
    agent = StubAgent({"result": {"columns": ["n"], "rows": [[1]], "truncated": False}})
    code = cli.answer(agent, "how many?", as_json=False, quiet=True)
    assert code == 0
    out = capsys.readouterr().out
    assert "n" in out
    assert agent.questions == ["how many?"]


def test_answer_reports_error_on_stderr_and_returns_one(capsys):
    agent = StubAgent({"error": "boom"})
    code = cli.answer(agent, "q", as_json=False, quiet=True)
    assert code == 1
    err = capsys.readouterr().err
    assert "boom" in err


def test_answer_json_mode_emits_full_state_and_error_flag(capsys):
    agent = StubAgent(
        {
            "selected_tables": ["dim_store"],
            "sql": "SELECT 1",
            "knowledge_chunks": [
                {"chunk_id": "biz:1", "source_doc": "business_index", "heading_path": "h", "distance": 0.2}
            ],
            "result": {"columns": ["n"], "rows": [[1]], "row_count": 1, "truncated": False},
        }
    )
    code = cli.answer(agent, "q", as_json=True, quiet=False)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sql"] == "SELECT 1"
    assert payload["selected_tables"] == ["dim_store"]
    assert payload["error"] is None
    # Retrieval provenance travels with the answer, so a result can be traced
    # back to the chunks that shaped it.
    assert payload["knowledge_chunks"][0]["chunk_id"] == "biz:1"
    # v3 carried a single retrieval_error; v4 has retrievers that fail
    # independently, so the provenance is a dict keyed by which one.
    assert payload["retrieval_errors"] == {}
    assert payload["snippet_hits"] == []
    # arch5: what a complete answer was held to, and what it assumed.
    assert payload["answer_contract"] is None
    assert payload["completeness"] is None
    assert payload["assumptions"] == []


def test_answer_json_mode_returns_one_on_error(capsys):
    agent = StubAgent({"error": "nope"})
    code = cli.answer(agent, "q", as_json=True, quiet=False)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "nope"


# ---------------------------------------------------------------------------
# main(): the LlmUnavailableError -> exit code 2 fail-fast path
# ---------------------------------------------------------------------------


def test_main_exits_two_and_prints_a_clean_message_when_the_llm_is_unavailable(monkeypatch, capsys):
    def fake_init(self, settings, **kwargs):
        raise LlmUnavailableError("Cannot reach Ollama at http://bad:11434.")

    from nl2sql_agent.graph import Nl2SqlAgent

    # The pipeline's own constructor, which the ensemble builds too.
    monkeypatch.setattr(Nl2SqlAgent, "__init__", fake_init)

    code = cli.main(["irrelevant question"])

    assert code == 2
    err = capsys.readouterr().err
    assert "error: Cannot reach Ollama" in err


# ---------------------------------------------------------------------------
# main(): question mode, interactive mode, and progress reporting
# ---------------------------------------------------------------------------


class _StubAgentFactory:
    """Replaces `build_agent` so main() can be driven without a model or database."""

    def __init__(self, state: dict | None = None, tracer=None):
        self.state = state or {"result": {"columns": ["n"], "rows": [[1]], "truncated": False}}
        self.questions: list[str] = []
        self.on_progress = None
        self._tracer = tracer

    def __call__(self, settings, on_progress=None):
        from types import SimpleNamespace

        from nl2sql_agent.router import build_table
        from nl2sql_agent.tracing import Tracer

        self.settings = settings
        self.on_progress = on_progress
        self.router = SimpleNamespace(table=build_table(settings))
        self.tracer = self._tracer or Tracer(settings)
        return self

    def run(self, question: str, *, principal: str | None = None) -> dict:
        self.questions.append(question)
        return self.state


def test_main_joins_argv_words_into_one_question(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    code = cli.main(["how", "many", "stores"])
    assert code == 0
    assert factory.questions == ["how many stores"]


def test_main_returns_one_when_the_agent_reports_an_error(monkeypatch, capsys):
    factory = _StubAgentFactory({"error": "gave up"})
    monkeypatch.setattr(cli, "build_agent", factory)
    assert cli.main(["q"]) == 1


def test_progress_lines_go_to_stderr_with_friendly_labels(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    cli.main(["q"])
    factory.on_progress("retrieve_knowledge", "12 chunk(s)")
    factory.on_progress("retrieve_schema", "dim_store")
    factory.on_progress("planner_gate", "cost 1,024.00")
    err = capsys.readouterr().err
    assert "[knowledge] 12 chunk(s)" in err
    assert "[tables] dim_store" in err
    assert "[planner] cost 1,024.00" in err


@pytest.mark.parametrize("flag", ["--quiet", "--json"])
def test_progress_is_suppressed_in_quiet_and_json_modes(monkeypatch, capsys, flag):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    cli.main([flag, "q"])
    capsys.readouterr()  # drop the answer itself
    factory.on_progress("retrieve_knowledge", "12 chunk(s)")
    assert capsys.readouterr().err == ""


def test_interactive_mode_announces_the_knowledge_base(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    code = cli.main(["--vector-db-url", "postgresql+psycopg://v:v@vhost/vectors"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Knowledge base:" in out
    assert "vhost/vectors" in out
    assert "Ctrl-D to exit" in out


def test_interactive_mode_omits_the_knowledge_line_when_rag_is_off(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    cli.main(["--no-rag"])

    out = capsys.readouterr().out
    assert "Knowledge base:" not in out


def test_interactive_mode_names_the_example_store_only_when_examples_are_on(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    cli.main([])
    assert "Worked examples:" in capsys.readouterr().out

    cli.main(["--no-examples"])
    out = capsys.readouterr().out
    assert "Worked examples:" not in out
    assert "Ctrl-D to exit" in out


def test_interactive_mode_names_the_snippet_store_only_when_snippets_are_on(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    cli.main(["--snippets-top-k", "4"])
    out = capsys.readouterr().out
    assert "SQL snippets: by keyword phrase and by meaning" in out
    assert "up to 4 shown when their tables are in scope" in out

    cli.main(["--no-snippets"])
    assert "SQL snippets:" not in capsys.readouterr().out


def test_interactive_mode_answers_each_question_and_skips_blank_input(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)

    answers = iter(["  how many stores ", "   ", "and departments?"])

    def fake_input(_prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    assert cli.main([]) == 0
    assert factory.questions == ["how many stores", "and departments?"]


def test_interactive_mode_exits_cleanly_on_keyboard_interrupt(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.main([]) == 0


def test_the_module_entry_point_runs_when_executed_directly():
    """`python -m nl2sql_agent` is how the container's ENTRYPOINT starts, so the
    `if __name__ == "__main__"` guard is real code on the only path that
    matters -- and the one path pytest never takes by itself.
    """
    import runpy
    import sys
    import warnings

    argv = sys.argv
    sys.argv = ["nl2sql-agent", "--help"]
    try:
        with warnings.catch_warnings():
            # runpy re-executes a module already imported by the suite; that is
            # exactly the point here and its warning about it is not actionable.
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit) as exit_info:
                runpy.run_module("nl2sql_agent.__main__", run_name="__main__")
    finally:
        sys.argv = argv
    assert exit_info.value.code == 0


# ---------------------------------------------------------------------------
# Rendering the v4 state
# ---------------------------------------------------------------------------


def test_a_refusal_prints_the_answer_because_there_are_no_rows_to_print():
    """The Supervisor stops the run before any SQL, so the answer is all
    there is. Falling through to the table renderer would print "(no rows)"
    and lose the explanation.
    """
    agent = StubAgent({"answer": "I can't answer that: it is outside this data.", "result": None})
    assert cli.answer(agent, "q", as_json=False, quiet=True) == 0


def test_a_run_with_neither_answer_nor_rows_says_so_rather_than_printing_nothing(capsys):
    agent = StubAgent({"result": None})
    cli.answer(agent, "q", as_json=False, quiet=True)
    assert "(no answer)" in capsys.readouterr().out


def test_the_narrative_is_printed_above_the_table(capsys):
    """The sentence is the answer; the rows are the evidence for it."""
    from nl2sql_agent.state import QueryResult

    agent = StubAgent(
        {
            "narrative": "There are 10 stores.",
            "result": QueryResult(columns=["n"], rows=[[10]]),
        }
    )
    cli.answer(agent, "q", as_json=False, quiet=True)
    out = capsys.readouterr().out
    assert out.index("There are 10 stores.") < out.index("n")


def test_a_result_dataclass_renders_the_same_table_as_a_dict():
    """The benchmark and older callers hand over plain dictionaries; the v4
    pipeline carries a dataclass. Both have to render.
    """
    from nl2sql_agent.state import QueryResult

    as_dict = cli.format_rows({"columns": ["n"], "rows": [[10]], "truncated": False})
    as_object = cli.format_rows(QueryResult(columns=["n"], rows=[[10]]))
    assert as_dict == as_object


def test_no_result_at_all_renders_as_no_rows():
    assert cli.format_rows(None) == "(no rows)"


def test_one_question_says_in_one_line_which_models_it_may_be_routed_to(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    assert cli.main(["how", "many", "stores"]) == 0
    err = capsys.readouterr().err
    assert "[routing] on: 1 model(s), anchor qwen3.8-256k:latest" in err


@pytest.mark.parametrize("flag", ["--quiet", "--json"])
def test_the_routing_line_is_left_out_where_progress_is(monkeypatch, capsys, flag):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    cli.main(["q", flag])
    assert "[routing]" not in capsys.readouterr().err


def test_interactive_mode_prints_the_whole_routing_table(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "model routing on: 1 model(s), anchor qwen3.8-256k:latest" in out
    assert "  generator  light qwen3.8-256k:latest" in out
    assert "note: no catalog (MODEL_CATALOG)" in out


def test_a_routing_configuration_that_cannot_be_used_is_an_error_not_a_traceback(monkeypatch, capsys):
    from nl2sql_agent.router import RoutingError

    def refuse(settings, on_progress=None):
        raise RoutingError("the catalog describes http://elsewhere:11434")

    monkeypatch.setattr(cli, "build_agent", refuse)
    assert cli.main(["q"]) == 2
    assert "error: the catalog describes http://elsewhere:11434" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


def _tracer(*, answers: bool = True):
    from nl2sql_agent.config import Settings
    from nl2sql_agent.tracing import Tracer

    from tests.fake_mlflow import FakeMlflow

    def probe(uri):
        if not answers:
            raise ConnectionRefusedError("connection refused")

    return Tracer(
        Settings(mlflow_tracking_uri="http://nl2sql-mlflow:5000"), client=FakeMlflow(), probe=probe
    )


def test_one_question_says_where_its_trace_goes(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory(tracer=_tracer()))
    assert cli.main(["q"]) == 0
    err = capsys.readouterr().err
    assert "[tracing] tracing to http://nl2sql-mlflow:5000, experiment nl2sql-agent" in err


def test_one_question_says_when_it_will_not_be_traced(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory(tracer=_tracer(answers=False)))
    assert cli.main(["q"]) == 0
    err = capsys.readouterr().err
    assert (
        "[tracing] MLflow at http://nl2sql-mlflow:5000 did not answer (connection refused); "
        "runs are not traced"
    ) in err


@pytest.mark.parametrize("flag", ["--quiet", "--json"])
def test_the_tracing_line_is_left_out_where_progress_is(monkeypatch, capsys, flag):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory(tracer=_tracer()))
    cli.main(["q", flag])
    assert "[tracing]" not in capsys.readouterr().err


def test_with_tracing_unset_the_cli_says_nothing_about_it(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    cli.main(["q"])
    cli.main([])
    captured = capsys.readouterr()
    assert "[tracing]" not in captured.err
    assert "Traces:" not in captured.out


def test_interactive_mode_says_where_traces_go(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory(tracer=_tracer()))
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert cli.main([]) == 0
    assert "Traces: tracing to http://nl2sql-mlflow:5000, experiment nl2sql-agent" in capsys.readouterr().out


def test_a_question_asked_here_is_tagged_as_the_clis(capsys):
    from nl2sql_agent import tracing

    seen = []

    class Recording(StubAgent):
        def run(self, question, **kwargs):
            seen.append(dict(tracing._tags.get()))
            return super().run(question, **kwargs)

    cli.answer(Recording({"result": None, "answer": "ok"}), "q", as_json=False, quiet=True)
    assert seen == [{"nl2sql.entrypoint": "cli"}]


def test_json_mode_names_the_trace(capsys):
    cli.answer(StubAgent({"sql": "SELECT 1", "trace_id": "tr-42"}), "q", as_json=True, quiet=False)
    assert json.loads(capsys.readouterr().out)["trace_id"] == "tr-42"


# ---------------------------------------------------------------------------
# The ensemble (arch7): its four flags, its progress and its record
# ---------------------------------------------------------------------------


def test_the_ensembles_flags_reach_the_settings_and_default_to_the_environment(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_PARAPHRASES", "5")
    monkeypatch.setenv("OLLAMA_PARALLEL_CALLS", "2")
    defaults = cli.settings_from_args(cli.parse_args(["q"]))
    assert (defaults.ensemble_enabled, defaults.ensemble_paraphrases, defaults.ollama_parallel_calls) == (True, 5, 2)
    assert defaults.ensemble_fuse_columns is True

    flagged = cli.settings_from_args(
        cli.parse_args(["q", "--no-ensemble", "--paraphrases", "4", "--parallel-calls", "3", "--no-fuse-columns"])
    )
    assert (flagged.ensemble_enabled, flagged.ensemble_paraphrases, flagged.ollama_parallel_calls) == (False, 4, 3)
    assert flagged.ensemble_fuse_columns is False


def test_no_ensemble_builds_the_pipeline_alone(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    cli.main(["q", "--no-ensemble"])
    assert factory.settings.ensemble_enabled is False


@pytest.mark.parametrize(
    ("argv", "named"),
    [(["q", "--paraphrases", "2"], "ENSEMBLE_PARAPHRASES is 2; it must be 3 to 10"),
     (["q", "--parallel-calls", "0"], "OLLAMA_PARALLEL_CALLS is 0; it must be at least 1")],
)
def test_a_flag_out_of_range_is_named_with_its_bound_not_a_traceback(monkeypatch, capsys, argv, named):
    monkeypatch.setattr(cli, "build_agent", _StubAgentFactory())
    assert cli.main(argv) == 2
    assert f"error: {named}" in capsys.readouterr().err


def test_a_setting_out_of_range_in_the_environment_stops_the_cli_the_same_way(monkeypatch, capsys):
    monkeypatch.setenv("ENSEMBLE_WAVES", "0")
    assert cli.main(["q"]) == 2
    assert "error: ENSEMBLE_WAVES is 0; it must be at least 1" in capsys.readouterr().err


def test_a_candidates_progress_lines_say_which_run_and_the_ensembles_own_do_not(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "build_agent", factory)
    cli.main(["q"])
    factory.on_progress("plan_wave", "wave 1: the original")
    factory.on_progress("generate_sql", "SELECT 1", candidate=0)
    factory.on_progress("deliver", "single: the original's run")
    err = capsys.readouterr().err
    assert "[wave] wave 1: the original" in err
    assert "[0] [sql] SELECT 1" in err
    assert "\n[answer] single: the original's run" in err


def test_json_mode_carries_the_ensembles_record_and_the_delivered_runs_own_fields(capsys):
    from nl2sql_agent.ensemble_state import Agreement, Candidate, Decision, new_ensemble_state
    from nl2sql_agent.state import TraceEntry, new_state

    run = {**new_state("q"), "attempts": 2, "selected_tables": ["dim_store"], "sql": "SELECT 1",
           "node_errors": {"narrator": "timed out"}, "trace": [TraceEntry(node="finish")]}
    state = {
        **new_ensemble_state("q"),
        "candidates": [Candidate(index=0, wording="q", origin="original", wave=1, state=run, outcome="answered")],
        "agreement": Agreement(admissible=1, agreed=1, total=1, level="single"),
        "decision": Decision(chosen=0, fused_from=[0]),
        "sql": "SELECT 1",
        "node_errors": {"supervisor": "down"},
        "trace": [TraceEntry(node="screen")],
    }
    assert cli.answer(StubAgent(state), "q", as_json=True, quiet=False) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["attempts"] == 2 and document["selected_tables"] == ["dim_store"]
    assert document["node_errors"] == {"narrator": "timed out", "supervisor": "down"}
    assert [entry["node"] for entry in document["trace"]] == ["screen", "finish"]
    assert document["ensemble"]["agreement"]["level"] == "single"
    assert document["ensemble"]["candidates"][0]["state"]["sql"] == "SELECT 1"


def test_json_mode_for_one_run_has_no_ensemble_record(capsys):
    cli.answer(StubAgent({"sql": "SELECT 1", "trace": []}), "q", as_json=True, quiet=False)
    assert "ensemble" not in json.loads(capsys.readouterr().out)


def test_an_ensemble_answer_is_printed_under_how_its_runs_agreed(capsys):
    from nl2sql_agent.ensemble_state import Decision

    state = {"result": {"columns": ["n"], "rows": [[10]], "truncated": False}, "narrative": "There are 10.",
             "decision": Decision(chosen=0, line="Agreed by 4 of 4 independent runs of the question, each worded differently.")}
    assert cli.answer(StubAgent(state), "q", as_json=False, quiet=True) == 0
    out = capsys.readouterr().out
    assert out.startswith("Agreed by 4 of 4 independent runs of the question, each worded differently.\n\nThere are 10.")
