"""Starting the review service.

A sibling of the agent's `api/server.py` and deliberately smaller, because
this process does less: no certificate to generate (under compose the pki
service issues it one of its own), no job store to shut down, no pipeline to
warm.

What it does do before binding is create its schema and reset the writer
role the agent API connects as. That ordering is the point -- the public
process's grants are whatever this file's `ensure_writer_role` last said,
so widening them by hand does not survive a restart. Since 5.1 it also
creates the tables of the corrections and completions stores, each in its
own database.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from cryptography import x509
from nl2sql_identity import pki

from .app import __version__, create_app
from .corrections import COMPLETIONS, CORRECTIONS, FixStore
from .settings import SERVICE_HOSTNAME, ReviewSettings
from .store import WRITER_ROLE, Repository
from nl2sql_common import privileges
from nl2sql_common.urls import redacted as _redacted
from nl2sql_common.errors import DATABASE_ERRORS


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
    p.add_argument("--retail-db-url")
    p.add_argument("--corrections-db-url")
    p.add_argument("--completions-db-url")
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
            ("retail_db_url", args.retail_db_url),
            ("corrections_db_url", args.corrections_db_url),
            ("completions_db_url", args.completions_db_url),
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


def describe_auth(settings: ReviewSettings) -> str:
    """Who may call, in the banner's words. Open is said in capitals."""
    if settings.auth_enabled:
        return "sign-in" + (", or the review token" if settings.token else "")
    return "bearer token" if settings.token else "NONE"


def describe_certificate(path: str) -> str:
    """What this service presents, as the console's banner says it: what
    issued it and for which names. Unreadable is said too, though `main`
    refuses to start on that before the banner is printed."""
    try:
        certificate = x509.load_pem_x509_certificate(Path(path).read_bytes())
    except (OSError, ValueError):
        return f"{path} (not readable)"
    if certificate.issuer == certificate.subject:
        kind = "self-signed"
    elif pki.is_development(certificate):
        kind = "issued by the development CA"
    else:
        kind = "CA-issued"
    names = ", ".join(sorted(pki.covered_names(certificate))) or "no names"
    return f"{kind}, the review service's own, for {names}"


def banner(settings: ReviewSettings, *, version: str = __version__) -> str:
    lines = [
        f"nl2sql review service {version}",
        f"  listening on   {settings.public_url()}",
        f"  staging db     {_redacted(settings.feedback_db_url)}",
        f"  writer role    {WRITER_ROLE} (INSERT only, reset on start)",
        f"  golden set     {settings.document}",
        f"  validates on   {_redacted(settings.retail_db_url)}",
        f"  corrections    {_redacted(settings.corrections_db_url)}",
        f"  completions    {_redacted(settings.completions_db_url)}",
        f"  reload         context={settings.reload_context} vectors={settings.reload_vectors}",
        f"  auth           {describe_auth(settings)}",
    ]
    if settings.tls_enabled:
        lines.append(f"  certificate    {describe_certificate(settings.tls_cert_file)}")
    for note in settings.warnings():
        lines.append(f"  ! {note}")
    return "\n".join(lines)


def prepare(settings: ReviewSettings) -> list[str]:
    """Create the schema and reset the writer role. Returns what happened.

    Failure is returned rather than raised. A staging database that is still
    starting is the normal case on a `compose up`, and a review service that
    refused to start because of it would need a restart to recover from a
    condition that fixes itself. `/readyz` reports it until it does.
    """
    if not settings.manage_schema:
        return ["schema: not managed (REVIEW_MANAGE_SCHEMA=false)"]
    notes: list[str] = []
    try:
        Repository(settings.feedback_db_url).setup(settings.writer_password)
        notes.append(f"schema: ready, {WRITER_ROLE} reset to INSERT-only")
    except DATABASE_ERRORS as exc:  # reported in the banner and /readyz
        notes.append(f"schema: NOT ready -- {type(exc).__name__}: {exc}")
    # Each store on its own: one being down does not stop the other, or the
    # golden-set work that needs neither.
    for kind, url in (
        (CORRECTIONS, settings.corrections_db_url),
        (COMPLETIONS, settings.completions_db_url),
    ):
        try:
            FixStore(kind, url).setup()
            notes.append(f"{kind.slug}: ready")
        except DATABASE_ERRORS as exc:
            notes.append(f"{kind.slug}: NOT ready -- {type(exc).__name__}: {exc}")
    return notes


def build(argv: Sequence[str] | None = None):
    """The app and its settings, without binding a socket. For tests."""
    settings = settings_from_args(parse_args(argv))
    return create_app(settings=settings), settings


#: Who this service runs as when the documents' directory is root's: an image
#: started without the checkout mounted, writing the copy inside it (V6-28).
ACCOUNT = "nl2sql"


def main(argv: Sequence[str] | None = None, *, become: Callable | None = None) -> int:
    args = parse_args(argv)
    settings = settings_from_args(args)

    if args.print_settings:
        print(banner(settings))
        return 0

    # Root only long enough to see who owns the checkout's documents, then
    # that person (V6-28): a promoted pair in the working tree is theirs, as
    # if they had typed it, and nothing here is root's to write as. The
    # account's group is kept beside theirs, which is how the TLS key the pki
    # service gave it is read.
    directory = str(settings.document_path.parent)
    if (become or privileges.become_owner_of)(directory, fallback=ACCOUNT):
        print(f"  running as uid {os.getuid()}, the owner of {directory}")

    for note in prepare(settings):
        print(note)
    print(banner(settings))

    if settings.tls_enabled and not settings.certificate_present:
        print(
            f"error: TLS is on but {settings.tls_cert_file} is not readable.\n"
            "  Under compose the pki service issues this service its own certificate into\n"
            f"  the reviewtls volume, for names that include {SERVICE_HOSTNAME}; mount it\n"
            "  here. Or start with --no-tls.",
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
