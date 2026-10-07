"""The review service: the HTTP surface a curator's browser talks to.

Structurally a sibling of the agent's `api/app.py` -- one error envelope, a
token checked by a dependency rather than by a path allowlist, collaborators
injected so the whole surface is testable without a database -- and it is a
*separate* application for one reason worth stating plainly.

This process can write `context_questions/translated_questions.md` and
rebuild the stores derived from it. That is the golden question set: the
thing the agent is measured against. The agent's own API is the process
exposed to whoever can reach the port, and giving it those powers because
both happen to be FastAPI would be putting the benchmark inside the blast
radius of the thing being benchmarked. So the public API holds an
INSERT-only role on one table, and everything else is here, behind its own
token, on its own port, in its own container.

The routes are shaped around what a review actually is: look at a queue,
open one, judge it, and -- for the ones worth keeping -- act on it. What
acting means depends on the verdict the user gave, and since 5.1 each
verdict has one way forward:

* **correct** -- build a golden pair out of it and write that pair into the
  question document (`/promote`);
* **wrong** -- write the query the agent should have generated, run it
  against the live retail database (`/validate`), and store the fix in the
  corrections store (`/fix`);
* **correct but incomplete** -- the same, into the completions store.

The golden set is what the agent is measured against, so a wrong or
incomplete answer never reaches it, however well it is fixed; and a fix is
stored only if the query runs, which `/fix` checks again for itself rather
than taking the browser's word for it.

Since 5.6 the same powers are also reachable without a submission, from the
curation interface: a golden pair written and validated by hand (`/v1/golden`),
a fix written straight into its store (`/v1/fixes/{kind}`), and the SQL
snippets -- joins, filters, measures, dimensions -- that have a document and a
store of their own (`/v1/snippets`). Every one of them is run against the
retail database before it is written, and validated again by the route that
writes it. Taking out a pair or a fix that the review queue produced puts its
submission back in the queue, as reopening it would.

A judgement can be taken back (since 5.4). `/reopen` puts a submission back
in the queue and `DELETE` removes it -- and when it had been promoted or
fixed, either one takes the pair back out of the question document, or the
fix out of its store, first. The queue and what it produced are never
allowed to disagree: a reopened submission that was still in the golden set
would be promoted into it a second time.

Promotion, fixing, reopening and deleting are the only steps with
consequences outside the staging database, and none of them is a field on a
PATCH: each is its own action.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from . import __version__
from . import promote as promotion_module
from . import snippet_validation as snippet_validation_module
from . import snippets as snippets_module
from . import validation as validation_module
from .corrections import COMPLETIONS, CORRECTIONS, FixStore
from .render import Draft
from .routes import HTTP_422_UNPROCESSABLE, ReviewContext, _error_response, routers
from .settings import ReviewSettings
from .store import Repository
from nl2sql_common.envelope import FALLBACK_CODES as SHARED_FALLBACK_CODES
from nl2sql_identity import Guard, GuardSettings
from nl2sql_identity.postgres import membership_lookup


#: Same envelope as the agent API, so one client parses both services.
#: The codes an error gets when the route raising it gave none: the shared
#: set, which every nl2sql API answers with.
FALLBACK_CODES = dict(SHARED_FALLBACK_CODES)


def default_guard(config: ReviewSettings) -> Guard:
    """Sign-in as the environment configures it, roles re-read from Postgres.

    Re-read through the validating connection: any role can ask
    `pg_has_role`. The static token is a caller of its own, named and
    holding what REVIEW_TOKEN_ROLES gives it (`ReviewSettings.token_holds`).
    """
    return Guard(
        GuardSettings(
            enabled=config.auth_enabled,
            public_key_file=config.auth_public_key_file,
            cookie_name=config.auth_cookie_name,
            service_token=config.token,
            service_roles=config.token_holds(),
            service_name=config.token_name,
        ),
        recheck=membership_lookup(config.retail_db_url) if config.auth_enabled else None,
    )


def default_embedder(config: ReviewSettings) -> Any:
    """The embedder the golden set's loaders use, pointed where they point.

    `ragproc`'s, imported the way promotion imports it, so a fix's question
    and a golden pair's are embedded by the same code with the same model --
    which is what makes their vectors comparable when an agent reads both.
    """
    promotion_module._ensure_importable(config.rag_dir)
    from ragproc.embedder import build_embedder  # type: ignore[import-not-found]

    return build_embedder("ollama", config.embed_model, config.ollama_url)


def default_validator(config: ReviewSettings) -> Callable[..., validation_module.Validation]:
    def run(sql: str, reference: str, principal: str | None = None) -> validation_module.Validation:
        return validation_module.validate(
            sql,
            url=config.retail_db_url,
            reference=reference,
            timeout_ms=config.validate_timeout_ms,
            max_rows=config.validate_max_rows,
            principal=principal,
        )

    return run


def default_snippet_validator(
    config: ReviewSettings,
) -> Callable[[str, str, str], snippet_validation_module.SnippetValidation]:
    def run(
        kind: str, applies_to: str, sql: str, principal: str | None = None
    ) -> snippet_validation_module.SnippetValidation:
        return snippet_validation_module.validate_snippet(
            kind,
            applies_to,
            sql,
            url=config.retail_db_url,
            timeout_ms=config.validate_timeout_ms,
            principal=principal,
        )

    return run


def default_schema_reader(config: ReviewSettings) -> Callable[[], list[dict[str, Any]]]:
    """The retail tables and their columns, as the validating role sees them."""

    def read() -> list[dict[str, Any]]:
        import psycopg

        with psycopg.connect(config.retail_db_url, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() ORDER BY table_name, ordinal_position"
            ).fetchall()
        tables: dict[str, list[dict[str, str]]] = {}
        for table, column, kind in rows:
            tables.setdefault(table, []).append({"name": column, "type": kind})
        return [{"name": name, "columns": columns} for name, columns in tables.items()]

    return read


def default_retail_pinger(config: ReviewSettings) -> Callable[[], None]:
    def ping() -> None:
        import psycopg

        with psycopg.connect(config.retail_db_url, connect_timeout=5) as conn:
            conn.execute("SELECT 1").fetchone()

    return ping


def create_app(
    *,
    settings: ReviewSettings | None = None,
    repository: Repository | None = None,
    promoter: Callable[[ReviewSettings, Draft], promotion_module.Promotion] | None = None,
    previewer: Callable[[ReviewSettings, Draft], tuple[str, str, list[str]]] | None = None,
    fix_stores: dict[str, FixStore] | None = None,
    validator: Callable[..., validation_module.Validation] | None = None,
    embedder_factory: Callable[[], Any] | None = None,
    retail_pinger: Callable[[], None] | None = None,
    withdrawer: Callable[[ReviewSettings, str], promotion_module.Withdrawal] | None = None,
    snippet_validator: Callable[..., snippet_validation_module.SnippetValidation] | None = None,
    snippet_book: Any = None,
    schema_reader: Callable[[], list[dict[str, Any]]] | None = None,
    guard: Guard | None = None,
) -> FastAPI:
    """The application, with every collaborator injectable.

    `promoter`, `previewer` and `withdrawer` are separated from the repository
    because they are the three things that touch the filesystem. A test that wants to prove
    the routes behave when a promotion fails should not have to arrange for a
    real markdown file to be unwritable. The fix path is split the same way:
    the two stores, the validator that runs SQL against the retail database,
    and the embedder are each handed in, so the routes can be driven with no
    database and no model.

    The curation routes (5.6) follow suit: `snippet_validator` runs a snippet
    inside its probe query, `snippet_book` is what writes the snippet document
    and reads the store (`snippets.py`, unless a test hands in another), and
    `schema_reader` lists the retail tables a snippet can be written over.
    """
    config = settings or ReviewSettings.from_env()
    repo = repository if repository is not None else Repository(config.feedback_db_url)
    do_promote = promoter or promotion_module.promote
    do_preview = previewer or promotion_module.preview
    do_withdraw = withdrawer or promotion_module.withdraw
    stores = fix_stores if fix_stores is not None else {
        CORRECTIONS.slug: FixStore(CORRECTIONS, config.corrections_db_url),
        COMPLETIONS.slug: FixStore(COMPLETIONS, config.completions_db_url),
    }
    do_validate = validator or default_validator(config)
    make_embedder = embedder_factory or (lambda: default_embedder(config))
    ping_retail = retail_pinger or default_retail_pinger(config)
    do_validate_snippet = snippet_validator or default_snippet_validator(config)
    book = snippet_book if snippet_book is not None else snippets_module
    read_schema = schema_reader or default_schema_reader(config)
    guard = guard or default_guard(config)
    started = time.monotonic()

    app = FastAPI(
        title="NL2SQL feedback review",
        version=__version__,
        summary="Review captured feedback: promote the correct, fix the wrong and the incomplete.",
        description=(
            "Feedback from the web GUI lands in a staging database. This service "
            "is where it is read and judged. A *correct* answer is edited into a "
            "golden question/SQL pair and written into "
            "`context_questions/translated_questions.md`, the source of truth the "
            "retrieval stores are built from. A *wrong* answer, or a *correct but "
            "incomplete* one, is fixed instead: the reviewer writes the SQL that "
            "should have been generated, it is validated against the live retail "
            "database, and it is stored in the corrections or completions store."
        ),
        root_path=config.root_path,
        docs_url="/docs" if config.docs_enabled else None,
        redoc_url="/redoc" if config.docs_enabled else None,
        openapi_url="/openapi.json",
    )
    app.state.settings = config
    app.state.repository = repo

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials="*" not in config.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        )

    # --- error shape ------------------------------------------------------

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or FALLBACK_CODES.get(exc.status_code, "error")
        response = _error_response(exc.status_code, code, str(exc.detail))
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            HTTP_422_UNPROCESSABLE,
            "invalid_request",
            "the request body or query string is not valid",
            errors=json.loads(json.dumps(exc.errors(), default=str)),
        )

    # --- the routes (routes.py), each router carrying its guard -------------

    context = ReviewContext(
        config=config,
        repo=repo,
        do_promote=do_promote,
        do_preview=do_preview,
        do_withdraw=do_withdraw,
        stores=stores,
        do_validate=do_validate,
        make_embedder=make_embedder,
        ping_retail=ping_retail,
        do_validate_snippet=do_validate_snippet,
        book=book,
        read_schema=read_schema,
        guard=guard,
        started=started,
    )
    app.state.context = context
    for router in routers(context):
        app.include_router(router)

    return app

