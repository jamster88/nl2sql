"""`python -m nl2sql_ops`: what the dbprep service runs, and how it fails."""

from __future__ import annotations

import runpy
import sys

import psycopg
import pytest

from nl2sql_ops import __main__ as cli
from nl2sql_ops.connect import SOCKET_FILE, connect, listening
from nl2sql_ops.hba import HbaError

from .conftest import FakeConn

ENV = {
    "POSTGRES_READER_PASSWORD": "reader-pw",
    "AUTH_ROLESYNC_PASSWORD": "sync-pw",
    "FEEDBACK_DB_PASSWORD": "fb",
    "CORRECTIONS_DB_PASSWORD": "co",
    "COMPLETIONS_DB_PASSWORD": "cm",
    "SNIPPETS_DB_PASSWORD": "sn",
    "CONTEXT_DB_PASSWORD": "cx",
    "VECTOR_DB_PASSWORD": "vx",
    "MLFLOW_DB_PASSWORD": "ml",
}


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for name in ("AUTH_ENABLED", "NL2SQL_SOCKETS_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


class Wired:
    """Which sockets are up, the fake behind each, and every connection made."""

    def __init__(self, monkeypatch, up=("retail", "stores", "context", "vector", "mlflow"), conns=None):
        self.conns = conns or {}
        self.opened: list[tuple[str, str, str]] = []
        self.hba: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(cli, "listening", lambda sockets, name: name in up)
        monkeypatch.setattr(cli, "connect", self.connect)
        for module in ("nl2sql_ops.retail", "nl2sql_ops.stores"):
            monkeypatch.setattr(f"{module}.rewrite", lambda conn, begin, end, lines: self.hba.append((begin, lines)))

    def connect(self, sockets, name, *, user, dbname):
        self.opened.append((name, user, dbname))
        return self.conns.setdefault((name, dbname), FakeConn({"FROM pg_roles": [(1,)]}))


def test_prepare_makes_every_running_database_what_the_settings_say(monkeypatch, capsys):
    wired = Wired(monkeypatch)
    assert cli.main(["prepare"]) == 0
    assert wired.opened == [
        ("retail", "postgres", "nl2sql_retail"),
        ("stores", "postgres", "postgres"),
        ("stores", "postgres", "nl2sql_corrections"),
        ("stores", "postgres", "nl2sql_completions"),
        ("stores", "postgres", "nl2sql_snippets"),
        ("context", "ragproc", "nl2sql_chunks"),
        ("vector", "ragproc", "nl2sql_vectors"),
        ("mlflow", "mlflow", "mlflow"),
    ]
    assert [begin for begin, _ in wired.hba] == ["# BEGIN nl2sql sign-in", "# BEGIN nl2sql transport"]
    out = capsys.readouterr().out
    assert "INFO a person signs in with their directory password, which Postgres checks itself" in out
    assert "INFO the runtime stores: feedback (nl2sql_feedback), corrections (nl2sql_corrections)" in out
    assert "INFO mlflow store: mlflow's password is its secret" in out
    retail = wired.conns[("retail", "nl2sql_retail")]
    assert "ALTER ROLE \"nl2sql_reader\" PASSWORD 'reader-pw'" in retail.statements
    assert retail.ran("nl2sql_rolesync"), "sign-in is on by default"


def test_the_default_command_is_prepare(monkeypatch):
    Wired(monkeypatch, up=("retail",))
    assert cli.main([]) == 0


def test_sign_in_off_takes_the_rules_out_and_makes_no_sync(monkeypatch, capsys):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    wired = Wired(monkeypatch, up=("retail",))
    assert cli.main(["prepare"]) == 0
    assert wired.hba == [("# BEGIN nl2sql sign-in", [])]
    assert not wired.conns[("retail", "nl2sql_retail")].ran("nl2sql_rolesync")
    out = capsys.readouterr().out
    assert "sign-in is off (AUTH_ENABLED=false)" in out
    assert "INFO the runtime stores are not running, so they were left as they are" in out


def test_a_store_without_a_password_or_a_role_is_passed_over(monkeypatch, capsys):
    monkeypatch.setenv("CONTEXT_DB_PASSWORD", "")
    wired = Wired(monkeypatch, up=("retail", "context", "vector"),
                  conns={("vector", "nl2sql_vectors"): FakeConn()})
    assert cli.main(["prepare"]) == 0
    assert ("context", "ragproc", "nl2sql_chunks") not in wired.opened
    assert "vector store" not in capsys.readouterr().out, "no such role there"


def test_a_missing_pg_trgm_is_a_warning_not_a_failure(monkeypatch, capsys):
    conn = FakeConn({"FROM pg_roles": [(1,)]}, fail={"pg_trgm": psycopg.errors.InsufficientPrivilege("no")})
    Wired(monkeypatch, up=("retail",), conns={("retail", "nl2sql_retail"): conn})
    assert cli.main(["prepare"]) == 0
    assert "WARN could not create pg_trgm" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("up", "env", "message"),
    [
        ((), {}, "the retail database's socket is not in"),
        (("retail",), {"AUTH_ROLESYNC_PASSWORD": ""}, "AUTH_ROLESYNC_PASSWORD's secret is empty"),
        (("retail", "stores"), {"FEEDBACK_DB_PASSWORD": ""}, "the feedback store has no password"),
    ],
)
def test_what_is_missing_fails_the_one_shot_and_says_what(monkeypatch, capsys, up, env, message):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    Wired(monkeypatch, up=up)
    assert cli.main(["prepare"]) == 1
    assert message in capsys.readouterr().err


def test_rules_that_did_not_parse_fail_the_one_shot(monkeypatch, capsys):
    Wired(monkeypatch, up=("retail",))

    def broken(conn, begin, end, lines):
        raise HbaError("the new rules did not parse")

    monkeypatch.setattr("nl2sql_ops.retail.rewrite", broken)
    assert cli.main(["prepare"]) == 1
    assert "dbprep: the new rules did not parse" in capsys.readouterr().err


@pytest.mark.parametrize(("error", "said"), [
    (psycopg.errors.InsufficientPrivilege("permission denied for x\nDETAIL: y"),
     "dbprep: InsufficientPrivilege: permission denied for x"),
    (psycopg.errors.InsufficientPrivilege(""), "dbprep: InsufficientPrivilege"),
])
def test_a_database_error_fails_the_one_shot_with_its_first_line(monkeypatch, capsys, error, said):
    conn = FakeConn(fail={"CREATE ROLE": error})
    Wired(monkeypatch, up=("retail",), conns={("retail", "nl2sql_retail"): conn})
    assert cli.main(["prepare"]) == 1
    assert capsys.readouterr().err.strip() == said


def test_report_and_snippets_print_what_their_modules_say(monkeypatch, capsys):
    monkeypatch.setattr(cli, "report", lambda settings: ["STEP one", "INFO two"])
    monkeypatch.setattr(cli, "snippets_state", lambda settings: "behind")
    assert cli.main(["report"]) == 0 and capsys.readouterr().out == "STEP one\nINFO two\n"
    assert cli.main(["snippets"]) == 0 and capsys.readouterr().out == "behind\n"


def test_listening_is_the_socket_being_in_its_directory(tmp_path):
    (tmp_path / "retail").mkdir()
    assert not listening(tmp_path, "retail")
    (tmp_path / "retail" / SOCKET_FILE).touch()
    assert listening(tmp_path, "retail")


def test_connect_goes_through_the_socket_directory_in_autocommit(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: seen.update(kwargs) or "conn")
    assert connect(tmp_path, "stores", user="postgres", dbname="postgres") == "conn"
    assert seen == {"host": str(tmp_path / "stores"), "port": 5432, "user": "postgres", "dbname": "postgres",
                    "autocommit": True, "connect_timeout": 10, "application_name": "nl2sql:dbprep"}


def test_the_module_runs_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nl2sql_ops", "snippets"])
    monkeypatch.setattr("nl2sql_ops.report.snippets_state", lambda settings: "unknown")
    with pytest.raises(SystemExit) as exit_:
        runpy.run_module("nl2sql_ops", run_name="__main__", alter_sys=True)
    assert exit_.value.code == 0
