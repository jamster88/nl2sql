"""The review service's routes, by router, each refused by default (V6-26).

As the other services': the routes open by design -- what this is, its
health, its readiness -- are on the one router that says so, and every
other is on a router that carries the guard for what it does: reading takes
a reviewer or a curator, judging submissions a reviewer, writing the golden
set, the fix stores and the snippets directly a curator. A route added to
one of them is refused to anyone else before anyone thinks to refuse it.

The routes read what they need -- and the helpers they share -- from a
`ReviewContext` rather than from `create_app`'s locals.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.exceptions import HTTPException
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

#: Written out rather than imported from `starlette.status`, which renamed
#: this one and deprecates the old spelling. A literal cannot be deprecated.
HTTP_422_UNPROCESSABLE = 422

from . import __version__
from . import promote as promotion_module
from . import render
from . import snippet_validation as snippet_validation_module
from . import snippets as snippets_module
from . import validation as validation_module
from .corrections import (
    BY_SLUG,
    KINDS,
    AlreadyFixed,
    EmbedResult,
    Fix,
    FixStore,
)
from .models import (
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
    PreviewModel,
    PreviewRequest,
    PromotionList,
    PromotionModel,
    PromotionRecord,
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
from nl2sql_common.errors import DATABASE_ERRORS, MODEL_ERRORS, PARSE_ERRORS
from nl2sql_common.envelope import ApiError, Check, Health, Readiness
from .promote import PromotionError
from .render import Draft
from .settings import ReviewSettings
from .snippets import SnippetDraft, SnippetMissing
from .store import STATES, VERDICTS, Repository, Submission
from nl2sql_identity import ANONYMOUS, Guard, Identity
from nl2sql_common.urls import redacted as _redacted

#: What each verdict's submissions are for, in the words a refusal uses.
#: `yes` is promoted into the golden set; the other two are fixed into the
#: store for their verdict.
GOLDEN_VERDICT = "yes"

_bearer = HTTPBearer(
    auto_error=False,
    description="A session token from the auth service (POST /auth/token), or the review token.",
)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="The review token, by another name.")

#: On every route that needs a caller, so the OpenAPI document says how to
#: authenticate. They decide nothing: `Guard` does.
DOCUMENTED = [Depends(_bearer), Depends(_api_key)]


def author(caller: Identity, claimed: str | None) -> str | None:
    """Who did it: a signed-in person, or the token's own name -- never what
    a header or a field said (V6-62).

    Only an anonymous caller -- sign-in off and no token, where nobody can be
    told apart -- is recorded under the name it gives (`X-Reviewer`), as
    before there was anybody to sign in.
    """
    if caller.kind == ANONYMOUS:
        return claimed
    return caller.actor


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


@dataclass
class ReviewContext:
    """Everything a route may reach, the helpers they share, and the three
    guards the routers carry."""

    config: ReviewSettings
    repo: Repository
    do_promote: Callable[..., Any]
    do_preview: Callable[..., Any]
    do_withdraw: Callable[..., Any]
    stores: dict[str, FixStore]
    do_validate: Callable[..., validation_module.Validation]
    make_embedder: Callable[[], Any]
    ping_retail: Callable[[], None]
    do_validate_snippet: Callable[..., Any]
    book: Any
    read_schema: Callable[[], list[dict[str, Any]]]
    guard: Guard
    started: float

    def __post_init__(self) -> None:
        self.reviewing: Callable[[Request], Identity] = self.guard.require(*self.config.reviewer_roles)
        self.curating: Callable[[Request], Identity] = self.guard.require(*self.config.curator_roles)
        self.reading: Callable[[Request], Identity] = self.guard.require(
            *self.config.reviewer_roles, *self.config.curator_roles
        )

    # --- helpers ----------------------------------------------------------
    def require(self, submission_id: str) -> Submission:
        found = self.repo.get(submission_id)
        if found is None:
            raise ReviewHTTPError(
                HTTP_404_NOT_FOUND, "not_found", f"no submission {submission_id}"
            )
        return found

    def require_fixable(self, submission_id: str) -> Submission:
        """A submission whose verdict is wrong or incomplete, and so is fixed."""
        found = self.require(submission_id)
        if found.verdict == GOLDEN_VERDICT:
            raise ReviewHTTPError(
                HTTP_409_CONFLICT,
                "wrong_workflow",
                f"{submission_id} was marked correct; a correct answer is promoted into "
                "the golden set (POST /v1/submissions/{id}/promote), not fixed",
            )
        return found

    def fix_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for slug, store in self.stores.items():
            try:
                counts[slug] = store.count()
            except DATABASE_ERRORS:  # meta must answer with a store down
                counts[slug] = 0
        return counts

    def golden(self, with_sources: bool = False) -> GoldenSet:
        """The set as the document holds it, or why it could not be read.

        `with_sources` also asks the staging database which pairs the review
        queue produced -- best effort, as the counts in `/v1/meta` are.
        """
        try:
            document = self.config.document_path.read_text()
        except OSError as exc:
            return GoldenSet(
                pairs=[], count=0, document=self.config.document, error=f"cannot read: {exc}"
            )
        promotion_module._ensure_importable(self.config.rag_dir)
        try:
            gp = promotion_module._parser()
            pairs = promotion_module._parse_text(gp, document)
        except PARSE_ERRORS as exc:  # reported, never raised at a browser
            return GoldenSet(
                pairs=[], count=0, document=self.config.document, error=f"cannot parse: {exc}"
            )
        sources: dict[str, str] = {}
        if with_sources:
            try:
                sources = self.repo.outcomes()
            except DATABASE_ERRORS:  # the set reads without the staging database
                sources = {}
        return GoldenSet(
            pairs=[self.pair_model(p, sources.get(p.pair_id)) for p in pairs],
            count=len(pairs),
            document=self.config.document,
            next_pair_id=render.next_pair_id(document),
            suite_in_force=render.suite_in_force(document),
        )

    def pair_model(self, pair: Any, submission_id: str | None = None) -> GoldenPairModel:
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

    def golden_check(self, sql: str, principal: str | None = None) -> validation_module.Validation:
        """A golden pair's SQL, run: it has to run, and it has to return rows.

        Rows, because the golden set's own rule is that an empty result reads
        as a failure: a pair whose answer is nothing teaches nothing and
        measures nothing.
        """
        checked = self.do_validate(sql, "", principal=principal)
        if checked.valid and checked.row_count == 0:
            checked.valid = False
            checked.problems.append(
                "it ran and returned no rows; a golden pair's SQL returns its answer, and in "
                "the golden set an empty result reads as a failure"
            )
            checked.warnings = [w for w in checked.warnings if "no rows" not in w]
        return checked

    def promotion_model(self, result: promotion_module.Promotion) -> PromotionModel:
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

    def withdrawal_model(self, result: promotion_module.Withdrawal) -> WithdrawalModel:
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

    def embed(self, store: FixStore) -> EmbedResult:
        """The RAG half of a fix. Best-effort: the record is the fact."""
        if not self.config.embed_fixes:
            return EmbedResult(ran=False)
        try:
            return store.embed_pending(self.make_embedder())
        # The loaders' package missing (ImportError), the model host, or the
        # store the vectors go to: each costs the vector, never the fix.
        except MODEL_ERRORS + DATABASE_ERRORS + (ImportError,) as exc:
            return EmbedResult(ran=False, error=f"{type(exc).__name__}: {exc}")

    def store_unavailable(self, slug: str, verb: str, exc: Exception) -> ReviewHTTPError:
        return ReviewHTTPError(
            HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
            f"the {slug} store could not be {verb}: {type(exc).__name__}: {exc}",
        )

    # --- taking a judgement back ------------------------------------------------
    def withdraw(self, found: Submission) -> tuple[WithdrawalModel | None, dict[str, Any] | None]:
        """Take back out whatever this submission produced, before its row changes.

        Returns what came out, and the draft the submission should carry if it
        is reopened: the pair as the document held it, or the fix's SQL --
        the work, kept. Done before the row is touched, so a failure here
        leaves everything as it was, and a failure after it is healed by
        trying again: the pair or the fix is then simply not found.
        """
        if found.state == "promoted":
            try:
                result = self.do_withdraw(self.config, found.promoted_pair_id or "")
            except PromotionError as exc:
                raise ReviewHTTPError(
                    HTTP_422_UNPROCESSABLE, "not_withdrawable", "; ".join(exc.reasons)
                ) from exc
            model = self.withdrawal_model(result)
            return model, result.draft.as_dict() if result.draft is not None else None
        if found.state == "corrected":
            kind = KINDS[found.verdict]
            try:
                removed = self.stores[kind.slug].delete(found.id)
            except DATABASE_ERRORS as exc:  # reported, and nothing was changed
                raise self.store_unavailable(kind.slug, "written", exc) from exc
            model = WithdrawalModel(
                kind=kind.slug, id=found.promoted_pair_id or "", found=removed is not None
            )
            return model, {"sql_code": removed.corrected_sql} if removed is not None else None
        return None, None

    # --- curation: SQL snippets ---------------------------------------------
    def snippet_model(self, snippet: Any) -> SnippetModel:
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

    def snippet_check(
        self,
        draft: SnippetDraft, principal: str | None = None
    ) -> snippet_validation_module.SnippetValidation:
        """Run the snippet, and hold its listed tables to the ones it uses.

        A table the SQL uses and `tables` does not list is a problem, not a
        warning: the agent shows a snippet only when every table it lists is
        in scope, so an unlisted one could be shown with that table out of
        scope -- straight into the static validator's refusal. An empty list
        is filled in from what the SQL uses.
        """
        checked = self.do_validate_snippet(draft.kind, draft.applies_to, draft.sql, principal=principal)
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

    def snippet_result(
        self,
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

    def checked_draft(
        self,
        body: SnippetRequest, principal: str | None = None
    ) -> tuple[SnippetDraft, snippet_validation_module.SnippetValidation]:
        draft = SnippetDraft.from_mapping(body.draft.model_dump())
        checked = self.snippet_check(draft, principal)
        if not checked.valid:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_valid", "; ".join(checked.problems))
        return draft, checked

    def write_snippet(self, write: Callable[[], snippets_module.Outcome]) -> snippets_module.Outcome:
        try:
            return write()
        except SnippetMissing as exc:
            raise ReviewHTTPError(HTTP_404_NOT_FOUND, "not_found", "; ".join(exc.reasons)) from exc
        except PromotionError as exc:
            raise ReviewHTTPError(HTTP_422_UNPROCESSABLE, "not_writable", "; ".join(exc.reasons)) from exc


def public_routes(ctx: ReviewContext) -> APIRouter:
    """What this is, its health and its readiness: open by design, and the
    only router that is."""
    router = APIRouter()
    config, repo, stores = ctx.config, ctx.repo, ctx.stores
    ping_retail, book, guard = ctx.ping_retail, ctx.book, ctx.guard
    started, _golden = ctx.started, ctx.golden

    # --- the unauthenticated routes ---------------------------------------
    @router.get("/", tags=["service"], summary="What this is and where to go next")
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

    @router.get("/healthz", tags=["service"], response_model=Health, summary="Is the process alive")
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - started, 3))

    @router.get(
        "/readyz",
        tags=["service"],
        response_model=Readiness,
        summary="Can it review and promote right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(request: Request, response: Response) -> Readiness:
        checks: dict[str, Check] = {}
        try:
            repo.ping()
            checks["staging_database"] = Check(ok=True, detail=_redacted(config.feedback_db_url))
        except DATABASE_ERRORS as exc:  # the detail is the whole point
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
        except DATABASE_ERRORS as exc:  # the detail is the whole point
            checks["retail_database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")
        for slug, store in stores.items():
            try:
                store.ping()
                checks[f"{slug}_store"] = Check(ok=True, detail=_redacted(store.url))
            except DATABASE_ERRORS as exc:
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
        return Readiness(ready=ready, checks=checks, warnings=config.warnings()).for_caller(operator=guard.operator(request))

    return router


def reading_routes(ctx: ReviewContext) -> APIRouter:
    """What there is to read: anyone who reviews or curates."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.reading)])
    config, repo, stores = ctx.config, ctx.repo, ctx.stores
    book, read_schema, guard = ctx.book, ctx.read_schema, ctx.guard
    reading, _fix_counts, _golden = ctx.reading, ctx.fix_counts, ctx.golden
    _store_unavailable, _snippet_model = ctx.store_unavailable, ctx.snippet_model

    # --- the API ----------------------------------------------------------
    @router.get(
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
        except DATABASE_ERRORS:  # meta must answer even with no database
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

    @router.get(
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
        except DATABASE_ERRORS as exc:
            raise _store_unavailable(kind, "read", exc) from exc
        return FixList(kind=kind, fixes=[FixModel(**f.as_dict()) for f in found], count=len(found))

    @router.get(
        "/v1/golden",
        tags=["promotion"],
        response_model=GoldenSet,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="The golden question set as the document holds it",
    )
    def golden() -> GoldenSet:
        return _golden(with_sources=True)

    @router.get(
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

    @router.get(
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

    @router.get(
        "/v1/schema",
        tags=["snippets"],
        response_model=SchemaModel,
        dependencies=[*DOCUMENTED, Depends(reading)],
        summary="The retail tables and columns a snippet can be written over",
    )
    def schema() -> SchemaModel:
        try:
            return SchemaModel(tables=read_schema())
        except DATABASE_ERRORS as exc:  # shown in place of the list
            return SchemaModel(error=f"{type(exc).__name__}: {exc}")

    return router


def reviewing_routes(ctx: ReviewContext) -> APIRouter:
    """The submissions, judged and acted on: reviewers."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.reviewing)])
    config, repo, do_promote = ctx.config, ctx.repo, ctx.do_promote
    do_preview, stores, do_validate = ctx.do_preview, ctx.stores, ctx.do_validate
    reviewing, _require, _require_fixable = ctx.reviewing, ctx.require, ctx.require_fixable
    _golden, _golden_check, _promotion_model = ctx.golden, ctx.golden_check, ctx.promotion_model
    _embed, _store_unavailable, _withdraw = ctx.embed, ctx.store_unavailable, ctx.withdraw

    @router.get(
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

    @router.get(
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

    @router.patch(
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

    @router.post(
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

    @router.post(
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

    @router.post(
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

    @router.delete(
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
    @router.post(
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

    @router.post(
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
        except DATABASE_ERRORS as exc:  # reported, and nothing was marked
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

    return router


def curating_routes(ctx: ReviewContext) -> APIRouter:
    """Writing the golden set, the fix stores and the snippets directly: curators."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.curating)])
    config, repo, do_promote = ctx.config, ctx.repo, ctx.do_promote
    do_preview, do_withdraw, stores = ctx.do_preview, ctx.do_withdraw, ctx.stores
    do_validate, book, curating = ctx.do_validate, ctx.book, ctx.curating
    _golden, _golden_check, _promotion_model = ctx.golden, ctx.golden_check, ctx.promotion_model
    _withdrawal_model, _embed, _store_unavailable = ctx.withdrawal_model, ctx.embed, ctx.store_unavailable
    _withdraw, _snippet_check, _snippet_result = ctx.withdraw, ctx.snippet_check, ctx.snippet_result
    _checked_draft, _write_snippet = ctx.checked_draft, ctx.write_snippet

    # --- curation (5.6): golden pairs written by hand ------------------------
    @router.post(
        "/v1/golden/validate",
        tags=["curation"],
        response_model=ValidationModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Run a golden pair's SQL against the live retail database",
    )
    def validate_golden(body: ValidateRequest, caller: Identity = Depends(curating)) -> ValidationModel:
        return ValidationModel(**_golden_check(body.sql, caller.principal).as_dict())

    @router.post(
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

    @router.post(
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

    @router.delete(
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
        except DATABASE_ERRORS as exc:  # not knowing would leave the queue claiming the pair
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
    @router.post(
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

    @router.post(
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
        except DATABASE_ERRORS as exc:  # reported, and nothing was stored
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

    @router.delete(
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
        except DATABASE_ERRORS as exc:
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
        except DATABASE_ERRORS as exc:
            raise _store_unavailable(kind, "written", exc) from exc
        return FixRemovalModel(
            kind=kind,
            fix_id=fix_id,
            found=removed is not None,
            fix=FixModel(**removed.as_dict()) if removed is not None else None,
        )

    @router.post(
        "/v1/snippets/validate",
        tags=["snippets"],
        response_model=SnippetValidationModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Run a snippet inside its probe query against the live retail database",
    )
    def validate_snippet(body: SnippetRequest, caller: Identity = Depends(curating)) -> SnippetValidationModel:
        draft = SnippetDraft.from_mapping(body.draft.model_dump())
        return SnippetValidationModel(**_snippet_check(draft, caller.principal).as_dict())

    @router.post(
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

    @router.post(
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

    @router.put(
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

    @router.delete(
        "/v1/snippets/{snippet_id}",
        tags=["snippets"],
        response_model=SnippetResultModel,
        dependencies=[*DOCUMENTED, Depends(curating)],
        summary="Take a snippet out of its document, and out of the store",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}, HTTP_422_UNPROCESSABLE: {"model": ApiError}},
    )
    def remove_snippet(snippet_id: str) -> SnippetResultModel:
        return _snippet_result(_write_snippet(lambda: book.delete(config, snippet_id)), None)

    return router


def routers(ctx: ReviewContext) -> list[APIRouter]:
    """Every router, the open one first."""
    return [public_routes(ctx), reading_routes(ctx), reviewing_routes(ctx), curating_routes(ctx)]


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
