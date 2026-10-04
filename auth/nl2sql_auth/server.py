"""Starting the auth service: the signing key, the certificate, the banner, uvicorn.

Like the review service and the console, this presents the certificate the
agent API generates rather than one of its own -- the GUIs' proxies and the
desktop client already trust it, and API_TLS_HOSTNAMES covers nl2sql-auth for
this reason. The signing key is its own, made on first start.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Callable, Sequence

import psycopg

from . import __version__, keys
from .app import create_app
from .settings import SERVICE_HOSTNAME, AuthSettings


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
    if not url:
        return "(not set)"
    scheme, _, rest = url.partition("://")
    credentials, at, host = rest.rpartition("@")
    if not at:
        return url
    return f"{scheme}://{credentials.partition(':')[0]}:***@{host}"


def banner(settings: AuthSettings, *, version: str = __version__) -> str:
    lines = [
        f"nl2sql auth service {version}",
        f"  listening on   {settings.public_url()}",
        f"  signs in at    {settings.db_host}:{settings.db_port}/{settings.db_name} (sslmode={settings.db_sslmode})",
        f"  directory      {settings.ldap_url} ({settings.ldap_mode}, {settings.ldap_base_dn})",
        f"  role sync      {_redacted(settings.rolesync_url)}, every {settings.role_sync_interval:g}s",
        f"  sessions       {settings.session_hours:g} hours, signed with {settings.signing_key_file}",
        f"  web interface  {'off: the directory is a replica' if settings.replica else '/directory/v1'}",
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


def main(argv: Sequence[str] | None = None, *, run: Callable | None = None) -> int:
    args = parse_args(argv)
    settings = settings_from_args(args)
    print(banner(settings))
    if args.print_settings:
        return 0
    if settings.tls_enabled and not settings.certificate_present:
        print(
            f"error: TLS is on but {settings.tls_cert_file} is not readable.\n"
            "  This service presents the certificate the agent API generates. Start the API\n"
            "  once so it writes one, mount its volume here, and make sure API_TLS_HOSTNAMES\n"
            f"  covers {SERVICE_HOSTNAME}. Or start with --no-tls.",
            file=sys.stderr,
        )
        return 2
    try:
        key, note = keys.load_or_create(settings.signing_key_file, settings.public_key_file)
    except (OSError, ValueError, keys.SigningKeyError) as exc:
        print(f"error: the signing key: {exc}", file=sys.stderr)
        return 2
    print(note)
    app = create_app(settings=settings, signing_key=key, database_check=database_check(settings.rolesync_url))
    if run is None:
        import uvicorn

        run = uvicorn.run
    run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ssl_certfile=settings.tls_cert_file if settings.tls_enabled else None,
        ssl_keyfile=settings.tls_key_file if settings.tls_enabled else None,
    )
    return 0
