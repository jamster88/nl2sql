"""The four runtime stores, as four databases in one server (V6-40, stage 1).

Until 6.3 the feedback, corrections, completions and snippet stores were
four Postgres servers, each a stock image whose owner was its superuser.
They are one pgvector server now, which this makes ready on every start:

* each store a database of its own, owned by a login role of its own with
  its own password -- what each service connects as is what it connected as
  before, only no longer a superuser;
* `CONNECT` taken from PUBLIC on every database, so a store's owner, and
  the roles the stores' owners make, reach their own database and no other;
* pgvector made in the stores that embed, by the superuser, since an owner
  that is not one cannot;
* the feedback and snippet owners with CREATEROLE: the review service makes
  the API's INSERT-only writer as the one, the snippet loader the agent's
  reader as the other. CREATEROLE lets a role change only the roles it
  made, which is a great deal less than the superuser each one was;
* each owner's limits (`nl2sql_common.roles.STORE_OWNER`);
* the superuser on the socket only: a marked block at the top of
  pg_hba.conf refuses it over the network, as the retail image does.

The knowledge stores (chunkdb, vectordb) ship their data in published
images and MLflow's is MLflow's; they are separate servers still.
"""

from __future__ import annotations

from psycopg import sql

from nl2sql_common.roles import STORE_OWNER, limit_role

from .hba import rewrite
from .settings import Store

TRANSPORT_BEGIN = "# BEGIN nl2sql transport"
TRANSPORT_END = "# END nl2sql transport"
TRANSPORT_LINES = ["host all postgres all reject"]

#: The superuser's own databases, which nobody else needs to reach.
MAINTENANCE = ("postgres", "template1")


def ensure_store(conn, store: Store) -> None:
    """One store's owner and database, from a connection as the superuser."""
    if not store.password:
        raise ValueError(f"the {store.key} store has no password: its secret is empty")
    role = sql.Identifier(store.role)
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (store.role,)).fetchone() is None:
        conn.execute(sql.SQL("CREATE ROLE {}").format(role))
    creates = sql.SQL("CREATEROLE" if store.creates_roles else "NOCREATEROLE")
    conn.execute(sql.SQL(
        "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB {} NOREPLICATION NOBYPASSRLS"
    ).format(role, sql.Literal(store.password), creates))
    limit_role(conn, store.role, STORE_OWNER)
    database = sql.Identifier(store.database)
    if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (store.database,)).fetchone() is None:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(database, role))
    conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(database, role))
    conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(database))


def close_maintenance(conn) -> None:
    for name in MAINTENANCE:
        conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))


def ensure_vectors(conn) -> None:
    """pgvector in the database `conn` is connected to, as the superuser."""
    conn.execute("SET client_min_messages = warning")
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def write_transport_rules(conn) -> bool:
    return rewrite(conn, TRANSPORT_BEGIN, TRANSPORT_END, TRANSPORT_LINES)
