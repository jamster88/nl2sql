"""What a login role may cost the database it connects to (V6-39).

People's roles had a statement timeout and a connection limit since 6.0;
the roles services log in as -- the agent's reader, the snippet store's
reader, the feedback writer, the stores' owners -- had none, so a runaway
query or a leaked pool on any of them was bounded only by the server. Each
now carries its own, set on the role, so every session that role opens
starts with them:

* `statement_timeout` -- the longest one statement may run. A backstop: the
  agent and the console set a shorter one on each transaction themselves.
* `work_mem` -- the memory one sort or hash may take before it spills to
  disk, set explicitly so it is a decision rather than the server's default.
* `idle_in_transaction_session_timeout` -- a transaction left open and idle
  is ended, so a client that died holding locks frees them.
* `CONNECTION LIMIT` -- how many sessions the role may hold at once.

All but the last are defaults a session may change with `SET`; the
validators in front of every role that runs a person's SQL refuse `SET`, so
for those they hold. `CONNECTION LIMIT` is enforced by the server.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import sql

#: The settings a limit is made of, by their Postgres names, in the order
#: they are applied and compared.
SETTINGS = ("statement_timeout", "work_mem", "idle_in_transaction_session_timeout")


@dataclass(frozen=True)
class RoleLimits:
    """Limits for one role. None leaves that limit as the role has it."""

    statement_timeout_ms: int | None = None
    work_mem: str | None = None
    idle_in_transaction_ms: int | None = None
    connection_limit: int | None = None

    def settings(self) -> dict[str, str]:
        """The `ALTER ROLE ... SET` values, as Postgres stores them in rolconfig."""
        values = {
            "statement_timeout": self.statement_timeout_ms,
            "work_mem": self.work_mem,
            "idle_in_transaction_session_timeout": self.idle_in_transaction_ms,
        }
        return {name: str(value) for name, value in values.items() if value is not None}

    def differ(self, config: list[str] | tuple[str, ...] | None, connection_limit: int) -> bool:
        """Whether a role with this `rolconfig` and `rolconnlimit` lacks any of these."""
        held = dict(entry.split("=", 1) for entry in (config or ()) if "=" in entry)
        if any(held.get(name) != value for name, value in self.settings().items()):
            return True
        return self.connection_limit is not None and connection_limit != self.connection_limit


def limit_role(conn, role: str, limits: RoleLimits) -> None:
    """Give `role` these limits. Idempotent; needs the right to ALTER the role."""
    name = sql.Identifier(role)
    if limits.connection_limit is not None:
        conn.execute(
            sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(name, sql.Literal(limits.connection_limit))
        )
    for setting, value in limits.settings().items():
        conn.execute(
            sql.SQL("ALTER ROLE {} SET {} = {}").format(name, sql.Identifier(setting), sql.Literal(value))
        )


#: The agent's reader: every question, the console's every query and the
#: review service's validation run as it (and then as the person, with SET
#: LOCAL ROLE, whose own role settings do not apply mid-session). Above the
#: agent's own per-transaction timeout (STATEMENT_TIMEOUT_MS, 30 s) so that
#: one is what a question meets; the limit is for a client that set none.
READER = RoleLimits(statement_timeout_ms=120_000, work_mem="16MB", idle_in_transaction_ms=60_000, connection_limit=60)

#: The snippet store's reader: the agent's retriever, one pool per process.
SNIPPETS_READER = RoleLimits(statement_timeout_ms=30_000, work_mem="16MB", idle_in_transaction_ms=60_000, connection_limit=30)

#: The feedback writer: the API's INSERT-only role, small single-row writes.
FEEDBACK_WRITER = RoleLimits(statement_timeout_ms=10_000, work_mem="4MB", idle_in_transaction_ms=30_000, connection_limit=20)

#: A store's owner: the review service, and the loaders it runs. No idle
#: limit: a load embeds between statements, through a host that may take
#: minutes (REVIEW_RELOAD_TIMEOUT_SECONDS), with its transaction open.
STORE_OWNER = RoleLimits(work_mem="16MB", connection_limit=20)

#: The role sync's login: one connection per sync, and the revocation writes.
ROLESYNC = RoleLimits(statement_timeout_ms=60_000, work_mem="4MB", idle_in_transaction_ms=60_000, connection_limit=10)
