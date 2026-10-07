"""A connection that records what the one-shot sends, and answers its reads.

Statements are kept as Postgres receives them (a `psycopg.sql` composition
rendered, or the string as it was); a read is answered by the first entry in
`answers` whose key is part of its text. That Postgres accepts all of it is
tests/ops/test_ops_live.py.
"""

from __future__ import annotations

from contextlib import contextmanager


class Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, answers=None, fail=None):
        self.answers = dict(answers or {})
        #: A substring, and the exception a statement containing it raises.
        self.fail = dict(fail or {})
        self.statements: list[str] = []
        self.params: list[object] = []
        self.transactions = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(None)
        for needle, error in self.fail.items():
            if needle in text:
                raise error
        self.statements.append(" ".join(text.split()))
        self.params.append(params)
        for needle, rows in self.answers.items():
            if needle in text:
                return Rows(rows(params) if callable(rows) else rows)
        return Rows([])

    def ran(self, needle: str) -> list[str]:
        return [statement for statement in self.statements if needle in statement]

