"""nl2sql_common.roles: what a login role may cost the database (V6-39).

The statements are checked as Postgres receives them; that Postgres accepts
them, from a role that is not a superuser, is tests/ops/test_ops_live.py.
"""

from __future__ import annotations

from nl2sql_common import roles
from nl2sql_common.roles import RoleLimits, limit_role


class Recorder:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, query, params=None):
        self.statements.append(query.as_string(None))


def test_every_limit_is_set_on_the_role_with_its_name_quoted():
    conn = Recorder()
    limit_role(conn, "nl2sql_reader", roles.READER)
    assert conn.statements == [
        'ALTER ROLE "nl2sql_reader" CONNECTION LIMIT 60',
        'ALTER ROLE "nl2sql_reader" SET "statement_timeout" = \'120000\'',
        'ALTER ROLE "nl2sql_reader" SET "work_mem" = \'16MB\'',
        'ALTER ROLE "nl2sql_reader" SET "idle_in_transaction_session_timeout" = \'60000\'',
    ]


def test_a_limit_left_out_is_left_as_the_role_has_it():
    conn = Recorder()
    limit_role(conn, "snippets", roles.STORE_OWNER)
    assert conn.statements == [
        'ALTER ROLE "snippets" CONNECTION LIMIT 20',
        'ALTER ROLE "snippets" SET "work_mem" = \'16MB\'',
    ]
    nothing = Recorder()
    limit_role(nothing, "anyone", RoleLimits())
    assert nothing.statements == []


def test_the_settings_are_compared_as_postgres_stores_them():
    limits = RoleLimits(statement_timeout_ms=5000, work_mem="8MB", connection_limit=3)
    held = ["default_transaction_read_only=on", "statement_timeout=5000", "work_mem=8MB"]
    assert not limits.differ(held, 3)
    assert limits.differ(held, -1), "-1, no limit, is a difference"
    assert limits.differ(["statement_timeout=5000"], 3), "work_mem missing"
    assert limits.differ(["statement_timeout=6000", "work_mem=8MB"], 3)
    assert limits.differ(None, 3)
    assert not RoleLimits(work_mem="8MB").differ(["work_mem=8MB"], 99), "no connection limit asked for"


def test_each_service_role_has_every_limit_the_review_asked_for():
    """X8: statement_timeout, work_mem, idle_in_transaction_session_timeout and
    CONNECTION LIMIT on the reader, the snippet reader and the feedback writer;
    an owner, which embeds inside its transactions, has no idle limit."""
    for limits in (roles.READER, roles.SNIPPETS_READER, roles.FEEDBACK_WRITER, roles.ROLESYNC):
        assert set(limits.settings()) == set(roles.SETTINGS)
        assert limits.connection_limit and limits.connection_limit > 0
    assert roles.STORE_OWNER.idle_in_transaction_ms is None
    assert roles.READER.statement_timeout_ms > 30_000, "above the agent's own STATEMENT_TIMEOUT_MS"
