"""Fixtures for the review service's tests.

The important one is `document`: a *real* copy of
`context_questions/translated_questions.md`, not a miniature stand-in. The
whole promotion path is a bet that a rendered pair survives
`ragproc.golden_pairs.parse_document`, and that bet is only worth anything
against the document the loader actually reads -- every pair, 25 suites, prose
sections between them and a `## How to read a pair` heading that is not a
pair and must not be counted as one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataclasses import replace

from nl2sql_identity import ADMINS, CURATORS, REVIEWERS
from nl2sql_review import snippets as snippets_module
from nl2sql_review.app import create_app
from nl2sql_review.corrections import COMPLETIONS, CORRECTIONS, AlreadyFixed, EmbedResult, Kind
from nl2sql_review.render import HEADING_RE, Draft, next_pair_id
from nl2sql_review.settings import ReviewSettings
from nl2sql_review.snippet_validation import SnippetValidation
from nl2sql_review.snippets import SnippetDraft
from nl2sql_review.store import Submission
from nl2sql_review.validation import Validation, clean

ROOT = Path(__file__).resolve().parent.parent.parent
REAL_DOCUMENT = ROOT / "context_questions" / "translated_questions.md"
REAL_SNIPPETS = ROOT / "context_questions" / "sql_snippets.md"

#: The golden set as the copied document holds it when a test starts: how
#: many pairs, and the id the next promotion takes. Derived rather than
#: written down as 45 and Q46, because the document is the live golden set
#: and every promotion made through the review interface grows it -- a suite
#: that pinned the count failed for the first person who used the feature.
BASE_PAIRS = len(HEADING_RE.findall(REAL_DOCUMENT.read_text()))
NEXT_ID = next_pair_id(REAL_DOCUMENT.read_text())


def pair_after(pair_id: str, steps: int = 1) -> str:
    """`Q49` -> `Q50`: the id `steps` promotions after this one."""
    return f"Q{int(pair_id[1:]) + steps:02d}"


def renumber_highest(text: str, pair_id: str) -> str:
    """The document with its highest pair renumbered to `pair_id`: how a test
    reaches the edge of the id space without promoting fifty pairs first."""
    highest = f"## Q{int(NEXT_ID[1:]) - 1:02d} - "
    assert highest in text
    return text.replace(highest, f"## {pair_id} - ")


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """A writable copy of the real question document."""
    target = tmp_path / "translated_questions.md"
    shutil.copy(REAL_DOCUMENT, target)
    return target


@pytest.fixture
def snippet_document(tmp_path: Path) -> Path:
    """A writable copy of the real snippet document, beside the golden one."""
    target = tmp_path / "sql_snippets.md"
    shutil.copy(REAL_SNIPPETS, target)
    return target


@pytest.fixture
def settings(document: Path, snippet_document: Path) -> ReviewSettings:
    """Pointed at the copies, with every loader off.

    Off because they need a context store, a vector store, a snippet store
    and an embedding host. The tests that care about the loaders being
    *invoked* fake them; the ones here care about what is written.
    """
    return ReviewSettings(
        document=str(document),
        rag_dir=str(ROOT / "rag"),
        reload_context=False,
        reload_vectors=False,
        snippets_document=str(snippet_document),
        snippets_db_url="postgresql://snippets:secret@nowhere:5432/nl2sql_snippets",
        reload_snippets=False,
        token="test-token",
        # Every role there is, an administrator's among them: the readiness
        # tests read what only an operator is told (V6-32).
        token_roles=(REVIEWERS, CURATORS, ADMINS),
        # The token-guarded service of 5.x; sign-in has its own file.
        auth_enabled=False,
    )


@pytest.fixture
def draft() -> Draft:
    """A complete, promotable draft."""
    return Draft(
        title="Total net sales for a department in one fiscal year",
        question="What were total net sales for the Produce department in FY2025?",
        tables="fact_pos_retail_sales, dim_product, dim_date",
        keywords="net sales, produce, department, fiscal year",
        reasoning_target=(
            "Filtering a fact table by a dimension attribute across a fiscal year, "
            "where the naive date filter uses the calendar year instead."
        ),
        sql_code=(
            "SELECT ROUND(SUM(s.net_sales_amt), 2) AS net_sales\n"
            "FROM fact_pos_retail_sales s\n"
            "JOIN dim_product p ON p.product_key = s.product_key\n"
            "JOIN dim_date d ON d.date_key = s.sales_date_key\n"
            "WHERE p.department_name = 'Produce' AND d.fiscal_year = 2025;"
        ),
        result="One row, one column.",
    )


@pytest.fixture
def submission() -> Submission:
    return Submission(
        id="sub-1",
        job_id="job-abc",
        verdict="yes",
        question="What were total net sales for the Produce department in FY2025?",
        sql_code="SELECT sum(net_sales_amt) FROM fact_pos_retail_sales;",
        answer="Produce took $719,279.97.",
        narrative="Produce took $719,279.97 in net sales across FY2025.",
        intent="aggregate",
        tables="fact_pos_retail_sales, dim_product",
        row_count=1,
        columns=["net_sales"],
        comment="",
        agent_version="4.2.0",
    )


class FakeRepository:
    """The staging tables, in memory.

    Every route is exercised against this rather than against Postgres, for
    the same reason the agent API is tested against a fake pipeline: it is
    what makes the whole HTTP surface run on every test invocation instead
    of only when a container happens to be up. The live behaviour -- the
    grants, the row-level policies, the unique constraint -- is tested
    separately in `test_store_live.py`, against a real database, because a
    fake cannot tell the truth about a privilege.
    """

    def __init__(self, submissions: list[Submission] | None = None) -> None:
        self.submissions = {s.id: s for s in (submissions or [])}
        self.promotion_log: list[dict] = []
        self.pings = 0
        self.fail_ping: Exception | None = None
        self.fail_outcome: Exception | None = None

    def ping(self) -> None:
        self.pings += 1
        if self.fail_ping is not None:
            raise self.fail_ping

    def get(self, submission_id):
        return self.submissions.get(submission_id)

    def get_by_outcome(self, outcome_id):
        if self.fail_outcome is not None:
            raise self.fail_outcome
        return next(
            (
                s
                for s in self.submissions.values()
                if s.promoted_pair_id == outcome_id and s.state in ("promoted", "corrected")
            ),
            None,
        )

    def outcomes(self):
        if self.fail_outcome is not None:
            raise self.fail_outcome
        return {
            s.promoted_pair_id: s.id
            for s in self.submissions.values()
            if s.promoted_pair_id and s.state in ("promoted", "corrected")
        }

    def listing(self, *, state=None, verdict=None, limit=50, offset=0):
        found = list(self.submissions.values())
        if state:
            found = [s for s in found if s.state == state]
        if verdict:
            found = [s for s in found if s.verdict == verdict]
        return found[offset : offset + limit]

    def counts(self):
        from nl2sql_review.store import STATES

        found = {state: 0 for state in STATES}
        for s in self.submissions.values():
            found[s.state] = found.get(s.state, 0) + 1
        return found

    def counts_by_verdict(self):
        from nl2sql_review.store import STATES, VERDICTS

        found = {v: {state: 0 for state in STATES} for v in VERDICTS}
        for s in self.submissions.values():
            found[s.verdict][s.state] += 1
        return found

    def mark_corrected(self, submission_id, *, fix_id, reviewer, review_note):
        found = self.submissions.get(submission_id)
        if found is None:
            return None
        found.state = "corrected"
        found.promoted_pair_id = fix_id
        found.reviewer = reviewer
        found.review_note = review_note
        return found

    def review(self, submission_id, *, state=None, reviewer=None, review_note=None, draft=None):
        found = self.submissions.get(submission_id)
        if found is None:
            return None
        if state is not None:
            found.state = state
        if reviewer is not None:
            found.reviewer = reviewer
        if review_note is not None:
            found.review_note = review_note
        if draft is not None:
            found.draft = draft
        return found

    def reopen(self, submission_id, *, draft=None):
        found = self.submissions.get(submission_id)
        if found is None:
            return None
        self.promotion_log = [row for row in self.promotion_log if row["submission_id"] != submission_id]
        found.state = "pending"
        found.promoted_pair_id = None
        found.reviewed_at = None
        if draft is not None:
            found.draft = draft
        return found

    def delete(self, submission_id):
        self.promotion_log = [row for row in self.promotion_log if row["submission_id"] != submission_id]
        return self.submissions.pop(submission_id, None)

    def mark_promoted(self, submission_id, **kwargs):
        found = self.submissions[submission_id]
        found.state = "promoted"
        found.promoted_pair_id = kwargs["pair_id"]
        self.promotion_log.append({"submission_id": submission_id, **kwargs})

    def promotions(self, limit=50):
        # Shaped like the real query's rows: `markdown` is present and the
        # route is expected to drop it, because a promotion log listing does
        # not need to carry every pair's full text.
        return [
            {
                "pair_id": row["pair_id"],
                "submission_id": row["submission_id"],
                "chunk_id": row["chunk_id"],
                "suite": row["suite"],
                "title": row["title"],
                "markdown": row["markdown"],
                "reviewer": row["reviewer"],
                "promoted_at": None,
                "reloaded": row["reloaded"],
                "reload_detail": row["reload_detail"],
            }
            for row in self.promotion_log[:limit]
        ]


@pytest.fixture
def repository(submission: Submission) -> FakeRepository:
    return FakeRepository([submission])


class FakeFixStore:
    """One fix store, in memory. The real one is tested live, in
    `test_corrections_live.py`, against a scratch database."""

    def __init__(self, kind: Kind, url: str = "postgresql://fixes:secret@nowhere:5432/fixes") -> None:
        self.kind = kind
        self.url = url
        self.saved: list = []
        self.embedders: list = []
        self.fail_ping: Exception | None = None
        self.fail_save: Exception | None = None
        self.fail_count: Exception | None = None
        self.fail_listing: Exception | None = None
        self.fail_delete: Exception | None = None
        self.fail_get: Exception | None = None
        self.already: str | None = None
        self.embed_result = EmbedResult(embedded=1, pending=0, ran=True)

    def ping(self) -> None:
        if self.fail_ping is not None:
            raise self.fail_ping

    def count(self) -> int:
        if self.fail_count is not None:
            raise self.fail_count
        return len(self.saved)

    def save(self, fix):
        if self.already is not None:
            raise AlreadyFixed(fix.submission_id, self.already)
        if self.fail_save is not None:
            raise self.fail_save
        fix.fix_id = f"{self.kind.prefix}{len(self.saved) + 1:04d}"
        self.saved.append(fix)
        return fix

    def delete(self, submission_id):
        if self.fail_delete is not None:
            raise self.fail_delete
        found = next((fix for fix in self.saved if fix.submission_id == submission_id), None)
        if found is not None:
            self.saved.remove(found)
        return found

    def get(self, fix_id):
        if self.fail_get is not None:
            raise self.fail_get
        return next((fix for fix in self.saved if fix.fix_id == fix_id), None)

    def delete_by_id(self, fix_id):
        if self.fail_delete is not None:
            raise self.fail_delete
        found = next((fix for fix in self.saved if fix.fix_id == fix_id), None)
        if found is not None:
            self.saved.remove(found)
        return found

    def listing(self, limit: int = 50):
        if self.fail_listing is not None:
            raise self.fail_listing
        return list(reversed(self.saved))[:limit]

    def embed_pending(self, embedder) -> EmbedResult:
        self.embedders.append(embedder)
        return self.embed_result


class FakeValidator:
    """Stands in for running SQL against the retail database."""

    def __init__(self, result: Validation | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.principals: list[str | None] = []
        self.result = result or Validation(
            sql="",
            valid=True,
            columns=["sku_id", "product_name"],
            rows=[["SKU1", "Whole Milk"]],
            row_count=1,
            plan_cost=4.5,
            elapsed_ms=3.0,
        )

    def __call__(self, sql: str, reference: str, principal: str | None = None) -> Validation:
        self.calls.append((sql, reference))
        self.principals.append(principal)
        return replace(self.result, sql=clean(sql))


@pytest.fixture
def fix_stores() -> dict[str, FakeFixStore]:
    return {CORRECTIONS.slug: FakeFixStore(CORRECTIONS), COMPLETIONS.slug: FakeFixStore(COMPLETIONS)}


@pytest.fixture
def validator() -> FakeValidator:
    return FakeValidator()


@pytest.fixture
def wrong_submission() -> Submission:
    return Submission(
        id="sub-w",
        job_id="job-w",
        verdict="no",
        question="top 10 SKUs",
        sql_code="SELECT sku_id FROM dim_product",
        answer="| sku_id |\n| SKU1 |",
        narrative="",
        intent="aggregate",
        tables="dim_product",
        row_count=10,
        columns=["sku_id"],
        comment="no names",
        agent_version="5.0.0",
    )


class FakeSnippetValidator:
    """Stands in for running a snippet inside its probe query."""

    def __init__(self, result: SnippetValidation | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.principals: list[str | None] = []
        self.result = result

    def __call__(self, kind: str, applies_to: str, sql: str, principal: str | None = None) -> SnippetValidation:
        self.calls.append((kind, applies_to, sql))
        self.principals.append(principal)
        if self.result is not None:
            return replace(self.result, kind=kind, applies_to=applies_to, sql=sql)
        tables = [t for t in ("fact_pos_retail_sales", "dim_date", "dim_product") if t in f"{applies_to} {sql}"]
        return SnippetValidation(
            kind=kind, valid=True, applies_to=applies_to, sql=sql, probe_sql=f"SELECT {sql}",
            columns=["value"], rows=[["12136500.40"]], tables=tables, elapsed_ms=2.0,
        )


class FakeSnippetBook:
    """The real document operations, over a copy, with the store faked.

    Writing the document is what the routes are about and is done for real;
    the store needs a database, and what it reports is set by the test.
    """

    def __init__(self) -> None:
        self.status = {"reachable": True, "snippets": 32, "embedded": 32, "current": True, "detail": "current"}
        self.status_calls: list[tuple[str, str]] = []
        self.listing = snippets_module.listing
        self.preview = snippets_module.preview
        self.add = snippets_module.add
        self.change = snippets_module.change
        self.delete = snippets_module.delete

    def store_status(self, url: str, digest: str) -> dict:
        self.status_calls.append((url, digest))
        return dict(self.status)


@pytest.fixture
def snippet_validator() -> FakeSnippetValidator:
    return FakeSnippetValidator()


@pytest.fixture
def snippet_book() -> FakeSnippetBook:
    return FakeSnippetBook()


@pytest.fixture
def snippet_draft() -> SnippetDraft:
    return SnippetDraft(
        name="Holiday sales",
        kind="filter",
        tables="fact_pos_retail_sales, dim_date",
        keywords="holiday sales, sales on public holidays",
        means="Restricts sales to the days the calendar marks as holidays.",
        applies_to="fact_pos_retail_sales f JOIN dim_date d ON d.date_key = f.sales_date_key",
        sql="d.is_holiday",
    )


# ---------------------------------------------------------------------------
# The service over HTTP: test_app.py and test_curation.py both drive it
# ---------------------------------------------------------------------------


@pytest.fixture
def pinged() -> list[str]:
    return []


@pytest.fixture
def make_client(settings, repository, fix_stores, validator, pinged, snippet_validator, snippet_book):
    def build(**overrides):
        app = create_app(
            settings=overrides.pop("settings", settings),
            repository=overrides.pop("repository", repository),
            fix_stores=overrides.pop("fix_stores", fix_stores),
            validator=overrides.pop("validator", validator),
            embedder_factory=overrides.pop("embedder_factory", lambda: "an embedder"),
            retail_pinger=overrides.pop("retail_pinger", lambda: pinged.append("retail")),
            snippet_validator=overrides.pop("snippet_validator", snippet_validator),
            snippet_book=overrides.pop("snippet_book", snippet_book),
            schema_reader=overrides.pop("schema_reader", lambda: [{"name": "dim_date", "columns": [{"name": "date_key", "type": "integer"}]}]),
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
