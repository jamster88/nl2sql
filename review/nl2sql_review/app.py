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
open one, judge it, and -- for the ones worth keeping -- build a golden pair
out of it and write that pair into the document. The last step is the only
one with consequences outside this database, and it is the only one that is
a POST to a named action rather than a field on a PATCH.
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
from .models import (
    ApiError,
    Check,
    GoldenPairModel,
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
    StepModel,
    SubmissionList,
    SubmissionModel,
)
from .promote import PromotionError
from .render import Draft
from .settings import ReviewSettings
from .store import STATES, Repository, Submission

__version__ = "4.4.0"

#: Same envelope as the agent API, so one client parses both services.
FALLBACK_CODES = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    503: "unavailable",
}

_bearer = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


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


def create_app(
    *,
    settings: ReviewSettings | None = None,
    repository: Repository | None = None,
    promoter: Callable[[ReviewSettings, Draft], promotion_module.Promotion] | None = None,
    previewer: Callable[[ReviewSettings, Draft], tuple[str, str, list[str]]] | None = None,
) -> FastAPI:
    """The application, with every collaborator injectable.

    `promoter` and `previewer` are separated from the repository because they
    are the two things that touch the filesystem. A test that wants to prove
    the routes behave when a promotion fails should not have to arrange for a
    real markdown file to be unwritable.
    """
    config = settings or ReviewSettings.from_env()
    repo = repository if repository is not None else Repository(config.feedback_db_url)
    do_promote = promoter or promotion_module.promote
    do_preview = previewer or promotion_module.preview
    started = time.monotonic()

    app = FastAPI(
        title="NL2SQL feedback review",
        version=__version__,
        summary="Review captured feedback and promote it into the golden question set.",
        description=(
            "Feedback from the web GUI lands in a staging database. This service "
            "is where it is read, judged, edited into a golden question/SQL pair "
            "and written into `context_questions/translated_questions.md`, which "
            "is the source of truth the retrieval stores are built from."
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
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        )

    # --- authentication ---------------------------------------------------

    def authenticate(
        request: Request,
        bearer=Depends(_bearer),
        api_key: str | None = Depends(_api_key),
    ) -> None:
        # No query-string token here, unlike the agent API. That one accepts
        # one because `EventSource` cannot set headers; nothing in this
        # service streams, so the token never has to go somewhere it would
        # be written to an access log.
        if not config.token:
            return
        presented = (bearer.credentials if bearer else None) or api_key
        if presented != config.token:
            raise ReviewHTTPError(
                HTTP_401_UNAUTHORIZED,
                "unauthorized",
                "a valid review token is required",
                **{"WWW-Authenticate": "Bearer"},
            )

    guarded = [Depends(authenticate)]

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

    def _golden() -> GoldenSet:
        """The set as the document holds it, or why it could not be read."""
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
        try:
            nxt = render.next_pair_id(document)
        except ValueError as exc:
            nxt = ""
            return GoldenSet(
                pairs=[_pair_model(p) for p in pairs],
                count=len(pairs),
                document=config.document,
                next_pair_id=nxt,
                suite_in_force=render.suite_in_force(document),
                error=str(exc),
            )
        return GoldenSet(
            pairs=[_pair_model(p) for p in pairs],
            count=len(pairs),
            document=config.document,
            next_pair_id=nxt,
            suite_in_force=render.suite_in_force(document),
        )

    def _pair_model(pair: Any) -> GoldenPairModel:
        return GoldenPairModel(
            pair_id=pair.pair_id,
            chunk_id=pair.chunk_id,
            title=pair.title,
            suite=pair.suite,
            question=pair.question,
            tables=pair.table_list,
            keywords=pair.keyword_list,
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
                "golden": "/v1/golden",
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

        ready = all(check.ok for check in checks.values())
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=config.warnings())

    # --- the API ----------------------------------------------------------

    @app.get(
        "/v1/meta",
        tags=["service"],
        response_model=ReviewMeta,
        dependencies=guarded,
        summary="Everything the review GUI needs to configure itself",
    )
    def meta() -> ReviewMeta:
        golden = _golden()
        try:
            counts = repo.counts()
        except Exception:  # noqa: BLE001 - meta must answer even with no database
            counts = {state: 0 for state in STATES}
        return ReviewMeta(
            version=__version__,
            states=list(STATES),
            document=config.document,
            golden_count=golden.count,
            next_pair_id=golden.next_pair_id,
            counts=counts,
            reload_context=config.reload_context,
            reload_vectors=config.reload_vectors,
            limits=ReviewLimits(
                max_pair_number=render.MAX_PAIR_NUMBER,
                reload_timeout_seconds=config.reload_timeout_seconds,
            ),
            authentication="bearer" if config.token else "none",
            warnings=config.warnings(),
        )

    @app.get(
        "/v1/submissions",
        tags=["submissions"],
        response_model=SubmissionList,
        dependencies=guarded,
        summary="The review queue, newest first",
    )
    def list_submissions(
        state: str | None = Query(default=None, description=f"One of {', '.join(STATES)}."),
        verdict: str | None = Query(default=None, description="'yes' or 'no'."),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> SubmissionList:
        if state is not None and state not in STATES:
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE,
                "invalid_request",
                f"state must be one of {', '.join(STATES)}",
            )
        if verdict is not None and verdict not in ("yes", "no"):
            raise ReviewHTTPError(
                HTTP_422_UNPROCESSABLE, "invalid_request", "verdict must be 'yes' or 'no'"
            )
        found = repo.listing(state=state, verdict=verdict, limit=limit, offset=offset)
        return SubmissionList(
            submissions=[_to_model(s) for s in found],
            count=len(found),
            counts=repo.counts(),
        )

    @app.get(
        "/v1/submissions/{submission_id}",
        tags=["submissions"],
        response_model=SubmissionModel,
        dependencies=guarded,
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
        dependencies=guarded,
        summary="Judge a submission, or save a draft golden pair against it",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
        },
    )
    def patch_submission(submission_id: str, body: ReviewRequest) -> SubmissionModel:
        found = _require(submission_id)
        if found.state == "promoted":
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "already_promoted",
                f"{submission_id} is already in the golden set as "
                f"{found.promoted_pair_id}; its record is not editable",
            )
        updated = repo.review(
            submission_id,
            state=body.state,
            reviewer=body.reviewer,
            review_note=body.review_note,
            draft=body.draft.model_dump() if body.draft is not None else None,
        )
        if updated is None:  # pragma: no cover - the row was deleted mid-request
            raise ReviewHTTPError(
                HTTP_404_NOT_FOUND, "not_found", f"no submission {submission_id}"
            )
        return _to_model(updated)

    @app.post(
        "/v1/submissions/{submission_id}/preview",
        tags=["promotion"],
        response_model=PreviewModel,
        dependencies=guarded,
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
        dependencies=guarded,
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
    ) -> PromotionModel:
        found = _require(submission_id)
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
            reviewer=reviewer or found.reviewer,
            reloaded=result.reloaded,
            reload_detail=result.detail,
        )
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

    @app.get(
        "/v1/golden",
        tags=["promotion"],
        response_model=GoldenSet,
        dependencies=guarded,
        summary="The golden question set as the document holds it",
    )
    def golden() -> GoldenSet:
        return _golden()

    @app.get(
        "/v1/promotions",
        tags=["promotion"],
        response_model=PromotionList,
        dependencies=guarded,
        summary="What has been promoted, newest first",
    )
    def promotions(limit: int = Query(default=50, ge=1, le=500)) -> PromotionList:
        rows = repo.promotions(limit)
        return PromotionList(
            promotions=[PromotionRecord(**{k: v for k, v in row.items() if k != "markdown"}) for row in rows],
            count=len(rows),
        )

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
