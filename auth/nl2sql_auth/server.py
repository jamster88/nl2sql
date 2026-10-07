"""Starting the auth service: the signing key, the certificate, the banner, uvicorn.

Its certificate is its own, issued by the stack's development CA (the pki
service; until 6.1 it presented the agent API's). The signing key is its
own too, made on first start.

One process, two ports (V6-58): AUTH_PORT for signing in, which is published
because the desktop client signs in there, and AUTH_DIRECTORY_PORT for the
directory's own API, which is not, and which the app answers nowhere else.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from dataclasses import replace
from typing import Any, Callable, Sequence

import psycopg

from . import __version__, keys
from .app import create_app
from .settings import SERVICE_HOSTNAME, AuthSettings
from nl2sql_common import privileges
from nl2sql_common.urls import redacted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m nl2sql_auth",
        description="Serve sign-in for the nl2sql stack, and the directory's web interface.",
        epilog="Every option also reads an environment variable (AUTH_HOST, AUTH_PORT, ...). The flag wins.",
    )
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--tls", dest="tls", action="store_true", default=None)
    tls.add_argument("--no-tls", dest="tls", action="store_false", default=None)
    p.add_argument("--print-settings", action="store_true", help="show the settings and exit")
    return p.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> AuthSettings:
    settings = AuthSettings.from_env()
    overrides = {
        name: value
        for name, value in (("host", args.host), ("port", args.port), ("tls_enabled", args.tls))
        if value is not None
    }
    return replace(settings, **overrides) if overrides else settings


def _redacted(url: str | None) -> str:
    return redacted(url, missing="(not set)")


def _directory_api(settings: AuthSettings) -> str:
    if settings.replica:
        return "off: the directory is a replica"
    if settings.directory_port:
        return f"/directory/v1 on port {settings.directory_port} only (AUTH_DIRECTORY_PORT)"
    return "/directory/v1, on the sign-in port"


def banner(settings: AuthSettings, *, version: str = __version__) -> str:
    verified = f", against {settings.db_sslrootcert}" if "sslrootcert" in settings.db_ssl else ""
    lines = [
        f"nl2sql auth service {version}",
        f"  listening on   {settings.public_url()}",
        f"  signs in at    {settings.db_host}:{settings.db_port}/{settings.db_name} "
        f"(sslmode={settings.db_sslmode}{verified})",
        f"  directory      {settings.ldap_url} ({settings.ldap_mode}, {settings.ldap_base_dn})",
        f"  role sync      {_redacted(settings.rolesync_url)}, every {settings.role_sync_interval:g}s",
        f"  sessions       {settings.session_hours:g} hours, signed with {settings.signing_key_file}",
        f"  web interface  {_directory_api(settings)}",
    ]
    for note in settings.problems() + settings.warnings():
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def database_check(url: str | None, *, connect: Callable = psycopg.connect) -> Callable[[], str]:
    """For `/readyz`: can the sync's login reach the retail database?"""

    def check() -> str:
        if not url:
            raise RuntimeError("AUTH_ROLESYNC_DB_URL is not set")
        with connect(url, connect_timeout=5) as conn:
            role = conn.execute("SELECT current_user").fetchone()[0]
        return f"reachable as {role}"

    return check


#: The account this service runs as once it has its directories (V6-31).
ACCOUNT = "nl2sql"


def main(argv: Sequence[str] | None = None, *, run: Callable | None = None, become: Callable | None = None) -> int:
    args = parse_args(argv)
    settings = settings_from_args(args)
    print(banner(settings))
    if args.print_settings:
        return 0
    # Root only long enough to give the account the two directories it
    # writes -- the signing key's, the public key's -- with what an older
    # release wrote there as root (V6-31). Everything after, the key read
    # and the server, is the account's.
    if (become or privileges.become)(
        ACCOUNT,
        own=(os.path.dirname(settings.signing_key_file), os.path.dirname(settings.public_key_file)),
        recursive=True,
    ):
        print(f"  running as {ACCOUNT}")
    if settings.tls_enabled and not settings.certificate_present:
        print(
            f"error: TLS is on but {settings.tls_cert_file} is not readable.\n"
            "  Under compose the pki service issues this service its own certificate into\n"
            f"  the authtls volume, for names that include {SERVICE_HOSTNAME}; mount it here.\n"
            "  Or start with --no-tls.",
            file=sys.stderr,
        )
        return 2
    try:
        key, note = keys.load_or_create(settings.signing_key_file, settings.public_key_file)
    except (OSError, ValueError, keys.SigningKeyError) as exc:
        print(f"error: the signing key: {exc}", file=sys.stderr)
        return 2
    print(note)
    app = create_app(settings=settings, signing_key=key, database_check=database_check(settings.rolesync_conninfo))
    (run or serve)(
        app,
        host=settings.host,
        port=settings.port,
        directory_port=settings.directory_port,
        log_level=settings.log_level,
        ssl_certfile=settings.tls_cert_file if settings.tls_enabled else None,
        ssl_keyfile=settings.tls_key_file if settings.tls_enabled else None,
    )
    return 0


def _listening(host: str, port: int) -> socket.socket:
    """A socket bound to host:port, as uvicorn binds its own."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def serve(app: Any, *, host: str, port: int, directory_port: int, server_factory: Callable | None = None, **options: Any) -> None:
    """uvicorn, on the sign-in port and, when there is one, the directory's.

    One server over two sockets rather than two servers: one event loop,
    one lifespan -- the role sync starts once -- and one set of signal
    handlers, so a stop stops both. `scope["server"]` carries the port a
    request arrived on, which is what the app reads to keep the directory's
    routes off the published one.
    """
    import uvicorn

    server = (server_factory or uvicorn.Server)(uvicorn.Config(app, host=host, port=port, **options))
    if not directory_port:
        server.run()
        return
    server.run(sockets=[_listening(host, port), _listening(host, directory_port)])
