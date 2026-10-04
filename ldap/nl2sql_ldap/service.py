"""Running the directory: slapd, the first-start work, and a replica's copy.

    python3 -m nl2sql_ldap            serve (the container's command)
    python3 -m nl2sql_ldap health     exit 0 when the directory answers
    python3 -m nl2sql_ldap import F   load people from a CSV or LDIF file
    python3 -m nl2sql_ldap sync       copy from the primary now (replica)
    python3 -m nl2sql_ldap config     print the slapd.conf these settings make

This process is the container's first one, so it holds slapd as a child and
passes the stop signal on: a first process that installs no handler ignores
SIGTERM, and `docker stop` would wait out its timeout and kill slapd mid-write.
A replica's copy runs beside slapd in a thread of this process, so the
container is one directory whichever mode it is in.

Started as root, it gives root up before it does anything else (`become`):
every command runs as the `ldap` user, slapd included, and so does a
`docker exec` or the health check, which Docker runs as root. The one thing
done as root first is to make the directories `serve` writes the ldap user's
-- because a named volume another container mounted first is created empty
and owned by root, which is what happens to the certificate's volume when the
retail database starts before the directory, as it does under compose.
"""

from __future__ import annotations

import argparse
import os
import pwd
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from ldap3 import EXTERNAL, NONE, SASL, Connection, Server
from ldap3.core.exceptions import LDAPException

from . import slapd, tls
from .bootstrap import bootstrap
from .directory import Directory, DirectoryError
from .layout import Layout
from .records import parse
from .replica import Replicator
from .settings import CONFIG_FILE, DATA_DIR, RUN_DIR, DirectorySettings, SettingsError

#: Who slapd and this process run as: the account Alpine's openldap package
#: makes, and the directory's root on its local socket by its peer credentials.
ACCOUNT = "ldap"


def become(
    account: str = ACCOUNT,
    *,
    own: Sequence[str] = (),
    getuid: Callable[[], int] = os.getuid,
    lookup: Callable = pwd.getpwnam,
    makedirs: Callable = os.makedirs,
    chown: Callable = os.chown,
    setgroups: Callable = os.setgroups,
    setgid: Callable = os.setgid,
    setuid: Callable = os.setuid,
    environ: dict | None = None,
) -> None:
    """Run as `account` from here on, giving it `own` first, if started as root.

    Not recursive: a directory is given to the account, and what is written
    in it from then on is the account's anyway. A directory that cannot be
    given -- a certificate mounted read-only, say -- is left as it is; it is
    only written when there is no certificate to read, and `tls.ensure` says
    so in words when that fails.
    """
    if getuid() != 0:
        return
    entry = lookup(account)
    for path in own:
        makedirs(path, exist_ok=True)
        try:
            chown(path, entry.pw_uid, entry.pw_gid)
        except OSError:
            pass
    setgroups([])
    setgid(entry.pw_gid)
    setuid(entry.pw_uid)
    (os.environ if environ is None else environ)["HOME"] = entry.pw_dir


def local_connection() -> Connection:
    """Root on the directory: this container's user, on the local socket."""
    server = Server(f"ldapi://{slapd.SOCKET}", get_info=NONE, connect_timeout=5)
    return Connection(
        server, authentication=SASL, sasl_mechanism=EXTERNAL, sasl_credentials="", auto_bind=True
    )


def wait_for(
    connect: Callable[[], Connection],
    alive: Callable[[], bool],
    *,
    seconds: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Connection | None:
    """A connection once slapd answers; None if it died or never did."""
    deadline = clock() + seconds
    while alive() and clock() < deadline:
        try:
            return connect()
        except (LDAPException, OSError):
            sleep(0.25)
    return None


def serve(
    settings: DirectorySettings,
    *,
    spawn: Callable = subprocess.Popen,
    connect: Callable[[], Connection] = local_connection,
    install: Callable = signal.signal,
    replicator: Callable[..., Replicator] = Replicator,
    wait: Callable = wait_for,
) -> int:
    problems = settings.problems()
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 2
    for note in settings.warnings():
        print(f"warning: {note}", file=sys.stderr)
    for directory in (RUN_DIR, DATA_DIR):
        os.makedirs(directory, exist_ok=True)
    try:
        print(tls.ensure(settings))
    except tls.CertificateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    Path(CONFIG_FILE).write_text(slapd.render(settings))

    process = spawn(["slapd", "-d", settings.log_level, "-f", CONFIG_FILE, "-h", slapd.listen_urls()])
    stop = threading.Event()

    def terminate(signum, frame) -> None:
        stop.set()
        process.terminate()

    install(signal.SIGTERM, terminate)
    install(signal.SIGINT, terminate)

    conn = wait(connect, lambda: process.poll() is None)
    if conn is None:
        print("error: slapd did not start answering on its socket", file=sys.stderr)
        process.terminate()
        return process.wait() or 1
    layout = Layout(settings.base_dn)
    try:
        for note in bootstrap(Directory(conn, layout), settings):
            print(note)
    except DirectoryError as exc:
        print(f"error: preparing the directory: {exc}", file=sys.stderr)
        process.terminate()
        return process.wait() or 1
    finally:
        conn.unbind()

    print(f"nl2sql directory ({settings.mode}) serving {settings.base_dn}")
    if settings.replica:
        assert settings.upstream is not None
        copier = replicator(settings.upstream, lambda: Directory(connect(), layout))
        threading.Thread(target=copier.run_forever, args=(stop,), name="replica", daemon=True).start()
    code = process.wait()
    stop.set()
    return code


def health(*, connect: Callable[[], Connection] = local_connection, base_dn: str) -> int:
    try:
        conn = connect()
        found = conn.search(base_dn, "(objectClass=*)", search_scope="BASE", attributes=["1.1"])
        conn.unbind()
    except (LDAPException, OSError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    if not found:
        print(f"unhealthy: {base_dn} is not there yet", file=sys.stderr)
        return 1
    return 0


def import_file(path: str, settings: DirectorySettings, *, connect: Callable[[], Connection] = local_connection) -> int:
    if settings.replica:
        print(
            "error: this directory is a replica; people are added on the primary and copied here",
            file=sys.stderr,
        )
        return 2
    source = Path(path)
    if not source.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    conn = connect()
    try:
        summary = Directory(conn, Layout(settings.base_dn)).apply(
            parse(source.read_text(encoding="utf-8"), filename=source.name)
        )
    finally:
        conn.unbind()
    print(
        f"{source.name}: {len(summary.created)} created, {len(summary.updated)} updated, "
        f"{len(summary.passwords)} passwords set, {len(summary.groups)} memberships added"
    )
    for problem in summary.problems:
        print(f"  {problem}")
    return 1 if summary.problems else 0


def sync(settings: DirectorySettings, *, replicator: Callable[..., Replicator] = Replicator,
         connect: Callable[[], Connection] = local_connection) -> int:
    if not settings.replica:
        print("error: this directory is standalone; there is no primary to copy from", file=sys.stderr)
        return 2
    assert settings.upstream is not None
    layout = Layout(settings.base_dn)
    return 0 if replicator(settings.upstream, lambda: Directory(connect(), layout)).run_once() else 1


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python3 -m nl2sql_ldap",
        description="The nl2sql directory: standalone, or a read-only replica of another.",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("serve", help="run slapd, prepare the directory, copy from the primary (default)")
    sub.add_parser("health", help="exit 0 when the directory answers on its socket")
    loader = sub.add_parser("import", help="load people and groups from a CSV or LDIF file (standalone)")
    loader.add_argument("file")
    sub.add_parser("sync", help="copy from the primary now (replica)")
    sub.add_parser("config", help="print the slapd.conf these settings produce")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = DirectorySettings.from_env()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # Settings first, as root: a password in a file Docker mounted for root
    # alone is still read. Then root is given up, whatever the command.
    serving = args.command in (None, "serve")
    become(
        own=(
            RUN_DIR, DATA_DIR,
            os.path.dirname(settings.tls_cert_file), os.path.dirname(settings.tls_key_file),
        ) if serving else ()
    )
    if args.command == "health":
        return health(base_dn=settings.base_dn)
    if args.command == "import":
        return import_file(args.file, settings)
    if args.command == "sync":
        return sync(settings)
    if args.command == "config":
        print(slapd.render(settings), end="")
        return 0
    return serve(settings)
