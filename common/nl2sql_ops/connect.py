"""A database, reached through its own socket.

Each database container keeps its socket directory in a volume the one-shot
mounts too (`docker-compose.yml`), so the one-shot connects the way `docker
compose exec ... psql` did: over the socket, as whoever it names, which
Postgres trusts there. Nothing crosses the network and no password is used.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

PORT = 5432
#: What Postgres names the socket in its directory.
SOCKET_FILE = f".s.PGSQL.{PORT}"


def listening(sockets: Path, name: str) -> bool:
    """Whether that database's server is up: its socket is in the directory.

    A directory with no socket in it is a volume whose database is not
    running -- MLflow's, with MLflow off -- which the one-shot passes over.
    """
    return (sockets / name / SOCKET_FILE).exists()


def connect(sockets: Path, name: str, *, user: str, dbname: str) -> psycopg.Connection:
    """A connection to that database, as `user`, in autocommit."""
    return psycopg.connect(
        host=str(sockets / name),
        port=PORT,
        user=user,
        dbname=dbname,
        autocommit=True,
        connect_timeout=10,
        application_name="nl2sql:dbprep",
    )
