"""`python -m nl2sql_ops prepare | report | snippets` -- the dbprep service.

prepare   every database whose server is up, made what the settings say;
          the retail database is required. Run by compose before anything
          that connects to one, and exits non-zero, with the reason, when it
          cannot: the services that depend on it then do not start, which is
          better than starting against a database nobody can sign in to.
report    what each database holds (`nl2sql_ops.report`), for the scripts.
snippets  whether the snippet store is behind its document: behind,
          current or unknown.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from .connect import connect, listening
from .hba import HbaError
from .passwords import set_password
from .report import report, snippets_state
from .retail import ensure_extensions, ensure_reader, ensure_signin, signin_lines, write_signin_rules
from .settings import OpsSettings
from .stores import close_maintenance, ensure_store, ensure_vectors, write_transport_rules


class Missing(RuntimeError):
    """Something the settings say is needed is not there."""


def _say(text: str) -> None:
    print(f"INFO {text}", flush=True)


def prepare_retail(settings: OpsSettings) -> None:
    if not listening(settings.sockets, "retail"):
        raise Missing(f"the retail database's socket is not in {settings.sockets / 'retail'}: is it running?")
    with connect(settings.sockets, "retail", user="postgres", dbname=settings.retail_database) as conn:
        try:
            ensure_extensions(conn)
        except psycopg.Error as exc:
            print(f"WARN could not create pg_trgm; literal matching falls back to difflib ({exc.sqlstate})", flush=True)
        ensure_reader(conn, reader=settings.reader, owner=settings.retail_owner,
                      database=settings.retail_database, password=settings.reader_password)
        _say(f"role {settings.reader} can read every table and write none")
        if settings.signin:
            if not settings.rolesync_password:
                raise Missing("sign-in is on and AUTH_ROLESYNC_PASSWORD's secret is empty: run ./setup.sh")
            ensure_signin(conn, reader=settings.reader, owner=settings.retail_owner, rolesync=settings.rolesync,
                          password=settings.rolesync_password, database=settings.retail_database)
            lines = signin_lines(reader=settings.reader, rolesync=settings.rolesync, database=settings.retail_database,
                                 host=settings.ldap_host, port=settings.ldap_port, tls=settings.ldap_tls,
                                 base_dn=settings.ldap_base_dn)
            write_signin_rules(conn, lines)
            _say("a person signs in with their directory password, which Postgres checks itself")
        else:
            write_signin_rules(conn, [])
            _say("sign-in is off (AUTH_ENABLED=false): the database's sign-in lines are out")


def prepare_stores(settings: OpsSettings) -> None:
    if not listening(settings.sockets, "stores"):
        _say("the runtime stores are not running, so they were left as they are")
        return
    with connect(settings.sockets, "stores", user="postgres", dbname="postgres") as conn:
        write_transport_rules(conn)
        for store in settings.stores:
            ensure_store(conn, store)
        close_maintenance(conn)
    for store in settings.stores:
        if store.vectors:
            with connect(settings.sockets, "stores", user="postgres", dbname=store.database) as conn:
                ensure_vectors(conn)
    _say("the runtime stores: " + ", ".join(f"{store.key} ({store.database})" for store in settings.stores))


def prepare_passwords(settings: OpsSettings) -> None:
    for login in (*settings.knowledge, settings.mlflow):
        if login is None or not login.password or not listening(settings.sockets, login.socket):
            continue
        with connect(settings.sockets, login.socket, user=login.role, dbname=login.database) as conn:
            if set_password(conn, login.role, login.password):
                _say(f"{login.socket} store: {login.role}'s password is its secret")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nl2sql_ops", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("prepare", "report", "snippets"), nargs="?", default="prepare")
    command = parser.parse_args(argv).command
    settings = OpsSettings.from_env()
    try:
        if command == "report":
            for line in report(settings):
                print(line, flush=True)
        elif command == "snippets":
            print(snippets_state(settings), flush=True)
        else:
            prepare_retail(settings)
            prepare_stores(settings)
            prepare_passwords(settings)
    except (Missing, HbaError, ValueError) as exc:
        print(f"dbprep: {exc}", file=sys.stderr, flush=True)
        return 1
    except psycopg.Error as exc:
        words = str(exc).strip().splitlines()
        print(f"dbprep: {type(exc).__name__}" + (f": {words[0]}" if words else ""), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
