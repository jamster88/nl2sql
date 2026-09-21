"""Starting the server: flags, certificate, banner, uvicorn.

Kept apart from `app.py` so the application can be built and exercised
without binding a socket -- which is what makes the whole HTTP surface
testable offline. Everything here is about the process: what the command
line may override, which certificate gets presented, and what is printed
before the first request arrives.

The banner is not decoration. Three things go wrong when someone points a
GUI at this for the first time, and all three are invisible from the client
side: TLS is off when they thought it was on, the certificate is self-signed
so their HTTP library refuses it, or no token is set and the port is open to
whoever finds it. Each is printed at startup, next to the URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Sequence

from .. import __version__
from ..config import Settings
from .app import create_app
from .settings import ApiSettings
from .tls import TlsError, certificate_notes, ensure_certificate


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Flags, each one an override of an environment variable.

    The environment is the primary interface because the usual caller is
    Compose; the flags exist for the person debugging it by hand, and for
    `--print-openapi`, which has no environment equivalent because it is not
    a way of running the server.
    """
    defaults = ApiSettings.from_env()
    p = argparse.ArgumentParser(
        prog="nl2sql-agent-api",
        description="Serve the NL2SQL agent over HTTPS for a GUI to call.",
    )
    p.add_argument("--host", default=defaults.host, help="interface to bind (API_HOST)")
    p.add_argument("--port", type=int, default=defaults.port, help="port to bind (API_PORT)")
    p.add_argument(
        "--tls",
        action=argparse.BooleanOptionalAction,
        default=defaults.tls_enabled,
        help="serve HTTPS (API_TLS_ENABLED); --no-tls only behind a TLS terminator",
    )
    p.add_argument("--cert", default=defaults.tls_cert_file, help="PEM certificate (API_TLS_CERT_FILE)")
    p.add_argument("--key", default=defaults.tls_key_file, help="PEM private key (API_TLS_KEY_FILE)")
    p.add_argument(
        "--generate-cert",
        action=argparse.BooleanOptionalAction,
        default=defaults.tls_generate,
        help="write a development certificate when none exists (API_TLS_GENERATE)",
    )
    p.add_argument(
        "--allow-self-signed",
        action=argparse.BooleanOptionalAction,
        default=defaults.tls_allow_self_signed,
        help=(
            "accept a self-signed certificate (API_TLS_ALLOW_SELF_SIGNED); "
            "--no-allow-self-signed refuses to start without a CA-issued one"
        ),
    )
    p.add_argument(
        "--hostname",
        action="append",
        dest="hostnames",
        default=None,
        help="name the generated certificate should cover; repeatable (API_TLS_HOSTNAMES)",
    )
    p.add_argument("--token", default=defaults.token, help="require this API token (API_TOKEN)")
    p.add_argument(
        "--cors-origin",
        action="append",
        dest="cors_origins",
        default=None,
        help="browser origin allowed to call this API; repeatable (API_CORS_ORIGINS)",
    )
    p.add_argument(
        "--max-concurrency",
        type=int,
        default=defaults.max_concurrency,
        help="questions answered at once (API_MAX_CONCURRENCY)",
    )
    p.add_argument(
        "--allow-principal",
        action=argparse.BooleanOptionalAction,
        default=defaults.allow_principal,
        help="let callers choose the database role rows are read as (API_ALLOW_PRINCIPAL)",
    )
    p.add_argument(
        "--docs",
        action=argparse.BooleanOptionalAction,
        default=defaults.docs_enabled,
        help="serve the interactive /docs page (API_DOCS_ENABLED)",
    )
    p.add_argument("--log-level", default=defaults.log_level, help="uvicorn log level (API_LOG_LEVEL)")
    p.add_argument(
        "--print-openapi",
        action="store_true",
        help="write the OpenAPI document to stdout and exit, for client generation",
    )
    return p.parse_args(argv)


def api_settings_from_args(args: argparse.Namespace) -> ApiSettings:
    settings = replace(
        ApiSettings.from_env(),
        host=args.host,
        port=args.port,
        tls_enabled=args.tls,
        tls_cert_file=args.cert,
        tls_key_file=args.key,
        tls_generate=args.generate_cert,
        tls_allow_self_signed=args.allow_self_signed,
        token=args.token,
        allow_principal=args.allow_principal,
        max_concurrency=args.max_concurrency,
        docs_enabled=args.docs,
        log_level=args.log_level,
    )
    if args.hostnames:
        settings.tls_hostnames = tuple(args.hostnames)
    if args.cors_origins:
        settings.cors_origins = tuple(args.cors_origins)
    return settings


def banner(api: ApiSettings, certificate, *, version: str = __version__) -> str:
    """What is printed once, before the first request."""
    lines = [
        f"nl2sql-agent {version} REST API",
        f"  listening on {api.scheme}://{api.host}:{api.port}{api.root_path}",
        f"  openapi     {api.public_url()}/openapi.json",
    ]
    if api.docs_enabled:
        lines.append(f"  docs        {api.public_url()}/docs")
    lines.append(f"  health      {api.public_url()}/healthz")
    if certificate is not None:
        kind = "self-signed" if certificate.self_signed else "CA-issued"
        lines.append(
            f"  certificate {kind}, for {', '.join(certificate.hostnames) or 'no names'}, "
            f"good for {certificate.days_remaining} more day(s)"
        )
        lines.append(f"  fingerprint sha256:{certificate.fingerprint_sha256}")
    lines.append(
        "  auth        bearer token required" if api.token else "  auth        none"
    )
    for note in api.warnings() + certificate_notes(certificate):
        lines.append(f"  WARNING: {note}")
    return "\n".join(lines)


def build(argv: Sequence[str] | None = None):
    """Everything a run needs, without starting one.

    Returns `(app, api_settings, certificate)`. Separated from `main` so a
    test can prove the certificate policy and the banner are right without
    binding a port.
    """
    return build_from_args(parse_args(argv))


def build_from_args(args: argparse.Namespace):
    api = api_settings_from_args(args)
    certificate = ensure_certificate(api)
    app = create_app(
        settings=Settings.from_env(), api_settings=api, certificate=certificate
    )
    return app, api, certificate


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        app, api, certificate = build_from_args(args)
    except TlsError as exc:
        # Exit 2, not 1: a certificate the operator has to fix, distinct from
        # a server that started and then failed.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_openapi:
        print(json.dumps(app.openapi(), indent=2))
        return 0

    print(banner(api, certificate), flush=True)

    import uvicorn

    uvicorn.run(
        app,
        host=api.host,
        port=api.port,
        log_level=api.log_level,
        ssl_certfile=api.tls_cert_file if api.tls_enabled else None,
        ssl_keyfile=api.tls_key_file if api.tls_enabled else None,
        # Every worker would need its own copy of the job store, and a client
        # polling the job it just created would reach the wrong one. One
        # process, a thread pool inside it: `API_MAX_CONCURRENCY` is the knob.
        workers=None,
        timeout_graceful_shutdown=10,
    )
    return 0
