"""Fixtures for the review service's tests.

The important one is `document`: a *real* copy of
`context_questions/translated_questions.md`, not a miniature stand-in. The
whole promotion path is a bet that a rendered pair survives
`ragproc.golden_pairs.parse_document`, and that bet is only worth anything
against the document the loader actually reads -- 45 pairs, 25 suites, prose
sections between them and a `## How to read a pair` heading that is not a
pair and must not be counted as one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nl2sql_review.render import Draft
from nl2sql_review.settings import ReviewSettings
from nl2sql_review.store import Submission

ROOT = Path(__file__).resolve().parent.parent.parent
REAL_DOCUMENT = ROOT / "context_questions" / "translated_questions.md"


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """A writable copy of the real question document."""
    target = tmp_path / "translated_questions.md"
    shutil.copy(REAL_DOCUMENT, target)
    return target


@pytest.fixture
def settings(document: Path) -> ReviewSettings:
    """Pointed at the copy, with both loaders off.

    Off because they need a context store, a vector store and an embedding
    host. The tests that care about the loaders being *invoked* fake them;
    the ones here care about what is written to the document.
    """
    return ReviewSettings(
        document=str(document),
        rag_dir=str(ROOT / "rag"),
        reload_context=False,
        reload_vectors=False,
        token="test-token",
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

    def ping(self) -> None:
        self.pings += 1
        if self.fail_ping is not None:
            raise self.fail_ping

    def get(self, submission_id):
        return self.submissions.get(submission_id)

    def get_by_job(self, job_id):
        return next((s for s in self.submissions.values() if s.job_id == job_id), None)

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
