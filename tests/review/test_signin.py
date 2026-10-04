"""The review service with sign-in on.

Reviewers review, curators curate, both read; what a person does is recorded
as theirs whatever a header claims; the SQL they check runs as them.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_identity import CURATORS, REVIEWERS, USERS, Guard, GuardSettings, Identity, sign
from nl2sql_identity.tokens import SERVICE
from nl2sql_review import snippet_validation, validation
from nl2sql_review.app import author, default_guard
from nl2sql_review.settings import ReviewSettings

from .conftest import FakeRepository, complete

KEY = Ed25519PrivateKey.generate()
GOOD_SQL = "SELECT sku_id, product_name FROM dim_product"


def bearer(user: str, *roles: str) -> dict[str, str]:
    token = sign(Identity(user=user, roles=frozenset(roles)), KEY, lifetime_seconds=600)
    return {"Authorization": f"Bearer {token}"}


RITA = ("rita", REVIEWERS, USERS)
CORA = ("cora", CURATORS, USERS)


@pytest.fixture
def guard(settings):
    return Guard(
        GuardSettings(enabled=True, service_token="test-token", service_roles=frozenset({REVIEWERS, CURATORS})),
        public_key=KEY.public_key(),
    )


@pytest.fixture
def signed(make_client, guard):
    def build(**overrides):
        client = make_client(guard=guard, **overrides)
        client.headers.pop("Authorization")
        return client

    return build


def test_the_review_routes_need_a_reviewer(signed):
    client = signed()
    assert client.get("/v1/submissions").status_code == 401
    assert client.get("/v1/submissions", headers=bearer(*RITA)).status_code == 200
    refused = client.get("/v1/submissions", headers=bearer(*CORA))
    assert refused.status_code == 403 and "nl2sql_reviewers" in refused.json()["error"]["message"]


def test_the_curation_routes_need_a_curator(signed, snippet_draft):
    client = signed()
    body = {"draft": snippet_draft.as_dict()}
    assert client.post("/v1/snippets/validate", json=body, headers=bearer(*CORA)).status_code == 200
    assert client.post("/v1/snippets/validate", json=body, headers=bearer(*RITA)).status_code == 403


@pytest.mark.parametrize("path", ["/v1/meta", "/v1/golden", "/v1/promotions", "/v1/fixes/corrections", "/v1/snippets", "/v1/schema"])
def test_either_may_read(signed, path):
    client = signed()
    for who in (RITA, CORA):
        assert client.get(path, headers=bearer(*who)).status_code == 200, (path, who[0])
    assert client.get(path, headers=bearer("uma", USERS)).status_code == 403


def test_meta_says_sign_in_is_in_use(signed):
    assert signed().get("/v1/meta", headers=bearer(*RITA)).json()["authentication"] == "session"


def test_a_promotion_is_recorded_as_the_signed_in_reviewers_whatever_the_header_says(signed, draft):
    client = signed()
    answer = client.post(
        "/v1/submissions/sub-1/promote",
        json={"draft": complete(draft)},
        headers={**bearer(*RITA), "X-Reviewer": "someone else"},
    )
    assert answer.status_code == 200
    assert client.app.state.repository.promotion_log[0]["reviewer"] == "rita"


def test_a_script_with_the_token_still_names_itself_with_the_header(signed, draft):
    client = signed()
    client.post(
        "/v1/submissions/sub-1/promote",
        json={"draft": complete(draft)},
        headers={"Authorization": "Bearer test-token", "X-Reviewer": "nightly-job"},
    )
    assert client.app.state.repository.promotion_log[0]["reviewer"] == "nightly-job"


def test_a_review_is_recorded_as_the_reviewers(signed, submission):
    client = signed()
    answer = client.patch(
        "/v1/submissions/sub-1",
        json={"state": "accepted", "reviewer": "pretend"},
        headers=bearer(*RITA),
    )
    assert answer.status_code == 200 and answer.json()["reviewer"] == "rita"


def test_a_fix_is_validated_as_the_reviewer_and_recorded_as_theirs(signed, submission, wrong_submission, validator, fix_stores):
    client = signed(repository=FakeRepository([submission, wrong_submission]))
    assert client.post("/v1/submissions/sub-w/validate", json={"sql": GOOD_SQL}, headers=bearer(*RITA)).status_code == 200
    answer = client.post(
        "/v1/submissions/sub-w/fix",
        json={"sql": GOOD_SQL, "review_note": ""},
        headers={**bearer(*RITA), "X-Reviewer": "not-rita"},
    )
    assert answer.status_code == 200
    assert validator.principals == ["rita", "rita"]
    assert fix_stores["corrections"].saved[0].reviewer == "rita"


def test_curated_sql_runs_as_the_curator(signed, validator, fix_stores, snippet_validator, snippet_draft, draft):
    client = signed()
    headers = {**bearer(*CORA), "X-Reviewer": "not-cora"}
    client.post("/v1/fixes/corrections/validate", json={"sql": "SELECT 2", "incorrect_sql": "SELECT 1"}, headers=headers)
    stored = client.post(
        "/v1/fixes/completions",
        json={"question": "q", "sql": GOOD_SQL, "incorrect_sql": "SELECT 1", "review_note": ""},
        headers=headers,
    )
    assert stored.json()["fix"]["reviewer"] == "cora"
    client.post("/v1/golden/validate", json={"sql": GOOD_SQL}, headers=headers)
    client.post("/v1/golden", json={"draft": complete(draft)}, headers=headers)
    assert validator.principals == ["cora", "cora", "cora", "cora"]
    client.post("/v1/snippets", json={"draft": snippet_draft.as_dict()}, headers=headers)
    client.put("/v1/snippets/S001", json={"draft": snippet_draft.as_dict()}, headers=headers)
    assert snippet_validator.principals == ["cora", "cora"]


def test_readiness_counts_sign_in(make_client, tmp_path):
    guard = Guard(GuardSettings(enabled=True, public_key_file=str(tmp_path / "missing.pub")))
    readiness = make_client(guard=guard).get("/readyz").json()
    assert readiness["ready"] is False and readiness["checks"]["sign_in"]["ok"] is False


def test_the_default_guard_follows_the_settings():
    off = default_guard(ReviewSettings(token="t"))
    assert not off.settings.enabled and off._recheck is None
    assert off.settings.service_roles == {REVIEWERS, CURATORS}
    on = default_guard(ReviewSettings(auth_enabled=True, reviewer_roles=("x",), curator_roles=("y",)))
    assert on.settings.enabled and on._recheck is not None and on.settings.service_roles == {"x", "y"}


def test_the_author_is_the_person_when_there_is_one():
    assert author(Identity(user="rita"), "claimed") == "rita"
    assert author(Identity(user="", kind=SERVICE), "claimed") == "claimed"
    assert author(Identity(user="", kind=SERVICE), None) is None


# --- the validators themselves -------------------------------------------------


class Recording:
    """A psycopg connection that records what it was told, as Postgres reads it."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(None)
        self.statements.append(text)
        return self

    def fetchone(self):
        return [[{"Plan": {"Total Cost": 1.0}}]]

    def fetchall(self):
        return [("dim_date",)]

    def fetchmany(self, size):
        return [(1,)]

    @property
    def description(self):
        class Column:
            name = "n"

        return [Column()]

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize("principal", [None, "rita"])
def test_a_fix_is_run_as_the_person_who_checks_it(principal):
    conn = Recording()
    validation.validate("SELECT 1", url="x", principal=principal, connect=lambda url, **k: conn)
    roles = [s for s in conn.statements if s.startswith("SET LOCAL ROLE")]
    assert roles == ([] if principal is None else ['SET LOCAL ROLE "rita"'])
    assert conn.statements[:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 30000"]


@pytest.mark.parametrize("principal", [None, "cora"])
def test_a_snippet_is_run_as_the_person_who_checks_it(principal):
    conn = Recording()
    snippet_validation.validate_snippet(
        "filter", "dim_date d", "d.is_holiday", url="x", principal=principal, connect=lambda url, **k: conn
    )
    roles = [s for s in conn.statements if s.startswith("SET LOCAL ROLE")]
    assert roles == ([] if principal is None else ['SET LOCAL ROLE "cora"'])


def test_settings_say_nothing_about_a_missing_token_once_sign_in_is_on():
    assert not any("REVIEW_TOKEN" in note for note in ReviewSettings(auth_enabled=True).warnings())
    assert any("REVIEW_TOKEN" in note for note in replace(ReviewSettings(), token=None).warnings())
