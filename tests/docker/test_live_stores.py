"""tests/live_stores.py: where the live tests find the stack's databases.

Offline: these check the helper itself, which the live tests only reach with
a stack up -- and then only one way, since a stack is either up or not.
"""

from __future__ import annotations

import pytest

from tests import live_stores


#: The URL variable these tests pretend a live test reads. Not one a real
#: live test reads, so a run with the stack's URLs exported -- which is how
#: the live tests are pointed elsewhere -- does not change what this sees.
VARIABLE = "NL2SQL_LIVE_STORES_TEST_URL"


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    path = tmp_path / "secrets"
    path.mkdir()
    monkeypatch.setattr(live_stores, "SECRETS", path)
    return path


@pytest.fixture
def dotenv(tmp_path, monkeypatch, secrets):
    path = tmp_path / ".env"
    monkeypatch.setattr(live_stores, "DOTENV", path)
    for store in live_stores.STORES.values():
        for key, _ in (store.port, store.user, store.password, store.database):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(VARIABLE, raising=False)
    return path


def test_a_url_in_its_own_variable_is_used_as_it_is(dotenv, monkeypatch):
    monkeypatch.setenv(VARIABLE, "postgresql+psycopg://me:pw@elsewhere:1/db")
    assert live_stores.url("retail", variable=VARIABLE) == "postgresql+psycopg://me:pw@elsewhere:1/db"


def test_without_one_it_is_the_stacks_with_the_password_dotenv_holds(dotenv):
    dotenv.write_text("# setup.sh\nPOSTGRES_READER_PASSWORD=old\nPOSTGRES_READER_PASSWORD=a/b@c\nPOSTGRES_PORT=15432\n")
    assert live_stores.url("retail", variable=VARIABLE) == (
        "postgresql+psycopg://nl2sql_reader:a%2Fb%40c@localhost:15432/nl2sql_retail"
    )


def test_the_password_is_the_file_compose_mounts_before_any_left_in_dotenv(dotenv, secrets):
    """6.3 moved every password into secrets/; a .env from before may still
    name one, which the file the containers really read wins over."""
    dotenv.write_text("POSTGRES_READER_PASSWORD=stale\n")
    (secrets / "postgres_reader_password").write_text("from-file\n")
    assert live_stores.url("retail", variable=VARIABLE) == (
        "postgresql+psycopg://nl2sql_reader:from-file@localhost:5432/nl2sql_retail"
    )


def test_the_four_runtime_stores_are_one_port(dotenv, secrets):
    (secrets / "snippets_db_password").write_text("s")
    dotenv.write_text("STORES_DB_PORT=15435\n")
    assert live_stores.url("snippets", variable=VARIABLE, driver="postgresql") == (
        "postgresql://snippets:s@localhost:15435/nl2sql_snippets"
    )
    assert {store.port for name, store in live_stores.STORES.items()
            if name in ("feedback", "corrections", "completions", "snippets")} == {("STORES_DB_PORT", "5435")}


def test_the_shell_wins_over_dotenv_as_it_does_for_compose(dotenv, monkeypatch):
    dotenv.write_text("FEEDBACK_DB_PASSWORD=from-dotenv\n")
    monkeypatch.setenv("FEEDBACK_DB_PASSWORD", "from-shell")
    assert live_stores.url("feedback", variable=VARIABLE, driver="postgresql") == (
        "postgresql://feedback:from-shell@localhost:5435/nl2sql_feedback"
    )


def test_with_neither_it_is_the_defaults_compose_falls_back_to(dotenv):
    assert live_stores.url("retail_owner", variable=VARIABLE) == (
        "postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail"
    )


def test_a_password_is_never_printed(dotenv):
    assert live_stores.redacted("postgresql://u:secret@h:5/db") == "postgresql://u:***@h:5/db"
    assert live_stores.redacted("postgresql://u:secret@h/db") == "postgresql://u:***@h/db"
    assert live_stores.redacted("postgresql://h/db") == "postgresql://h/db"


def test_nothing_listening_is_a_skip():
    with pytest.raises(pytest.skip.Exception, match=r"no reachable Postgres at postgresql://u:\*\*\*@h/db: refused"):
        live_stores.unreachable("Postgres", "postgresql://u:secret@h/db", OSError("refused"))


@pytest.mark.parametrize(
    "said",
    ['password authentication failed for user "u"', "no pg_hba.conf entry for host", "pg_hba.conf rejects connection"],
)
def test_a_server_that_refuses_the_login_is_a_failure(said):
    with pytest.raises(pytest.fail.Exception, match="refused the login") as raised:
        live_stores.unreachable("Postgres", "postgresql://u:hunter2@h/db", OSError(said))
    assert "hunter2" not in str(raised.value)
