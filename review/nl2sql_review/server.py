"""Starting the review service.

A sibling of the agent's `api/server.py` and deliberately smaller, because
this process does less: no certificate to generate (it presents the one the
agent API wrote), no job store to shut down, no pipeline to warm.

What it does do before binding is create its schema and reset the writer
role the agent API connects as. That ordering is the point -- the public
process's grants are whatever this file's `ensure_writer_role` last said,
so widening them by hand does not survive a restart.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Sequence

from .app import __version__, create_app
from .settings import SERVICE_HOSTNAME, ReviewSettings
from .store import WRITER_ROLE, Repository


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m nl2sql_review",
        description="Serve the feedback review API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every option also reads an environment variable (REVIEW_HOST, "
            "REVIEW_PORT, REVIEW_TOKEN, FEEDBACK_DB_URL, ...). The flag wins.\n\n"
            "Examples:\n"
            "  python -m nl2sql_review\n"
            "  python -m nl2sql_review --no-tls --port 8444\n"
            "  python -m nl2sql_review --no-reload-vectors   # no embedding host\n"
        ),
    )
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--root-path")
    p.add_argument("--token")
    p.add_argument("--feedback-db-url")
    p.add_argument("--document")
    p.add_argument("--rag-dir")
    p.add_argument("--log-level")
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--tls", dest="tls", action="store_true", default=None)
    tls.add_argument("--no-tls", dest="tls", action="store_false", default=None)
    schema = p.add_mutually_exclusive_group()
    schema.add_argument("--manage-schema", dest="manage_schema", action="store_true", default=None)
    schema.add_argument(
        "--no-manage-schema", dest="manage_schema", action="store_false", default=None
    )
    context = p.add_mutually_exclusive_group()
    context.add_argument(
        "--reload-context", dest="reload_context", action="store_true", default=None
    )
    context.add_argument(
        "--no-reload-context", dest="reload_context", action="store_false", default=None
    )
    vectors = p.add_mutually_exclusive_group()
    vectors.add_argument(
        "--reload-vectors", dest="reload_vectors", action="store_true", default=None
    )
    vectors.add_argument(
        "--no-reload-vectors", dest="reload_vectors", action="store_false", default=None
    )
    p.add_argument("--print-settings", action="store_true", help="show the settings and exit")
    return p.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> ReviewSettings:
    """Environment first, then the flags that were actually given.

    `None` means "not given" for every flag, which is why the booleans use
    `default=None` above: without it, a store_true flag nobody passed would
    silently override an environment variable that was set.
    """
    settings = ReviewSettings.from_env()
    overrides = {
        name: value
        for name, value in (
            ("host", args.host),
            ("port", args.port),
            ("root_path", args.root_path),
            ("token", args.token),
            ("feedback_db_url", args.feedback_db_url),
            ("document", args.document),
            ("rag_dir", args.rag_dir),
            ("log_level", args.log_level),
            ("tls_enabled", args.tls),
            ("manage_schema", args.manage_schema),
            ("reload_context", args.reload_context),
            ("reload_vectors", args.reload_vectors),
        )
        if value is not None
    }
    return replace(settings, **overrides) if overrides else settings


def banner(settings: ReviewSettings, *, version: str = __version__) -> str:
    lines = [
        f"nl2sql review service {version}",
        f"  listening on   {settings.public_url()}",
        f"  staging db     {_redacted(settings.feedback_db_url)}",
        f"  writer role    {WRITER_ROLE} (INSERT only, reset on start)",
        f"  golden set     {settings.document}",
        f"  reload         context={settings.reload_context} vectors={settings.reload_vectors}",
        f"  auth           {'bearer token' if settings.authenticated else 'NONE'}",
    ]
    if settings.tls_enabled:
        lines.append(f"  certificate    {settings.tls_cert_file} (written by the agent API)")
    for note in settings.warnings():
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def _redacted(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    user = credentials.partition(":")[0]
    return f"{scheme}://{user}:***@{host}" if user else f"{scheme}://{host}"


def prepare(settings: ReviewSettings) -> list[str]:
    """Create the schema and reset the writer role. Returns what happened.

    Failure is returned rather than raised. A staging database that is still
    starting is the normal case on a `compose up`, and a review service that
    refused to start because of it would need a restart to recover from a
    condition that fixes itself. `/readyz` reports it until it does.
    """
    if not settings.manage_schema:
        return ["schema: not managed (REVIEW_MANAGE_SCHEMA=false)"]
    try:
        Repository(settings.feedback_db_url).setup(settings.writer_password)
    except Exception as exc:  # noqa: BLE001 - reported in the banner and /readyz
        return [f"schema: NOT ready -- {type(exc).__name__}: {exc}"]
    return [f"schema: ready, {WRITER_ROLE} reset to INSERT-only"]


def build(argv: Sequence[str] | None = None):
    """The app and its settings, without binding a socket. For tests."""
    settings = settings_from_args(parse_args(argv))
    return create_app(settings=settings), settings


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = settings_from_args(args)

    if args.print_settings:
        print(banner(settings))
        return 0

    for note in prepare(settings):
        print(note)
    print(banner(settings))

    if settings.tls_enabled and not settings.certificate_present:
        print(
            f"error: TLS is on but {settings.tls_cert_file} is not readable.\n"
            "  This service presents the certificate the agent API generates. Start the\n"
            f"  API once so it writes one, mount its volume here, and make sure\n"
            f"  API_TLS_HOSTNAMES covers {SERVICE_HOSTNAME}. Or start with --no-tls.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ssl_certfile=settings.tls_cert_file if settings.tls_enabled else None,
        ssl_keyfile=settings.tls_key_file if settings.tls_enabled else None,
    )
    return 0
