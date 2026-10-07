"""nl2sql_common.env: settings read the same way by every service."""

from __future__ import annotations

import pytest

from nl2sql_common import env


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_empty_and_blank_are_all_unset(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("NL2SQL_TEST", raising=False)
    else:
        monkeypatch.setenv("NL2SQL_TEST", raw)
    assert env.env("NL2SQL_TEST") is None
    assert env.env_str("NL2SQL_TEST", "d") == "d"
    assert env.env_int("NL2SQL_TEST", 7) == 7
    assert env.env_float("NL2SQL_TEST", 1.5) == 1.5
    assert env.env_bool("NL2SQL_TEST", True) is True
    assert env.env_tuple("NL2SQL_TEST", ("a",)) == ("a",)


def test_a_value_is_stripped_and_converted(monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST", " 42 ")
    assert env.env("NL2SQL_TEST") == "42"
    assert env.env_str("NL2SQL_TEST", "d") == "42"
    assert env.env_int("NL2SQL_TEST", 0) == 42
    assert env.env_float("NL2SQL_TEST", 0.0) == 42.0


@pytest.mark.parametrize("raw,expected", [("1", True), ("YES", True), ("on", True), ("true", True),
                                          ("false", False), ("0", False), ("maybe", False)])
def test_a_switch_is_on_only_when_it_says_so(monkeypatch, raw, expected):
    monkeypatch.setenv("NL2SQL_TEST", raw)
    assert env.env_bool("NL2SQL_TEST", not expected) is expected


def test_a_list_drops_its_blanks(monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST", " a, ,b ,")
    assert env.env_tuple("NL2SQL_TEST", ()) == ("a", "b")


def test_a_secret_file_wins_over_the_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("NL2SQL_SECRET", "from-env")
    assert env.secret("NL2SQL_SECRET") == "from-env"
    path = tmp_path / "secret"
    path.write_text("from-file\n")
    monkeypatch.setenv("NL2SQL_SECRET_FILE", str(path))
    assert env.secret("NL2SQL_SECRET") == "from-file"
    path.write_text("\n")
    assert env.secret("NL2SQL_SECRET") is None


# --- a URL with its password beside it (6.3, V6-38) ----------------------------------------


@pytest.fixture
def no_db_settings(monkeypatch):
    for name in ("NL2SQL_DB_URL", "NL2SQL_DB_URL_FILE", "NL2SQL_DB_PASSWORD", "NL2SQL_DB_PASSWORD_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_a_url_takes_its_password_from_the_file_beside_it(monkeypatch, tmp_path, no_db_settings):
    """Compose gives a service a URL that names who and where, and the
    password as a file: in the environment, `docker inspect` shows it."""
    password = tmp_path / "password"
    password.write_text("p@ss/word\n")
    monkeypatch.setenv("NL2SQL_DB_URL", "postgresql://reader@db:5432/retail")
    monkeypatch.setenv("NL2SQL_DB_PASSWORD_FILE", str(password))
    assert env.env_url("NL2SQL_DB_URL") == "postgresql://reader:p%40ss%2Fword@db:5432/retail"


def test_the_file_replaces_a_password_the_url_already_has(monkeypatch, tmp_path, no_db_settings):
    password = tmp_path / "password"
    password.write_text("new")
    monkeypatch.setenv("NL2SQL_DB_PASSWORD_FILE", str(password))
    assert env.env_url("NL2SQL_DB_URL", "postgresql://reader:old@db/retail") == "postgresql://reader:new@db/retail"


def test_without_a_password_the_url_is_as_given(monkeypatch, tmp_path, no_db_settings):
    """For a service started by hand: a URL with its password in it, and no
    file. An empty file is no password, as an empty variable is no value."""
    assert env.env_url("NL2SQL_DB_URL") is None
    assert env.env_url("NL2SQL_DB_URL", "postgresql://u:p@h/d") == "postgresql://u:p@h/d"
    empty = tmp_path / "empty"
    empty.write_text("\n")
    monkeypatch.setenv("NL2SQL_DB_PASSWORD_FILE", str(empty))
    assert env.env_url("NL2SQL_DB_URL", "postgresql://u:p@h/d") == "postgresql://u:p@h/d"


def test_a_password_with_no_url_is_nothing(monkeypatch, no_db_settings):
    monkeypatch.setenv("NL2SQL_DB_PASSWORD", "orphan")
    assert env.env_url("NL2SQL_DB_URL") is None


def test_a_name_that_is_not_a_url_setting_takes_its_own_password(monkeypatch, no_db_settings):
    """The password's name is the URL's without `_URL`; a name without it is
    its own stem."""
    monkeypatch.setenv("NL2SQL_DB", "postgresql://u@h/d")
    monkeypatch.setenv("NL2SQL_DB_PASSWORD", "pw")
    assert env.env_url("NL2SQL_DB") == "postgresql://u:pw@h/d"


@pytest.mark.parametrize("url", ["postgresql://db:5432/retail", "postgresql://:x@db/retail", "not a url"])
def test_a_password_is_never_given_to_a_url_with_no_user(url):
    """Refused rather than sent: a password nobody would present, with the
    URL in the message and the password nowhere in it."""
    from nl2sql_common.urls import with_password

    with pytest.raises(ValueError, match="names no user") as raised:
        with_password(url, "s3cret")
    assert "s3cret" not in str(raised.value)
