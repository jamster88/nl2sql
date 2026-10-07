"""Telling Postgres whose statement it is running (V6-64).

A signed-in person's SQL runs as their own role for one transaction
(`SET LOCAL ROLE`), so inside it `current_user` is them -- and nowhere else:
the connection is the reader's, so `session_user`, `pg_stat_activity` and the
connection log all said the agent asked. Setting `application_name` for the
same transaction puts the person, and the service that ran it for them,
where an operator looks: `pg_stat_activity.application_name` while it runs,
and `%a` in `log_line_prefix` for anything it logs. `SET LOCAL`, as the role
is, so the pooled connection goes back to the next borrower as it came.
"""

from __future__ import annotations

#: Parameterised, so a name is a value and never part of the statement.
APPLICATION_NAME_SQL = "SELECT set_config('application_name', %(name)s, true)"

#: What Postgres keeps of an application name (NAMEDATALEN - 1 bytes); it
#: shows anything past printable ASCII as `?`, and so does this.
LIMIT = 63


def application_name(service: str, principal: str) -> str:
    """`nl2sql:<service>:<person>`, as Postgres will keep it."""
    text = f"nl2sql:{service}:{principal}"
    printable = "".join(character if " " <= character <= "~" else "?" for character in text)
    return printable[:LIMIT]
