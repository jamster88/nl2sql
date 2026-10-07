"""nl2sql_common.errors and envelope: what failures are, and how they read."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import nl2sql_common
from nl2sql_common import envelope, errors

ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_families_hold_what_each_driver_raises():
    import httpx
    import psycopg
    import sqlalchemy.exc

    assert issubclass(psycopg.OperationalError, errors.DATABASE_ERRORS)
    assert issubclass(sqlalchemy.exc.OperationalError, errors.DATABASE_ERRORS)
    assert issubclass(ConnectionRefusedError, errors.DATABASE_ERRORS)
    assert issubclass(httpx.ConnectError, errors.NETWORK_ERRORS)
    assert issubclass(httpx.ConnectError, errors.MODEL_ERRORS)
    assert issubclass(json.JSONDecodeError, errors.PARSE_ERRORS)
    # What none of them is: a bug.
    for bug in (KeyError, NameError, AttributeError, TypeError):
        assert not issubclass(bug, errors.DATABASE_ERRORS + errors.MODEL_ERRORS)


def test_a_driver_this_image_does_not_have_is_left_out():
    assert errors._optional("no_such_module.Error", "json.JSONDecodeError") == [json.JSONDecodeError]
    assert errors._family([OSError], [OSError, ValueError]) == (OSError, ValueError)


def test_each_kind_of_failure_says_what_it_is():
    assert [errors.Unavailable.code, errors.Refused.code, errors.Invalid.code] == ["unavailable", "refused", "invalid"]
    assert issubclass(errors.Unavailable, errors.Nl2SqlError) and errors.Nl2SqlError.code == "error"


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ConnectionRefusedError("refused\n  by the host"), "ConnectionRefusedError: refused by the host"),
        (ValueError(), "ValueError"),
        (OSError("x" * 400), "OSError: " + "x" * 288 + "..."),
    ],
)
def test_a_failure_is_described_in_one_bounded_line(exc, expected):
    assert errors.described(exc) == expected


def test_the_envelope_is_one_shape():
    assert envelope.ApiError.of("not_found", "gone").model_dump() == {"error": {"code": "not_found", "message": "gone"}}
    assert envelope.ApiError.of("x", "y", why=1).error["detail"] == {"why": 1}
    with pytest.raises(ValueError):
        envelope.Health(version="1", uptime_seconds=0, surprise=True)
    assert envelope.Readiness(ready=True, checks={"db": envelope.Check(ok=True)}).warnings == []
    assert envelope.FALLBACK_CODES[503] == "unavailable"


def test_the_package_records_the_version_the_images_carry():
    project = tomllib.loads((ROOT / "common" / "pyproject.toml").read_text())["project"]
    assert project["name"] == "nl2sql-common"
    assert project["version"] == nl2sql_common.__version__
    from nl2sql_agent import __version__

    assert nl2sql_common.__version__ == __version__


def test_readiness_tells_an_operator_everything_and_anyone_else_what_is_up():
    from nl2sql_common.envelope import Check, Readiness

    full = Readiness(
        ready=False,
        checks={"database": Check(ok=False, detail="OperationalError: nl2sql-postgres:5432 refused"),
                "llm": Check(ok=True, detail="qwen at http://nl2sql-ollama:11434")},
        warnings=["API_TOKEN is not set"],
    )
    assert full.for_caller(operator=True) is full
    trimmed = full.for_caller(operator=False)
    assert trimmed.ready is False and trimmed.warnings == []
    assert trimmed.checks == {"database": Check(ok=False), "llm": Check(ok=True)}
