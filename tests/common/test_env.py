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
