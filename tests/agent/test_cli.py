"""nl2sql_agent.__main__: argument parsing, row formatting, and the
answer()/main() glue -- exercised without a real Agent (answer() is given a
tiny stub with the same .run() contract; main()'s LlmUnavailableError path is
exercised against Nl2SqlAgent construction directly).
"""

from __future__ import annotations

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
    # v3 carried a single retrieval_error; v4 has four retrievers that fail
    # independently, so the provenance is a dict keyed by which one.
    assert payload["retrieval_errors"] == {}


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
    def fake_init(self, settings, on_progress=None):
        raise LlmUnavailableError("Cannot reach Ollama at http://bad:11434.")

    monkeypatch.setattr(cli.Nl2SqlAgent, "__init__", fake_init)

    code = cli.main(["irrelevant question"])

    assert code == 2
    err = capsys.readouterr().err
    assert "error: Cannot reach Ollama" in err


# ---------------------------------------------------------------------------
# main(): question mode, interactive mode, and progress reporting
# ---------------------------------------------------------------------------


class _StubAgentFactory:
    """Replaces Nl2SqlAgent so main() can be driven without a model or database."""

    def __init__(self, state: dict | None = None):
        self.state = state or {"result": {"columns": ["n"], "rows": [[1]], "truncated": False}}
        self.questions: list[str] = []
        self.on_progress = None

    def __call__(self, settings, on_progress=None):
        self.settings = settings
        self.on_progress = on_progress
        return self

    def run(self, question: str, *, principal: str | None = None) -> dict:
        self.questions.append(question)
        return self.state


def test_main_joins_argv_words_into_one_question(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
    code = cli.main(["how", "many", "stores"])
    assert code == 0
    assert factory.questions == ["how many stores"]


def test_main_returns_one_when_the_agent_reports_an_error(monkeypatch, capsys):
    factory = _StubAgentFactory({"error": "gave up"})
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
    assert cli.main(["q"]) == 1


def test_progress_lines_go_to_stderr_with_friendly_labels(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
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
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
    cli.main([flag, "q"])
    capsys.readouterr()  # drop the answer itself
    factory.on_progress("retrieve_knowledge", "12 chunk(s)")
    assert capsys.readouterr().err == ""


def test_interactive_mode_announces_the_knowledge_base(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    code = cli.main(["--vector-db-url", "postgresql+psycopg://v:v@vhost/vectors"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Knowledge base:" in out
    assert "vhost/vectors" in out
    assert "Ctrl-D to exit" in out


def test_interactive_mode_omits_the_knowledge_line_when_rag_is_off(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError))

    cli.main(["--no-rag"])

    out = capsys.readouterr().out
    assert "Knowledge base:" not in out


def test_interactive_mode_answers_each_question_and_skips_blank_input(monkeypatch, capsys):
    factory = _StubAgentFactory()
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)

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
    monkeypatch.setattr(cli, "Nl2SqlAgent", factory)
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
