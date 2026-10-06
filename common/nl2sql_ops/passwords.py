"""A store's password, made what its secret says.

The knowledge stores and MLflow's are their own servers, whose owner is
their superuser and whose password the image or the first start set. A
volume made before the passwords were generated (6.1) has the one every
copy of this repository shares until it is told otherwise, which is what
this does on every start. The stores' own server sets its roles' in
`nl2sql_ops.stores`.
"""

from __future__ import annotations

from psycopg import sql


def set_password(conn, role: str, password: str) -> bool:
    """`role`'s password, if there is such a role. True when there was."""
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
        return False
    conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
    return True
