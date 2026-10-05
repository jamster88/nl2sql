"""Starting the SQL console.

A sibling of the API's `server.py` and smaller, like the review service's:
no certificate to generate -- it presents the one the agent API writes --
and no pipeline to warm. What it prints before binding is the same kind of
thing the API prints, for the same reason: the mistakes worth catching are
invisible from a browser. The certificate is missing because the API has
not started; it does not cover `nl2sql-console`, so the interface's proxy
will refuse it; there is no token, so the port runs SQL for anyone.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .. import __version__
from ..api.tls import CertificateInfo, certificate_notes, describe_certificate
from ..config import Settings
from .app import create_app
from .settings import SERVICE_HOSTNAME, ConsoleSettings


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Flags, each an override of a `CONSOLE_*` variable. The flag wins."""
    defaults = ConsoleSettings.from_env()
    p = argparse.ArgumentParser(
        prog="python -m nl2sql_agent.console",
        description="Serve the SQL console: the retail database, through the agent's gates.",
    )
    p.add_argument("--host", default=defaults.host, help="interface to bind (CONSOLE_HOST)")
    p.add_argument("--port", type=int, default=defaults.port, help="port to bind (CONSOLE_PORT)")
    p.add_argument(
        "--tls",
        action=argparse.BooleanOptionalAction,
        default=defaults.tls_enabled,
        help="serve HTTPS (CONSOLE_TLS_ENABLED); --no-tls only behind a TLS terminator",
    )
    p.add_argument("--cert", default=defaults.tls_cert_file, help="PEM certificate (CONSOLE_TLS_CERT_FILE)")
    p.add_argument("--key", default=defaults.tls_key_file, help="PEM private key (CONSOLE_TLS_KEY_FILE)")
    p.add_argument("--token", default=defaults.token, help="require this token (CONSOLE_TOKEN)")
    p.add_argument(
        "--max-rows",
        type=int,
        default=defaults.max_rows,
        help="rows sent back for one query (CONSOLE_MAX_ROWS)",
    )
    p.add_argument(
        "--docs",
        action=argparse.BooleanOptionalAction,
        default=defaults.docs_enabled,
        help="serve the interactive /docs page (CONSOLE_DOCS_ENABLED)",
    )
    p.add_argument("--log-level", default=defaults.log_level, help="uvicorn log level (CONSOLE_LOG_LEVEL)")
    p.add_argument(
        "--print-openapi",
        action="store_true",
        help="write the OpenAPI document to stdout and exit",
    )
    return p.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> ConsoleSettings:
    return replace(
        ConsoleSettings.from_env(),
        host=args.host,
        port=args.port,
        tls_enabled=args.tls,
        tls_cert_file=args.cert,
        tls_key_file=args.key,
        token=args.token,
        max_rows=args.max_rows,
        docs_enabled=args.docs,
        log_level=args.log_level,
    )


def redacted(url: str) -> str:
    """A connection URL with its password taken out, for printing."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    user = credentials.partition(":")[0]
    return f"{scheme}://{user}:***@{host}" if user else f"{scheme}://{host}"


def load_certificate(console: ConsoleSettings) -> CertificateInfo | None:
    """This console's certificate, as this process will present it.

    None when TLS is off, or when there is nothing readable to present --
    which `main` refuses to start over rather than serving plain HTTP on a
    port that was supposed to be HTTPS.
    """
    if not console.tls_enabled:
        return None
    if not (Path(console.tls_cert_file).is_file() and Path(console.tls_key_file).is_file()):
        return None
    info = describe_certificate(console.tls_cert_file)
    if SERVICE_HOSTNAME not in info.hostnames:
        info.missing_hostnames = [SERVICE_HOSTNAME]
    return info


def describe_auth(console: ConsoleSettings) -> str:
    """Who may call, in the banner's words. Open is said in capitals."""
    if console.auth_enabled:
        return "sign-in" + (", or the console token" if console.token else "")
    return "bearer token" if console.token else "NONE"


def banner(
    console: ConsoleSettings,
    agent: Settings,
    certificate: CertificateInfo | None,
    *,
    version: str = __version__,
) -> str:
    lines = [
        f"nl2sql SQL console {version}",
        f"  listening on   {console.scheme}://{console.host}:{console.port}{console.root_path}",
        f"  database       {redacted(agent.database_url)} (schema {agent.db_schema})",
        f"  agent limits   timeout {agent.statement_timeout_ms} ms, {agent.max_rows} rows, "
        f"plan cost {agent.max_plan_cost:,.0f}",
        f"  rows shown     up to {console.max_rows} per query",
        f"  auth           {describe_auth(console)}",
    ]
    if certificate is not None:
        lines.append(
            f"  certificate    {certificate.kind}, the console's own, for "
            f"{', '.join(certificate.hostnames) or 'no names'}"
        )
    notes = console.warnings()
    if certificate is not None:
        # The notes about the certificate itself, less the two said here in
        # this process's terms: trusting a development one is its clients'
        # business, and a missing name is this console's to fix.
        notes.extend(
            certificate_notes(replace(certificate, self_signed=False, development=False, missing_hostnames=[]))
        )
        if certificate.missing_hostnames:
            notes.append(
                f"the certificate does not cover {SERVICE_HOSTNAME}, so the console interface's "
                "proxy will refuse it. Add it to CONSOLE_TLS_HOSTNAMES and start again: the "
                "pki service reissues the console's certificate to cover it."
            )
    for note in notes:
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def build(argv: Sequence[str] | None = None):
    """The app, its settings and its certificate, without binding a socket."""
    args = parse_args(argv)
    console = settings_from_args(args)
    certificate = load_certificate(console)
    app = create_app(settings=Settings.from_env(), console_settings=console, certificate=certificate)
    return app, console, certificate


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    console = settings_from_args(args)
    agent = Settings.from_env()
    certificate = load_certificate(console)

    if args.print_openapi:
        app = create_app(settings=agent, console_settings=console, certificate=certificate)
        print(json.dumps(app.openapi(), indent=2))
        return 0

    if console.tls_enabled and certificate is None:
        # Exit 2, as the API does for a certificate problem: something the
        # operator fixes, distinct from a server that started and failed.
        print(
            f"error: TLS is on but {console.tls_cert_file} and {console.tls_key_file} are not\n"
            "  both readable. Under compose the pki service issues the console its own\n"
            "  certificate into the consoletls volume, for names that include\n"
            f"  {SERVICE_HOSTNAME}; mount it here. Or start with --no-tls.",
            file=sys.stderr,
        )
        return 2

    print(banner(console, agent, certificate), flush=True)
    app = create_app(settings=agent, console_settings=console, certificate=certificate)

    import uvicorn

    uvicorn.run(
        app,
        host=console.host,
        port=console.port,
        log_level=console.log_level,
        ssl_certfile=console.tls_cert_file if console.tls_enabled else None,
        ssl_keyfile=console.tls_key_file if console.tls_enabled else None,
        timeout_graceful_shutdown=10,
    )
    return 0
