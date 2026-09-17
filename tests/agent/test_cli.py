"""nl2sql_agent.__main__: argument parsing, row formatting, and the
answer()/main() glue -- exercised without a real Agent (answer() is given a
tiny stub with the same .run() contract; main()'s LlmUnavailableError path is
exercised against Nl2SqlAgent construction directly).
"""

from __future__ import annotations

import json

from nl2sql_agent import __main__ as cli
from nl2sql_agent.llm import LlmUnavailableError


class StubAgent:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.questions: list[str] = []

    def run(self, question: str) -> dict:
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
    assert settings.max_sql_attempts == 2
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
    assert payload["retrieval_error"] is None


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
