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
import os
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.exceptions import HTTPException
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

#: Written out rather than imported from `starlette.status`, which renamed
#: this one and deprecates the old spelling. A literal cannot be deprecated.
HTTP_422_UNPROCESSABLE = 422

from . import promote as promotion_module
from . import render
from . import snippet_validation as snippet_validation_module
from . import snippets as snippets_module
from . import validation as validation_module
from .corrections import (
    BY_SLUG,
    COMPLETIONS,
    CORRECTIONS,
    KINDS,
    AlreadyFixed,
    EmbedResult,
    Fix,
    FixStore,
)
from .models import (
    ApiError,
    Check,
    CuratedFixRequest,
    CuratedFixResultModel,
    CuratedFixValidateRequest,
    FixKind,
    FixList,
    FixModel,
    FixRequest,
    FixRemovalModel,
    FixResultModel,
    GoldenPairModel,
    GoldenRemovalModel,
    GoldenResultModel,
    GoldenSet,
    Health,
    PreviewModel,
    PreviewRequest,
    PromotionList,
    PromotionModel,
    PromotionRecord,
    Readiness,
    ReviewLimits,
    ReviewMeta,
    ReviewRequest,
    SchemaModel,
    SnippetModel,
    SnippetPreviewModel,
    SnippetPreviewRequest,
    SnippetRequest,
    SnippetResultModel,
    SnippetSet,
    SnippetStoreModel,
    SnippetValidationModel,
    StepModel,
    SubmissionList,
    SubmissionModel,
    UndoModel,
    ValidateRequest,
    ValidationModel,
    WithdrawalModel,
)
from .promote import PromotionError
from .render import Draft
from .settings import ReviewSettings
from .snippets import SnippetDraft, SnippetMissing
from .store import STATES, VERDICTS, Repository, Submission
from nl2sql_identity import Guard, GuardSettings, Identity
from nl2sql_identity.postgres import membership_lookup

__version__ = "6.0.1"

#: What each verdict's submissions are for, in the words a refusal uses.
#: `yes` is promoted into the golden set; the other two are fixed into the
#: store for their verdict.
GOLDEN_VERDICT = "yes"

#: Same envelope as the agent API, so one client parses both services.
FALLBACK_CODES = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    503: "unavailable",
}

_bearer = HTTPBearer(
    auto_error=False,
    description="A session token from the auth service (POST /auth/token), or the review token.",
)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="The review token, by another name.")

#: On every route that needs a caller, so the OpenAPI document says how to
#: authenticate. They decide nothing: `Guard` does.
DOCUMENTED = [Depends(_bearer), Depends(_api_key)]


def default_guard(config: ReviewSettings) -> Guard:
    """Sign-in as the environment configures it, roles re-read from Postgres.

    Re-read through the validating connection: any role can ask
    `pg_has_role`. The static token holds both roles, as it always could do
    everything here.
    """
    return Guard(
        GuardSettings(
            enabled=config.auth_enabled,
            public_key_file=config.auth_public_key_file,
            cookie_name=config.auth_cookie_name,
            service_token=config.token,
            service_roles=frozenset((*config.reviewer_roles, *config.curator_roles)),
        ),
        recheck=membership_lookup(config.retail_db_url) if config.auth_enabled else None,
    )


def author(caller: Identity, claimed: str | None) -> str | None:
    """Who did it: a signed-in person, whatever a header or a field said.

    Only without sign-in is the claimed name used -- the `X-Reviewer` a
    token-holding script sends, as before there was anybody to sign in.
    """
    return caller.principal or claimed


class ReviewHTTPError(HTTPException):
    """An HTTPException that carries the stable machine-readable code."""

    def __init__(self, status_code: int, code: str, detail: str, **headers: str) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers or None)
        self.code = code


def _error_response(status: int, code: str, message: str, **detail: Any) -> JSONResponse:
    body = ApiError(error={"code": code, "message": message, **({"detail": detail} if detail else {})})
    return JSONResponse(status_code=status, content=json.loads(body.model_dump_json()))


def seed_draft(submission: Submission) -> Draft:
    """The draft a curator starts from, built out of what was submitted.

    Four of the seven required fields come across for free. The other three
    -- `keywords`, `reasoning_target`, `result` -- are judgements about what
    the question *tests*, and nothing in a thumbs-up carries them, so they
    start empty and the form makes that visible. Pre-filling them with
    something plausible would be worse than leaving them blank: a reviewer
    skimming a full form approves it, and the BM25 index would fill up with
    keywords nobody chose.
    """
    if submission.draft:
        return Draft.from_mapping(submission.draft)
    return Draft(
        title=_title_from(submission.question),
        question=submission.question,
        tables=submission.tables,
        sql_code=submission.sql_code,
    )


def _title_from(question: str, limit: int = 80) -> str:
    """A first guess at the heading, which the curator is expected to edit."""
    text = " ".join(question.strip().rstrip("?").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def _to_model(submission: Submission) -> SubmissionModel:
    return SubmissionModel(**submission.as_dict())


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

    # --- authentication ---------------------------------------------------

    reviewing = guard.require(*config.reviewer_roles)
    curating = guard.require(*config.curator_roles)
    reading = guard.require(*config.reviewer_roles, *config.curator_roles)

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

    # --- helpers ----------------------------------------------------------

    def _require(submission_id: str) -> Submission:
        found = repo.get(submission_id)
        if found is None:
            raise ReviewHTTPError(
                HTTP_404_NOT_FOUND, "not_found", f"no submission {submission_id}"
            )
        return found

    def _require_fixable(submission_id: str) -> Submission:
        """A submission whose verdict is wrong or incomplete, and so is fixed."""
        found = _require(submission_id)
        if found.verdict == GOLDEN_VERDICT:
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "wrong_workflow",
                f"{submission_id} was marked correct; a correct answer is promoted into "
                "the golden set (POST /v1/submissions/{id}/promote), not fixed",
            )
        return found

    def _fix_counts() -> dict[str, int]:
        counts: dict[str, int] = {}
        for slug, store in stores.items():
            try:
                counts[slug] = store.count()
            except Exception:  # noqa: BLE001 - meta must answer with a store down
                counts[slug] = 0
        return counts

    def _golden(with_sources: bool = False) -> GoldenSet:
        """The set as the document holds it, or why it could not be read.

        `with_sources` also asks the staging database which pairs the review
        queue produced -- best effort, as the counts in `/v1/meta` are.
        """
        try:
            document = config.document_path.read_text()
        except OSError as exc:
            return GoldenSet(
                pairs=[], count=0, document=config.document, error=f"cannot read: {exc}"
            )
        promotion_module._ensure_importable(config.rag_dir)
        try:
            gp = promotion_module._parser()
            pairs = promotion_module._parse_text(gp, document)
        except Exception as exc:  # noqa: BLE001 - reported, never raised at a browser
            return GoldenSet(
                pairs=[], count=0, document=config.document, error=f"cannot parse: {exc}"
            )
        sources: dict[str, str] = {}
        if with_sources:
            try:
                sources = repo.outcomes()
            except Exception:  # noqa: BLE001 - the set reads without the staging database
                sources = {}
        return GoldenSet(
            pairs=[_pair_model(p, sources.get(p.pair_id)) for p in pairs],
            count=len(pairs),
            document=config.document,
            next_pair_id=render.next_pair_id(document),
            suite_in_force=render.suite_in_force(document),
        )

    def _pair_model(pair: Any, submission_id: str | None = None) -> GoldenPairModel:
        return GoldenPairModel(
            pair_id=pair.pair_id,
            chunk_id=pair.chunk_id,
            title=pair.title,
            suite=pair.suite,
            question=pair.question,
            tables=pair.table_list,
            keywords=pair.keyword_list,
            reasoning_target=pair.reasoning_target,
            sql_code=pair.sql_code,
            result=pair.result,
            submission_id=submission_id,
        )

    def _golden_check(sql: str, principal: str | None = None) -> validation_module.Validation:
        """A golden pair's SQL, run: it has to run, and it has to return rows.

        Rows, because the golden set's own rule is that an empty result reads
        as a failure: a pair whose answer is nothing teaches nothing and
        measures nothing.
        """
        checked = do_validate(sql, "", principal=principal)
        if checked.valid and checked.row_count == 0:
            checked.valid = False
            checked.problems.append(
                "it ran and returned no rows; a golden pair's SQL returns its answer, and in "
                "the golden set an empty result reads as a failure"
            )
            checked.warnings = [w for w in checked.warnings if "no rows" not in w]
        return checked

    def _promotion_model(result: promotion_module.Promotion) -> PromotionModel:
        return PromotionModel(
            pair_id=result.pair_id,
            chunk_id=result.chunk_id,
            suite=result.suite,
            title=result.title,
            markdown=result.markdown,
            document=result.document,
            backup=result.backup,
            pairs_before=result.pairs_before,
            pairs_after=result.pairs_after,
            reloaded=result.reloaded,
            steps=[StepModel(**step.__dict__) for step in result.steps],
        )

    def _withdrawal_model(result: promotion_module.Withdrawal) -> WithdrawalModel:
        return WithdrawalModel(
            kind="golden",
            id=result.pair_id,
            found=result.found,
            document=result.document,
            backup=result.backup,
            pairs_before=result.pairs_before,
            pairs_after=result.pairs_after,
            reloaded=result.reloaded,
            steps=[StepModel(**step.__dict__) for step in result.steps],
        )

    def _embed(store: FixStore) -> EmbedResult:
        """The RAG half of a fix. Best-effort: the record is the fact."""
        if not config.embed_fixes:
            return EmbedResult(ran=False)
        try:
            return store.embed_pending(make_embedder())
        except Exception as exc:  # noqa: BLE001 - the embedder could not even be built
            return EmbedResult(ran=False, error=f"{type(exc).__name__}: {exc}")

    def _store_unavailable(slug: str, verb: str, exc: Exception) -> ReviewHTTPError:
        return ReviewHTTPError(
            HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
            f"the {slug} store could not be {verb}: {type(exc).__name__}: {exc}",
        )

    # --- the unauthenticated routes ---------------------------------------

    @app.get("/", tags=["service"], summary="What this is and where to go next")
    def root() -> dict[str, Any]:
        return {
            "service": "nl2sql-review",
            "version": __version__,
            "docs": "/docs" if config.docs_enabled else None,
            "openapi": "/openapi.json",
            "endpoints": {
                "meta": "/v1/meta",
                "submissions": "/v1/submissions",
                "submission": "/v1/submissions/{id}",
                "preview": "POST /v1/submissions/{id}/preview",
                "promote": "POST /v1/submissions/{id}/promote",
                "validate": "POST /v1/submissions/{id}/validate",
                "fix": "POST /v1/submissions/{id}/fix",
                "reopen": "POST /v1/submissions/{id}/reopen",
                "delete": "DELETE /v1/submissions/{id}",
                "fixes": "/v1/fixes/{corrections|completions}",
                "golden": "/v1/golden",
                "golden_add": "POST /v1/golden",
                "golden_remove": "DELETE /v1/golden/{pair_id}",
                "fix_add": "POST /v1/fixes/{corrections|completions}",
                "fix_remove": "DELETE /v1/fixes/{corrections|completions}/{fix_id}",
                "snippets": "/v1/snippets",
                "snippet": "/v1/snippets/{snippet_id}",
                "schema": "/v1/schema",
                "promotions": "/v1/promotions",
                "health": "/healthz",
                "readiness": "/readyz",
            },
        }

    @app.get("/healthz", tags=["service"], response_model=Health, summary="Is the process alive")
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - started, 3))

    @app.get(
        "/readyz",
        tags=["service"],
        response_model=Readiness,
        summary="Can it review and promote right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(response: Response) -> Readiness:
        checks: dict[str, Check] = {}
        try:
            repo.ping()
            checks["staging_database"] = Check(ok=True, detail=_redacted(config.feedback_db_url))
        except Exception as exc:  # noqa: BLE001 - the detail is the whole point
            checks["staging_database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")

        golden = _golden()
        checks["golden_document"] = Check(
            ok=golden.error is None,
            detail=golden.error or f"{golden.count} pairs, next is {golden.next_pair_id}",
        )
        # Writability is checked separately from readability because the
        # failure a reviewer hits is a promotion that refuses at the last
        # step, and finding that out from /readyz beats finding it out
        # after filling in the form.
        writable = _writable(config)
        checks["document_writable"] = Check(ok=writable[0], detail=writable[1])

        # The fix path needs three more databases: the retail one a fix is
        # validated against, and the two stores fixes are kept in.
        try:
            ping_retail()
            checks["retail_database"] = Check(ok=True, detail=_redacted(config.retail_db_url))
        except Exception as exc:  # noqa: BLE001 - the detail is the whole point
            checks["retail_database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")
        for slug, store in stores.items():
            try:
                store.ping()
                checks[f"{slug}_store"] = Check(ok=True, detail=_redacted(store.url))
            except Exception as exc:  # noqa: BLE001
                checks[f"{slug}_store"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")

        # The snippets (5.6): their document, and the store it is loaded into.
        digest = ""
        try:
            parsed, next_id, digest = book.listing(config)
            checks["snippets_document"] = Check(ok=True, detail=f"{len(parsed)} snippets, next is {next_id}")
        except PromotionError as exc:
            checks["snippets_document"] = Check(ok=False, detail="; ".join(exc.reasons))
        status = book.store_status(config.snippets_db_url, digest)
        checks["snippets_store"] = Check(ok=status["reachable"], detail=status["detail"])

        signed, detail = guard.check()
        checks["sign_in"] = Check(ok=signed, detail=detail)

        ready = all(check.ok for check in checks.values())
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=config.warnings())

    # --- the API ----------------------------------------------------------

    @app.get(
        "/v1/meta",
        tags=["service"],
        response_model=ReviewMeta,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="Everything the review GUI needs to configure itself",
    )
    def meta() -> ReviewMeta:
        golden = _golden()
        try:
            counts = repo.counts()
            by_verdict = repo.counts_by_verdict()
        except Exception:  # noqa: BLE001 - meta must answer even with no database
            counts = {state: 0 for state in STATES}
            by_verdict = {verdict: dict(counts) for verdict in VERDICTS}
        return ReviewMeta(
            version=__version__,
            states=list(STATES),
            document=config.document,
            golden_count=golden.count,
            next_pair_id=golden.next_pair_id,
            counts=counts,
            verdicts=list(VERDICTS),
            counts_by_verdict=by_verdict,
            fixes=_fix_counts(),
            reload_context=config.reload_context,
            reload_vectors=config.reload_vectors,
            limits=ReviewLimits(
                reload_timeout_seconds=config.reload_timeout_seconds,
                validate_timeout_ms=config.validate_timeout_ms,
                validate_max_rows=config.validate_max_rows,
            ),
            authentication=guard.describe(),
            warnings=config.warnings(),
        )

    @app.get(
        "/v1/submissions",
        tags=["submissions"],
        response_model=SubmissionList,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="The review queue, newest first",
    )
    def list_submissions(
        state: str | None = Query(default=None, description=f"One of {', '.join(STATES)}."),
        verdict: str | None = Query(default=None, description=f"One of {', '.join(VERDICTS)}."),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> SubmissionList:
        if state is not None and state not in STATES:
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE,
                "invalid_request",
                f"state must be one of {', '.join(STATES)}",
            )
        if verdict is not None and verdict not in VERDICTS:
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE,
                "invalid_request",
                f"verdict must be one of {', '.join(VERDICTS)}",
            )
        found = repo.listing(state=state, verdict=verdict, limit=limit, offset=offset)
        return SubmissionList(
            submissions=[_to_model(s) for s in found],
            count=len(found),
            counts=repo.counts(),
            counts_by_verdict=repo.counts_by_verdict(),
        )

    @app.get(
        "/v1/submissions/{submission_id}",
        tags=["submissions"],
        response_model=SubmissionModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="One submission, with the draft seeded if it has none",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}},
    )
    def get_submission(submission_id: str) -> SubmissionModel:
        found = _require(submission_id)
        model = _to_model(found)
        # Seeded on read rather than on capture: the seed is this service's
        # opinion about how a pair should start, and baking it in at capture
        # time would freeze today's opinion into every row ever written.
        if not model.draft:
            model.draft = seed_draft(found).as_dict()
        return model

    @app.patch(
        "/v1/submissions/{submission_id}",
        tags=["submissions"],
        response_model=SubmissionModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Judge a submission, or save a draft golden pair against it",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
        },
    )
    def patch_submission(
        submission_id: str, body: ReviewRequest, caller: Identity = Depends(reviewing)
    ) -> SubmissionModel:
        found = _require(submission_id)
        if found.state == "promoted":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_promoted",
                f"{submission_id} is already in the golden set as "
                f"{found.promoted_pair_id}; reopen it (POST /v1/submissions/{{id}}/reopen), "
                "which takes the pair back out, to change it",
            )
        if found.state == "corrected":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_fixed",
                f"{submission_id} is already fixed as {found.promoted_pair_id}; "
                "reopen it (POST /v1/submissions/{id}/reopen), which deletes the fix, "
                "to change it",
            )
        updated = repo.review(
            submission_id,
            state=body.state,
            reviewer=author(caller, body.reviewer),
            review_note=body.review_note,
            draft=body.draft.model_dump() if body.draft is not None else None,
        )
        if updated is None:  # deleted by another reviewer since it was read
            raise ReviewHTTPError(
                HTTP_404_NOT_FOUND, "not_found", f"no submission {submission_id}"
            )
        return _to_model(updated)

    @app.post(
        "/v1/submissions/{submission_id}/preview",
        tags=["promotion"],
        response_model=PreviewModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="The markdown this draft would add, without writing anything",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}},
    )
    def preview_submission(submission_id: str, body: PreviewRequest) -> PreviewModel:
        _require(submission_id)
        draft = Draft.from_mapping(body.draft.model_dump())
        pair_id, markdown, problems = do_preview(config, draft)
        return PreviewModel(
            pair_id=pair_id,
            markdown=markdown,
            valid=not problems,
            problems=problems,
            suite_in_force=_golden().suite_in_force,
        )

    @app.post(
        "/v1/submissions/{submission_id}/promote",
        tags=["promotion"],
        response_model=PromotionModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Write this pair into the golden question set",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
        },
    )
    def promote_submission(
        submission_id: str,
        body: PreviewRequest,
        reviewer: str = Header(default="", alias="X-Reviewer"),
        caller: Identity = Depends(reviewing),
    ) -> PromotionModel:
        found = _require(submission_id)
        if found.verdict != GOLDEN_VERDICT:
            # A wrong or incomplete answer is fixed into its own store. The
            # golden set is what the agent is measured against, and a pair
            # built from a mistake does not belong in it however well the
            # mistake was corrected.
            store = KINDS[found.verdict].slug
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "wrong_workflow",
                f"{submission_id} was marked {KINDS[found.verdict].label}; it is fixed into "
                f"the {store} store (POST /v1/submissions/{{id}}/fix), not promoted into "
                "the golden set",
            )
        if found.state == "promoted":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_promoted",
                f"{submission_id} is already in the golden set as {found.promoted_pair_id}",
            )
        if found.state == "rejected":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "rejected",
                f"{submission_id} was rejected; accept it before promoting it",
            )

        draft = Draft.from_mapping(body.draft.model_dump())
        # Since 5.6 a pair's SQL is run before it is written, whoever wrote
        # it: the curator may have edited the agent's query in the draft.
        checked = _golden_check(draft.sql_code)
        if not checked.valid:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems))
        draft.sql_code = checked.sql
        try:
            result = do_promote(config, draft)
        except PromotionError as exc:
            # 422 rather than 400: the request was well-formed and the
            # content is what cannot become a golden pair. The reasons are
            # all of them, because the caller is a form.
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE,
                "not_promotable",
                "; ".join(exc.reasons),
            ) from exc

        repo.mark_promoted(
            submission_id,
            pair_id=result.pair_id,
            chunk_id=result.chunk_id,
            suite=result.suite,
            title=result.title,
            markdown=result.markdown,
            reviewer=author(caller, reviewer) or found.reviewer,
            reloaded=result.reloaded,
            reload_detail=result.detail,
        )
        return _promotion_model(result)

    # --- taking a judgement back ------------------------------------------------

    def _withdraw(found: Submission) -> tuple[WithdrawalModel | None, dict[str, Any] | None]:
        """Take back out whatever this submission produced, before its row changes.

        Returns what came out, and the draft the submission should carry if it
        is reopened: the pair as the document held it, or the fix's SQL --
        the work, kept. Done before the row is touched, so a failure here
        leaves everything as it was, and a failure after it is healed by
        trying again: the pair or the fix is then simply not found.
        """
        if found.state == "promoted":
            try:
                result = do_withdraw(config, found.promoted_pair_id or "")
            except PromotionError as exc:
                raise ReviewHTTPError(
                    HTTP_422_UNPROCESSABLE, "not_withdrawable", "; ".join(exc.reasons)
                ) from exc
            model = _withdrawal_model(result)
            return model, result.draft.as_dict() if result.draft is not None else None
        if found.state == "corrected":
            kind = KINDS[found.verdict]
            try:
                removed = stores[kind.slug].delete(found.id)
            except Exception as exc:  # noqa: BLE001 - reported, and nothing was changed
                raise _store_unavailable(kind.slug, "written", exc) from exc
            model = WithdrawalModel(
                kind=kind.slug, id=found.promoted_pair_id or "", found=removed is not None
            )
            return model, {"sql_code": removed.corrected_sql} if removed is not None else None
        return None, None

    @app.post(
        "/v1/submissions/{submission_id}/reopen",
        tags=["submissions"],
        response_model=UndoModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Put a submission back in the queue, taking out what it produced",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def reopen_submission(submission_id: str) -> UndoModel:
        found = _require(submission_id)
        if found.state == "pending":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_pending",
                f"{submission_id} is already pending; there is nothing to reopen",
            )
        withdrawn, draft = _withdraw(found)
        reopened = repo.reopen(submission_id, draft=draft)
        if reopened is None:  # deleted by another reviewer since it was read
            raise ReviewHTTPError(HTTP_404_NOT_FOUND, "not_found", f"no submission {submission_id}")
        return UndoModel(action="reopened", submission=_to_model(reopened), withdrawn=withdrawn)

    @app.delete(
        "/v1/submissions/{submission_id}",
        tags=["submissions"],
        response_model=UndoModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Delete a submission for good, taking out what it produced",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def delete_submission(submission_id: str) -> UndoModel:
        found = _require(submission_id)
        withdrawn, _ = _withdraw(found)
        deleted = repo.delete(submission_id)
        return UndoModel(
            action="deleted", submission=_to_model(deleted or found), withdrawn=withdrawn
        )

    # --- fixing a wrong or incomplete answer --------------------------------

    @app.post(
        "/v1/submissions/{submission_id}/validate",
        tags=["fixes"],
        response_model=ValidationModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Run a corrected query against the live retail database",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}, HTTP_409_CONFLICT: {"model": ApiError}},
    )
    def validate_fix(
        submission_id: str, body: ValidateRequest, caller: Identity = Depends(reviewing)
    ) -> ValidationModel:
        found = _require_fixable(submission_id)
        return ValidationModel(**do_validate(body.sql, found.sql_code, principal=caller.principal).as_dict())

    @app.post(
        "/v1/submissions/{submission_id}/fix",
        tags=["fixes"],
        response_model=FixResultModel,
        dependencies=[*DOCUMENTED, Depends(reviewing)],
        summary="Store a validated fix in the corrections or completions store",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def fix_submission(
        submission_id: str,
        body: FixRequest,
        reviewer: str = Header(default="", alias="X-Reviewer"),
        caller: Identity = Depends(reviewing),
    ) -> FixResultModel:
        found = _require_fixable(submission_id)
        if found.state == "corrected":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_fixed",
                f"{submission_id} is already fixed as {found.promoted_pair_id}",
            )
        if found.state == "rejected":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "rejected",
                f"{submission_id} was rejected; accept it before fixing it",
            )

        # Validated again, here, whatever the browser was told a moment ago:
        # the rule is that only SQL that runs is stored, and a rule the
        # client enforces is a suggestion.
        checked = do_validate(body.sql, found.sql_code, principal=caller.principal)
        if not checked.valid:
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems)
            )

        kind = KINDS[found.verdict]
        store = stores[kind.slug]
        who = author(caller, reviewer) or found.reviewer
        record = Fix(
            fix_id="",
            submission_id=found.id,
            job_id=found.job_id,
            question=found.question,
            incorrect_sql=found.sql_code,
            incorrect_answer=found.answer or found.narrative,
            incorrect_columns=list(found.columns),
            incorrect_row_count=found.row_count,
            corrected_sql=checked.sql,
            corrected_columns=checked.columns,
            corrected_rows=checked.rows,
            corrected_row_count=checked.row_count,
            corrected_truncated=checked.truncated,
            plan_cost=checked.plan_cost,
            user_comment=found.comment,
            reviewer=who,
            review_note=body.review_note,
            agent_version=found.agent_version,
        )
        try:
            stored = store.save(record)
        except AlreadyFixed as exc:
            # The store has it and the staging row does not say so: a save
            # that stored the record and then lost its connection. Heal the
            # row, and say it was already done rather than doing it twice.
            repo.mark_corrected(
                submission_id, fix_id=exc.fix_id, reviewer=who, review_note=body.review_note
            )
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_fixed",
                f"{submission_id} is already fixed as {exc.fix_id}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - reported, and nothing was marked
            raise _store_unavailable(kind.slug, "written", exc) from exc

        submission = repo.mark_corrected(
            submission_id, fix_id=stored.fix_id, reviewer=who, review_note=body.review_note
        )

        # The RAG half. Best-effort: the record is the fact, and a vector the
        # embedding host could not produce now is produced by the next save.
        embedded = _embed(store)
        stored.embedded = embedded.ran and embedded.error is None and embedded.pending == 0

        return FixResultModel(
            kind=kind.slug,
            fix=FixModel(**stored.as_dict()),
            validation=ValidationModel(**checked.as_dict()),
            submission=_to_model(submission if submission is not None else found),
            embedded=stored.embedded,
            embed_detail=embedded.detail,
        )

    @app.get(
        "/v1/fixes/{kind}",
        tags=["fixes"],
        response_model=FixList,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="What one store holds, newest first",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError}},
    )
    def fixes(kind: FixKind, limit: int = Query(default=50, ge=1, le=500)) -> FixList:
        store = stores[BY_SLUG[kind].slug]
        try:
            found = store.listing(limit)
        except Exception as exc:  # noqa: BLE001
            raise _store_unavailable(kind, "read", exc) from exc
        return FixList(kind=kind, fixes=[FixModel(**f.as_dict()) for f in found], count=len(found))

    @app.get(
        "/v1/golden",
        tags=["promotion"],
        response_model=GoldenSet,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="The golden question set as the document holds it",
    )
    def golden() -> GoldenSet:
        return _golden(with_sources=True)

    @app.get(
        "/v1/promotions",
        tags=["promotion"],
        response_model=PromotionList,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="What has been promoted, newest first",
    )
    def promotions(limit: int = Query(default=50, ge=1, le=500)) -> PromotionList:
        rows = repo.promotions(limit)
        return PromotionList(
            promotions=[PromotionRecord(**{k: v for k, v in row.items() if k != "markdown"}) for row in rows],
            count=len(rows),
        )

    # --- curation (5.6): golden pairs written by hand ------------------------

    @app.post(
        "/v1/golden/validate",
        tags=["curation"],
        response_model=ValidationModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Run a golden pair's SQL against the live retail database",
    )
    def validate_golden(body: ValidateRequest, caller: Identity = Depends(curating)) -> ValidationModel:
        return ValidationModel(**_golden_check(body.sql, caller.principal).as_dict())

    @app.post(
        "/v1/golden/preview",
        tags=["curation"],
        response_model=PreviewModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="The markdown a hand-written pair would add, without writing anything",
    )
    def preview_golden(body: PreviewRequest) -> PreviewModel:
        pair_id, markdown, problems = do_preview(config, Draft.from_mapping(body.draft.model_dump()))
        return PreviewModel(
            pair_id=pair_id,
            markdown=markdown,
            valid=not problems,
            problems=problems,
            suite_in_force=_golden().suite_in_force,
        )

    @app.post(
        "/v1/golden",
        tags=["curation"],
        response_model=GoldenResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Validate a hand-written pair's SQL, then write it into the golden set",
        responses={HTTP_422_UNPROCESSABLE: {"model": ApiError}},
    )
    def add_golden(body: PreviewRequest, caller: Identity = Depends(curating)) -> GoldenResultModel:
        draft = Draft.from_mapping(body.draft.model_dump())
        checked = _golden_check(draft.sql_code, caller.principal)
        if not checked.valid:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems))
        draft.sql_code = checked.sql
        try:
            result = do_promote(config, draft)
        except PromotionError as exc:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_promotable", "; ".join(exc.reasons)) from exc
        return GoldenResultModel(
            promotion=_promotion_model(result), validation=ValidationModel(**checked.as_dict())
        )

    @app.delete(
        "/v1/golden/{pair_id}",
        tags=["curation"],
        response_model=GoldenRemovalModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Take a pair out of the golden set, reopening the submission it came from",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def remove_golden(pair_id: str) -> GoldenRemovalModel:
        golden = _golden()
        if golden.error is None and pair_id not in {pair.pair_id for pair in golden.pairs}:
            raise ReviewHTTPError(HTTP_404_NOT_FOUND, "not_found", f"no pair {pair_id} in the golden set")
        try:
            source = repo.get_by_outcome(pair_id)
        except Exception as exc:  # noqa: BLE001 - not knowing would leave the queue claiming the pair
            raise ReviewHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE,
                "unavailable",
                f"the staging database could not say whether {pair_id} came from a submission: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        if source is not None and source.state == "promoted":
            # The review queue produced it: put that submission back, with the
            # pair as its draft, exactly as reopening it would.
            withdrawn, draft = _withdraw(source)
            reopened = repo.reopen(source.id, draft=draft)
            return GoldenRemovalModel(
                pair_id=pair_id, withdrawal=withdrawn, submission=_to_model(reopened or source)
            )
        try:
            result = do_withdraw(config, pair_id)
        except PromotionError as exc:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_withdrawable", "; ".join(exc.reasons)) from exc
        return GoldenRemovalModel(pair_id=pair_id, withdrawal=_withdrawal_model(result))

    # --- curation: fixes written straight into a store --------------------

    @app.post(
        "/v1/fixes/{kind}/validate",
        tags=["curation"],
        response_model=ValidationModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Run a fix's SQL against the live retail database",
    )
    def validate_curated_fix(
        kind: FixKind, body: CuratedFixValidateRequest, caller: Identity = Depends(curating)
    ) -> ValidationModel:
        return ValidationModel(
            **do_validate(body.sql, body.incorrect_sql, principal=caller.principal).as_dict()
        )

    @app.post(
        "/v1/fixes/{kind}",
        tags=["curation"],
        response_model=CuratedFixResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Validate a fix with no submission behind it, then store it",
        responses={
            HTTP_422_UNPROCESSABLE: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def add_curated_fix(
        kind: FixKind,
        body: CuratedFixRequest,
        reviewer: str = Header(default="", alias="X-Reviewer"),
        caller: Identity = Depends(curating),
    ) -> CuratedFixResultModel:
        checked = do_validate(body.sql, body.incorrect_sql, principal=caller.principal)
        if not checked.valid:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems))
        store = stores[BY_SLUG[kind].slug]
        record = Fix(
            fix_id="",
            submission_id=None,
            job_id="",
            question=body.question.strip(),
            incorrect_sql=validation_module.clean(body.incorrect_sql),
            corrected_sql=checked.sql,
            corrected_columns=checked.columns,
            corrected_rows=checked.rows,
            corrected_row_count=checked.row_count,
            corrected_truncated=checked.truncated,
            plan_cost=checked.plan_cost,
            reviewer=author(caller, reviewer) or "",
            review_note=body.review_note,
            source="curated",
        )
        try:
            stored = store.save(record)
        except Exception as exc:  # noqa: BLE001 - reported, and nothing was stored
            raise _store_unavailable(kind, "written", exc) from exc
        embedded = _embed(store)
        stored.embedded = embedded.ran and embedded.error is None and embedded.pending == 0
        return CuratedFixResultModel(
            kind=kind,
            fix=FixModel(**stored.as_dict()),
            validation=ValidationModel(**checked.as_dict()),
            embedded=stored.embedded,
            embed_detail=embedded.detail,
        )

    @app.delete(
        "/v1/fixes/{kind}/{fix_id}",
        tags=["curation"],
        response_model=FixRemovalModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Take a fix out of its store, reopening the submission it came from",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def remove_fix(kind: FixKind, fix_id: str) -> FixRemovalModel:
        store = stores[BY_SLUG[kind].slug]
        try:
            fix = store.get(fix_id)
        except Exception as exc:  # noqa: BLE001
            raise _store_unavailable(kind, "read", exc) from exc
        if fix is None:
            raise ReviewHTTPError(HTTP_404_NOT_FOUND, "not_found", f"no fix {fix_id} in the {kind} store")
        if fix.submission_id:
            source = repo.get(fix.submission_id)
            if source is not None and source.state == "corrected" and source.promoted_pair_id == fix_id:
                _, draft = _withdraw(source)
                reopened = repo.reopen(source.id, draft=draft)
                return FixRemovalModel(
                    kind=kind, fix_id=fix_id, found=True, fix=FixModel(**fix.as_dict()),
                    submission=_to_model(reopened or source),
                )
        try:
            removed = store.delete_by_id(fix_id)
        except Exception as exc:  # noqa: BLE001
            raise _store_unavailable(kind, "written", exc) from exc
        return FixRemovalModel(
            kind=kind,
            fix_id=fix_id,
            found=removed is not None,
            fix=FixModel(**removed.as_dict()) if removed is not None else None,
        )

    # --- curation: SQL snippets ---------------------------------------------

    def _snippet_model(snippet: Any) -> SnippetModel:
        return SnippetModel(
            snippet_id=snippet.snippet_id,
            chunk_id=snippet.chunk_id,
            name=snippet.name,
            kind=snippet.kind,
            tables=snippet.table_list,
            keywords=snippet.keyword_list,
            means=snippet.means,
            applies_to=snippet.applies_to,
            sql=snippet.sql,
            note=snippet.note,
        )

    def _snippet_check(
        draft: SnippetDraft, principal: str | None = None
    ) -> snippet_validation_module.SnippetValidation:
        """Run the snippet, and hold its listed tables to the ones it uses.

        A table the SQL uses and `tables` does not list is a problem, not a
        warning: the agent shows a snippet only when every table it lists is
        in scope, so an unlisted one could be shown with that table out of
        scope -- straight into the static validator's refusal. An empty list
        is filled in from what the SQL uses.
        """
        checked = do_validate_snippet(draft.kind, draft.applies_to, draft.sql, principal=principal)
        if not checked.valid:
            return checked
        listed = [t.strip() for t in draft.tables.split(",") if t.strip()]
        if not listed:
            draft.tables = ", ".join(checked.tables)
            return checked
        unlisted = [t for t in checked.tables if t not in listed]
        if unlisted:
            checked.valid = False
            checked.problems.append(
                f"the SQL uses {', '.join(unlisted)}, which tables does not list -- the agent shows "
                "a snippet only when every table it lists is in scope"
            )
        unused = [t for t in listed if t not in checked.tables]
        if unused:
            checked.warnings.append(f"tables lists {', '.join(unused)}, which the SQL does not use")
        return checked

    def _snippet_result(
        outcome: snippets_module.Outcome, checked: snippet_validation_module.SnippetValidation | None
    ) -> SnippetResultModel:
        return SnippetResultModel(
            action=outcome.action,  # type: ignore[arg-type]
            snippet_id=outcome.snippet_id,
            chunk_id=outcome.chunk_id,
            markdown=outcome.markdown,
            document=outcome.document,
            backup=outcome.backup,
            snippets_before=outcome.snippets_before,
            snippets_after=outcome.snippets_after,
            reloaded=outcome.reloaded,
            steps=[StepModel(**step.__dict__) for step in outcome.steps],
            validation=SnippetValidationModel(**checked.as_dict()) if checked is not None else None,
        )

    def _checked_draft(
        body: SnippetRequest, principal: str | None = None
    ) -> tuple[SnippetDraft, snippet_validation_module.SnippetValidation]:
        draft = SnippetDraft.from_mapping(body.draft.model_dump())
        checked = _snippet_check(draft, principal)
        if not checked.valid:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems))
        return draft, checked

    def _write_snippet(write: Callable[[], snippets_module.Outcome]) -> snippets_module.Outcome:
        try:
            return write()
        except SnippetMissing as exc:
            raise ReviewHTTPError(HTTP_404_NOT_FOUND, "not_found", "; ".join(exc.reasons)) from exc
        except PromotionError as exc:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_writable", "; ".join(exc.reasons)) from exc

    @app.get(
        "/v1/snippets",
        tags=["snippets"],
        response_model=SnippetSet,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="The SQL snippets as their document holds them, and the store beside it",
    )
    def snippets() -> SnippetSet:
        kinds = list(snippet_validation_module.KINDS)
        try:
            parsed, next_id, digest = book.listing(config)
        except PromotionError as exc:
            return SnippetSet(
                snippets=[], count=0, document=config.snippets_document, kinds=kinds, error="; ".join(exc.reasons)
            )
        return SnippetSet(
            snippets=[_snippet_model(s) for s in parsed],
            count=len(parsed),
            document=config.snippets_document,
            next_snippet_id=next_id,
            kinds=kinds,
            store=SnippetStoreModel(**book.store_status(config.snippets_db_url, digest)),
        )

    @app.post(
        "/v1/snippets/validate",
        tags=["snippets"],
        response_model=SnippetValidationModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Run a snippet inside its probe query against the live retail database",
    )
    def validate_snippet(body: SnippetRequest, caller: Identity = Depends(curating)) -> SnippetValidationModel:
        draft = SnippetDraft.from_mapping(body.draft.model_dump())
        return SnippetValidationModel(**_snippet_check(draft, caller.principal).as_dict())

    @app.post(
        "/v1/snippets/preview",
        tags=["snippets"],
        response_model=SnippetPreviewModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="The section a snippet would write, without writing anything",
    )
    def preview_snippet(body: SnippetPreviewRequest) -> SnippetPreviewModel:
        draft = SnippetDraft.from_mapping(body.draft.model_dump())
        try:
            snippet_id, markdown, problems = book.preview(config, draft, body.snippet_id)
        except PromotionError as exc:
            return SnippetPreviewModel(snippet_id=body.snippet_id or "", problems=exc.reasons)
        return SnippetPreviewModel(snippet_id=snippet_id, markdown=markdown, valid=not problems, problems=problems)

    @app.post(
        "/v1/snippets",
        tags=["snippets"],
        response_model=SnippetResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Validate a snippet, write it into its document, and load the store",
        responses={HTTP_422_UNPROCESSABLE: {"model": ApiError}},
    )
    def add_snippet(body: SnippetRequest, caller: Identity = Depends(curating)) -> SnippetResultModel:
        draft, checked = _checked_draft(body, caller.principal)
        return _snippet_result(_write_snippet(lambda: book.add(config, draft)), checked)

    @app.put(
        "/v1/snippets/{snippet_id}",
        tags=["snippets"],
        response_model=SnippetResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Validate a changed snippet, rewrite it in its document, and load the store",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}, HTTP_422_UNPROCESSABLE: {"model": ApiError}},
    )
    def change_snippet(
        snippet_id: str, body: SnippetRequest, caller: Identity = Depends(curating)
    ) -> SnippetResultModel:
        draft, checked = _checked_draft(body, caller.principal)
        return _snippet_result(_write_snippet(lambda: book.change(config, snippet_id, draft)), checked)

    @app.delete(
        "/v1/snippets/{snippet_id}",
        tags=["snippets"],
        response_model=SnippetResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Take a snippet out of its document, and out of the store",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}, HTTP_422_UNPROCESSABLE: {"model": ApiError}},
    )
    def remove_snippet(snippet_id: str) -> SnippetResultModel:
        return _snippet_result(_write_snippet(lambda: book.delete(config, snippet_id)), None)

    @app.get(
        "/v1/schema",
        tags=["snippets"],
        response_model=SchemaModel,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="The retail tables and columns a snippet can be written over",
    )
    def schema() -> SchemaModel:
        try:
            return SchemaModel(tables=read_schema())
        except Exception as exc:  # noqa: BLE001 - shown in place of the list
            return SchemaModel(error=f"{type(exc).__name__}: {exc}")

    return app


def _redacted(url: str) -> str:
    """A connection URL with the password taken out, for the readiness body.

    `/readyz` is unauthenticated -- an orchestrator has to be able to call it
    -- so everything it returns is public, and a URL is the classic way a
    password ends up in one.
    """
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    user = credentials.partition(":")[0]
    return f"{scheme}://{user}:***@{host}" if user else f"{scheme}://{host}"


def _writable(config: ReviewSettings) -> tuple[bool, str]:
    """Whether the question document could actually be replaced.

    Checks the *directory*, not the file: promotion writes a temporary file
    beside the document and renames it over the top, so a read-only mount
    with a writable file would still fail, and a read-only file in a
    writable directory would still work.
    """
    path = config.document_path
    if not path.is_file():
        return False, f"{path} is not there"
    if not os.access(path.parent, os.W_OK):
        return False, f"{path.parent} is not writable; promotion needs to replace the file in place"
    return True, f"{path.parent} is writable"
