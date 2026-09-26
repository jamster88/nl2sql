"""The review service's HTTP surface.

Every route is exercised against a fake repository and, where the
filesystem is involved, a real copy of the real question document. That
combination is deliberate: the repository is faked because a privilege
cannot be tested with a fake and is tested for real elsewhere, while the
document is real because the whole promotion contract is a bet about how the
loader's parser reads *that file*.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nl2sql_review.app import __version__, create_app, seed_draft
from nl2sql_review.promote import Promotion, PromotionError, StepResult
from nl2sql_review.render import Draft
from nl2sql_review.store import STATES, Submission

from .conftest import FakeRepository


@pytest.fixture
def make_client(settings, repository):
    def build(**overrides):
        app = create_app(
            settings=overrides.pop("settings", settings),
            repository=overrides.pop("repository", repository),
            **overrides,
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.headers.update({"Authorization": "Bearer test-token"})
        return client

    return build


@pytest.fixture
def client(make_client):
    return make_client()


def complete(draft: Draft) -> dict:
    return draft.as_dict()


# ---------------------------------------------------------------------------
# The service routes
# ---------------------------------------------------------------------------


def test_the_root_names_every_endpoint(client):
    body = client.get("/").json()
    assert body["service"] == "nl2sql-review"
    assert set(body["endpoints"]) == {
        "meta", "submissions", "submission", "preview", "promote",
        "golden", "promotions", "health", "readiness",
    }


def test_health_is_about_the_process(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_readiness_checks_the_database_the_document_and_the_write(client):
    body = client.get("/readyz").json()
    assert body["ready"] is True
    assert set(body["checks"]) == {"staging_database", "golden_document", "document_writable"}
    assert "45 pairs, next is Q46" in body["checks"]["golden_document"]["detail"]


def test_readiness_reports_an_unreachable_staging_database(make_client, repository):
    repository.fail_ping = OSError("connection refused")
    response = make_client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["staging_database"]["ok"] is False
    assert "OSError" in response.json()["checks"]["staging_database"]["detail"]


def test_readiness_never_publishes_the_database_password(make_client, settings):
    """`/readyz` is unauthenticated, so everything it returns is public."""
    secret = "postgresql://feedback:hunter2@db:5432/nl2sql_feedback"
    response = make_client(settings=replace(settings, feedback_db_url=secret)).get("/readyz")
    detail = response.json()["checks"]["staging_database"]["detail"]

    assert "hunter2" not in detail
    assert detail == "postgresql://feedback:***@db:5432/nl2sql_feedback"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://db:5432/x", "postgresql://db:5432/x"),
        ("postgresql://user@db/x", "postgresql://user:***@db/x"),
        ("postgresql://:pw@db/x", "postgresql://db/x"),
    ],
)
def test_redaction_handles_every_url_shape(make_client, settings, url, expected):
    response = make_client(settings=replace(settings, feedback_db_url=url)).get("/readyz")
    assert response.json()["checks"]["staging_database"]["detail"] == expected


def test_readiness_reports_a_missing_document(make_client, settings, document):
    document.unlink()
    response = make_client().get("/readyz")

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["golden_document"]["ok"] is False
    assert "cannot read" in checks["golden_document"]["detail"]
    assert "is not there" in checks["document_writable"]["detail"]


def test_readiness_reports_a_document_that_cannot_be_rewritten(make_client, document):
    """The directory, not the file: promotion renames a new file over the old.

    Finding this out from /readyz beats finding it out after filling in the
    form.
    """
    document.parent.chmod(0o500)
    try:
        response = make_client().get("/readyz")
        assert response.status_code == 503
        assert "not writable" in response.json()["checks"]["document_writable"]["detail"]
    finally:
        document.parent.chmod(0o700)


def test_readiness_reports_a_document_that_does_not_parse(make_client, document):
    document.write_text("## Q46 - half a pair\n\nnothing else\n")
    response = make_client().get("/readyz")
    assert "cannot parse" in response.json()["checks"]["golden_document"]["detail"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_every_route_but_the_open_ones_refuses_an_anonymous_caller(settings, repository):
    """Derived from the app's own route table rather than a hand-kept list.

    A route added without `dependencies=guarded` is then a failing test
    rather than an open door nobody noticed.
    """
    open_by_design = {"/", "/healthz", "/readyz", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    app = create_app(settings=settings, repository=repository)
    anonymous = TestClient(app, raise_server_exceptions=False)

    checked = 0
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path in open_by_design:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            response = anonymous.request(method, path.replace("{submission_id}", "sub-1"), json={})
            assert response.status_code == 401, f"{method} {path} answered {response.status_code}"
            checked += 1
    assert checked >= 6


def test_a_token_may_be_presented_as_an_api_key(make_client, settings):
    client = make_client()
    client.headers.clear()
    assert client.get("/v1/meta", headers={"X-API-Key": "test-token"}).status_code == 200


def test_the_wrong_token_is_refused_with_a_challenge(make_client):
    client = make_client()
    client.headers.update({"Authorization": "Bearer wrong"})
    response = client.get("/v1/meta")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_no_token_configured_means_no_token_required(make_client, settings):
    client = make_client(settings=replace(settings, token=None))
    client.headers.clear()
    assert client.get("/v1/meta").status_code == 200


def test_a_query_string_token_is_not_accepted(make_client):
    """Unlike the agent API, which accepts one because EventSource must.

    Nothing here streams, so the token never has to go somewhere that ends
    up in an access log.
    """
    client = make_client()
    client.headers.clear()
    assert client.get("/v1/meta?access_token=test-token").status_code == 401


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


def test_meta_tells_a_client_everything_it_needs(client):
    body = client.get("/v1/meta").json()
    assert body["states"] == list(STATES)
    assert body["golden_count"] == 45
    assert body["next_pair_id"] == "Q46"
    assert body["authentication"] == "bearer"
    assert body["limits"]["max_pair_number"] == 99
    assert body["counts"]["pending"] == 1


def test_meta_answers_even_when_the_database_is_down(make_client, repository):
    repository.fail_ping = OSError("down")

    def explode(*args, **kwargs):
        raise OSError("down")

    repository.counts = explode
    body = make_client().get("/v1/meta").json()
    # Zeroes, not a 500: the review GUI should render and say what is wrong.
    assert body["counts"] == {state: 0 for state in STATES}


def test_meta_repeats_the_configuration_warnings(make_client, settings):
    body = make_client(settings=replace(settings, token=None)).get("/v1/meta").json()
    assert any("REVIEW_TOKEN" in warning for warning in body["warnings"])


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def test_the_queue_is_returned_with_its_counts(client):
    body = client.get("/v1/submissions").json()
    assert body["count"] == 1
    assert body["submissions"][0]["job_id"] == "job-abc"
    assert body["counts"]["pending"] == 1


def test_the_queue_filters_by_state_and_verdict(make_client, submission):
    other = Submission(id="sub-2", job_id="job-2", verdict="no", question="q2", state="accepted")
    client = make_client(repository=FakeRepository([submission, other]))

    assert client.get("/v1/submissions?state=accepted").json()["count"] == 1
    assert client.get("/v1/submissions?verdict=no").json()["count"] == 1
    assert client.get("/v1/submissions?state=pending&verdict=no").json()["count"] == 0


def test_a_correct_but_incomplete_verdict_is_listed_and_filterable(make_client, submission):
    incomplete = Submission(id="sub-3", job_id="job-3", verdict="incomplete", question="q3")
    client = make_client(repository=FakeRepository([submission, incomplete]))

    body = client.get("/v1/submissions?verdict=incomplete").json()
    assert body["count"] == 1
    assert body["submissions"][0]["verdict"] == "incomplete"


@pytest.mark.parametrize("query", ["state=nonsense", "verdict=maybe"])
def test_an_unknown_filter_is_refused_rather_than_silently_ignored(client, query):
    response = client.get(f"/v1/submissions?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_the_queue_pages(make_client, submission):
    many = [replace(submission, id=f"sub-{n}", job_id=f"job-{n}") for n in range(5)]
    client = make_client(repository=FakeRepository(many))
    assert client.get("/v1/submissions?limit=2").json()["count"] == 2
    assert client.get("/v1/submissions?limit=2&offset=4").json()["count"] == 1


# ---------------------------------------------------------------------------
# One submission
# ---------------------------------------------------------------------------


def test_one_submission_comes_back_with_a_seeded_draft(client):
    body = client.get("/v1/submissions/sub-1").json()
    draft = body["draft"]

    assert draft["question"] == body["question"]
    assert draft["sql_code"] == body["sql_code"]
    assert draft["tables"] == body["tables"]
    # The three a thumbs-up cannot carry start empty, and are meant to.
    assert (draft["keywords"], draft["reasoning_target"], draft["result"]) == ("", "", "")


def test_a_saved_draft_is_not_overwritten_by_the_seed(make_client, submission):
    submission.draft = {"title": "mine", "keywords": "kept"}
    body = make_client().get("/v1/submissions/sub-1").json()
    assert body["draft"]["title"] == "mine"
    assert body["draft"]["keywords"] == "kept"


def test_an_unknown_submission_is_a_404(client):
    response = client.get("/v1/submissions/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_seed_title_is_shortened_and_loses_its_question_mark(submission):
    submission.question = "What " + "very " * 40 + "long question is this?"
    seeded = seed_draft(submission)
    assert len(seeded.title) <= 84
    assert seeded.title.endswith("...")
    assert not seeded.title.endswith("?...")


def test_a_short_question_becomes_the_title_unchanged(submission):
    submission.question = "How many stores are there?"
    assert seed_draft(submission).title == "How many stores are there"


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------


def test_a_submission_can_be_accepted(client):
    body = client.patch(
        "/v1/submissions/sub-1", json={"state": "accepted", "reviewer": "sam", "review_note": "good"}
    ).json()
    assert body["state"] == "accepted"
    assert body["reviewer"] == "sam"


def test_a_draft_can_be_saved_without_a_judgement(client, draft):
    body = client.patch("/v1/submissions/sub-1", json={"draft": complete(draft)}).json()
    assert body["draft"]["keywords"] == draft.keywords
    assert body["state"] == "pending"


def test_the_submitted_evidence_is_not_editable(client):
    """The question and the SQL are what the agent did and the user judged.

    An API that let a curator rewrite them would turn the staging table into
    a place where evidence changes.
    """
    response = client.patch("/v1/submissions/sub-1", json={"question": "something else"})
    assert response.status_code == 422


def test_promoted_cannot_be_set_by_hand(client):
    """It is something that happens, not something that is set.

    Setting it directly would mark a submission as being in the golden set
    with nothing written to the document -- the one inconsistency this
    service exists to prevent.
    """
    response = client.patch("/v1/submissions/sub-1", json={"state": "promoted"})
    assert response.status_code == 422
    assert "POSTing" in str(response.json()["error"])


def test_an_already_promoted_submission_is_not_editable(make_client, submission):
    submission.state = "promoted"
    submission.promoted_pair_id = "Q46"
    response = make_client().patch("/v1/submissions/sub-1", json={"review_note": "actually no"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_promoted"


def test_patching_something_that_is_not_there_is_a_404(client):
    assert client.patch("/v1/submissions/nope", json={"state": "accepted"}).status_code == 404


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_returns_the_block_that_would_be_written(client, draft, document):
    before = document.read_text()
    body = client.post("/v1/submissions/sub-1/preview", json={"draft": complete(draft)}).json()

    assert body["valid"] is True
    assert body["pair_id"] == "Q46"
    assert body["markdown"].startswith("## Q46 - ")
    assert body["suite_in_force"].startswith("Suite 25")
    assert document.read_text() == before


def test_preview_lists_the_problems_instead(client):
    body = client.post(
        "/v1/submissions/sub-1/preview", json={"draft": {"title": "t", "question": "q?"}}
    ).json()
    assert body["valid"] is False
    assert len(body["problems"]) > 1
    assert body["markdown"] == ""


def test_preview_needs_a_submission_that_exists(client, draft):
    assert client.post("/v1/submissions/nope/preview", json={"draft": complete(draft)}).status_code == 404


def test_a_draft_field_that_is_too_long_is_refused(client, draft):
    payload = complete(draft)
    payload["title"] = "x" * 500
    assert client.post("/v1/submissions/sub-1/preview", json=payload | {"draft": payload}).status_code == 422


def test_an_unknown_draft_field_is_refused(client, draft):
    payload = complete(draft) | {"nonsense": "x"}
    response = client.post("/v1/submissions/sub-1/preview", json={"draft": payload})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_too_much_extra_meta_is_refused(client, draft):
    payload = complete(draft)
    payload["extra_meta"] = {f"k{n}": "v" for n in range(11)}
    assert client.post("/v1/submissions/sub-1/preview", json={"draft": payload}).status_code == 422


def test_an_over_long_meta_entry_is_refused(client, draft):
    payload = complete(draft)
    payload["extra_meta"] = {"k" * 100: "v"}
    assert client.post("/v1/submissions/sub-1/preview", json={"draft": payload}).status_code == 422


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_promoting_writes_the_pair_and_records_it(client, draft, document, repository):
    body = client.post(
        "/v1/submissions/sub-1/promote",
        json={"draft": complete(draft)},
        headers={"X-Reviewer": "sam"},
    ).json()

    assert body["pair_id"] == "Q46"
    assert (body["pairs_before"], body["pairs_after"]) == (45, 46)
    assert "## Q46 - " in document.read_text()

    assert repository.submissions["sub-1"].state == "promoted"
    assert repository.submissions["sub-1"].promoted_pair_id == "Q46"
    assert repository.promotion_log[0]["reviewer"] == "sam"


def test_the_reviewer_falls_back_to_the_one_on_the_record(make_client, draft, submission):
    submission.reviewer = "already-known"
    client = make_client()
    client.post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})
    assert client.app.state.repository.promotion_log[0]["reviewer"] == "already-known"


def test_a_draft_that_cannot_become_a_pair_is_refused_with_every_reason(client, document):
    before = document.read_text()
    response = client.post(
        "/v1/submissions/sub-1/promote", json={"draft": {"title": "t", "question": "q?"}}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_promotable"
    assert "keywords is empty" in response.json()["error"]["message"]
    assert document.read_text() == before


def test_an_already_promoted_submission_cannot_be_promoted_twice(make_client, submission, draft):
    submission.state = "promoted"
    submission.promoted_pair_id = "Q46"
    response = make_client().post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_promoted"


def test_a_rejected_submission_must_be_accepted_first(make_client, submission, draft):
    submission.state = "rejected"
    response = make_client().post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "rejected"


def test_promoting_something_that_is_not_there_is_a_404(client, draft):
    assert client.post("/v1/submissions/nope/promote", json={"draft": complete(draft)}).status_code == 404


def test_a_partial_reload_is_reported_as_partial(make_client, draft):
    """Neither a success nor a failure, and it must not be shown as either."""

    def half_done(settings, value):
        return Promotion(
            pair_id="Q46", chunk_id="eval:q46", suite="", title="", markdown="",
            document=settings.document, pairs_before=45, pairs_after=46,
            steps=[
                StepResult("load_golden_pairs", True, True, "46 rows"),
                StepResult("embed_golden_pairs", True, False, "no embedding host"),
            ],
        )

    body = make_client(promoter=half_done).post(
        "/v1/submissions/sub-1/promote", json={"draft": complete(draft)}
    ).json()

    assert body["pair_id"] == "Q46"
    assert body["reloaded"] is False
    assert [step["ok"] for step in body["steps"]] == [True, False]


def test_an_injected_previewer_is_used(make_client, draft):
    body = make_client(
        previewer=lambda settings, value: ("Q99", "## Q99 - injected\n", [])
    ).post("/v1/submissions/sub-1/preview", json={"draft": complete(draft)}).json()
    assert body["pair_id"] == "Q99"


def test_a_promotion_error_raised_late_is_still_a_422(make_client, draft):
    def refuse(settings, value):
        raise PromotionError(["the document changed under us"])

    response = make_client(promoter=refuse).post(
        "/v1/submissions/sub-1/promote", json={"draft": complete(draft)}
    )
    assert response.status_code == 422
    assert "changed under us" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# The golden set and the log
# ---------------------------------------------------------------------------


def test_the_golden_set_is_read_from_the_document(client):
    body = client.get("/v1/golden").json()
    assert body["count"] == 45
    assert body["next_pair_id"] == "Q46"
    assert body["error"] is None
    first = body["pairs"][0]
    assert first["pair_id"] == "Q01"
    assert first["chunk_id"] == "eval:q01"
    assert first["tables"] and first["keywords"]


def test_an_unreadable_golden_set_says_so_rather_than_looking_empty(make_client, document):
    document.unlink()
    body = make_client().get("/v1/golden").json()
    assert body["count"] == 0
    assert "cannot read" in body["error"]


def test_a_full_golden_set_still_lists_its_pairs(make_client, document):
    document.write_text(document.read_text().replace("## Q45 - ", "## Q99 - "))
    body = make_client().get("/v1/golden").json()
    assert body["count"] == 45
    assert body["next_pair_id"] == ""
    assert "golden set is full" in body["error"]


def test_the_promotion_log_is_returned_without_every_pairs_full_text(client, draft):
    client.post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})
    body = client.get("/v1/promotions").json()

    assert body["count"] == 1
    assert body["promotions"][0]["pair_id"] == "Q46"
    assert "markdown" not in body["promotions"][0]


def test_the_promotion_log_is_empty_to_begin_with(client):
    assert client.get("/v1/promotions").json() == {"promotions": [], "count": 0}


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------


def test_the_openapi_document_describes_every_route(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/", "/healthz", "/readyz", "/v1/meta", "/v1/submissions",
        "/v1/submissions/{submission_id}", "/v1/submissions/{submission_id}/preview",
        "/v1/submissions/{submission_id}/promote", "/v1/golden", "/v1/promotions",
    }


def test_the_documentation_can_be_switched_off(make_client, settings):
    client = make_client(settings=replace(settings, docs_enabled=False))
    assert client.get("/docs").status_code == 404
    assert client.get("/").json()["docs"] is None


def test_a_malformed_body_gets_the_same_error_envelope(client):
    response = client.patch("/v1/submissions/sub-1", json={"state": 7})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "errors" in response.json()["error"]["detail"]


def test_cors_is_configurable(make_client, settings):
    client = make_client(settings=replace(settings, cors_origins=("https://review.example",)))
    response = client.get("/v1/meta", headers={"Origin": "https://review.example"})
    assert response.headers["access-control-allow-origin"] == "https://review.example"


# ---------------------------------------------------------------------------
# Seeding, directly
# ---------------------------------------------------------------------------


def test_a_draft_already_saved_is_returned_unchanged(submission):
    """`seed_draft` is called by the route only when there is no draft, so
    this guard is reached by any other caller -- and it is the one that
    stops a curator's work being replaced by a fresh seed."""
    submission.draft = {"title": "mine", "keywords": "kept", "question": "edited"}
    seeded = seed_draft(submission)

    assert seeded.title == "mine"
    assert seeded.keywords == "kept"
    assert seeded.question == "edited"
