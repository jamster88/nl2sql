"""What Postgres is told about whose statement it runs (V6-64)."""

from __future__ import annotations

from nl2sql_common.attribution import APPLICATION_NAME_SQL, LIMIT, application_name


def test_the_name_says_the_service_and_the_person():
    assert application_name("console", "alice") == "nl2sql:console:alice"


def test_it_is_what_postgres_keeps_of_it():
    assert application_name("review", "zoë") == "nl2sql:review:zo?"
    assert len(application_name("agent", "x" * 100)) == LIMIT


def test_it_is_set_for_the_transaction_and_as_a_value():
    assert "set_config('application_name', %(name)s, true)" in APPLICATION_NAME_SQL
