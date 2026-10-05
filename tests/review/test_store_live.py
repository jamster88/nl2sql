"""The staging database, against a real Postgres.

Everything in `test_app.py` runs against a fake repository, which is what
makes the HTTP surface testable on every run. A fake cannot tell the truth
about a privilege, though, and the security story here is made entirely of
privileges: the internet-facing process holds a role that may add a verdict
and may not read one back, and rows a curator has judged are invisible to it
by a row-level policy rather than by the SQL in `feedback.py` being careful.

So these tests ask the cluster. They create the schema, connect as the writer
role, and try the things it must not be able to do.

**Isolation, and the part of it that cannot be a database.** Each run gets a
throwaway database created from template0 and dropped afterwards, the same
way `tests/rag/` protects the published golden pairs -- so nothing here
deletes feedback somebody actually staged.

A *role*, though, is cluster-level. `ensure_writer_role` resets the writer's
password every time it runs, by design, so running these tests against a
live stack changed the password out from under the API container and every
vote started failing authentication until the review service was restarted.
A test that breaks the thing it is testing against is not isolated, whatever
database it writes to. So the password is restored at teardown, and the one
test that proves a password *can* be rotated puts it back itself.

Opt-in (`pytest --run-docker`). Connects at FEEDBACK_DB_URL (default: the
compose feedbackdb on localhost:5435) and skips rather than fails when
nothing is listening.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from nl2sql_agent.api.feedback import AlreadyReviewed, Capture, PostgresSink
from nl2sql_review.settings import ReviewSettings
from nl2sql_review.store import (
    PROMOTIONS,
    STATES,
    SUBMISSIONS,
    STATE_CHECK,
    SUBMISSION_COLUMNS,
    VERDICT_CHECK,
    VERDICTS,
    WRITER_ROLE,
    Repository,
    connection,
    ensure_writer_role,
)
from tests import live_stores

pytestmark = pytest.mark.docker

ADMIN_URL = live_stores.url("feedback", variable="FEEDBACK_DB_URL", driver="postgresql")
WRITER_PASSWORD = "test-writer-password"

#: What the writer's password is put back to at teardown: the default a
#: review service starting with no FEEDBACK_WRITER_PASSWORD would set, which
#: is what the compose stack runs with.
LIVE_PASSWORD = ReviewSettings().writer_password


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture(scope="module")
def owner():
    """A throwaway database, dropped afterwards, with the role put back.

    The drop handles the tables. The `finally` handles the role, which no
    database can contain: it is the reason a live API stopped being able to
    write feedback the first time this module was run against a running
    stack.
    """
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except Exception as exc:  # noqa: BLE001
        live_stores.unreachable("staging database", ADMIN_URL, exc)

    name = f"t_feedback_{uuid.uuid4().hex[:12]}"
    try:
        # template0 rather than template1, for the same reason as tests/rag:
        # nothing an operator installed into template1 changes what is under
        # test.
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    except Exception as exc:  # noqa: BLE001
        admin.close()
        pytest.skip(f"cannot create a scratch database ({exc})")

    scratch = _with_database(ADMIN_URL, name)
    try:
        yield scratch
    finally:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        # The role survives the database. Put its password back to the one a
        # running review service would have set, so a live stack keeps
        # working after the suite has run.
        with connection(ADMIN_URL) as conn:
            ensure_writer_role(conn, LIVE_PASSWORD)
        admin.close()


def _set_writer_password(owner_url: str, password: str) -> None:
    with connection(owner_url) as conn:
        ensure_writer_role(conn, password)


def _writer_url(owner_url: str, password: str = WRITER_PASSWORD) -> str:
    _, _, rest = owner_url.partition("://")
    host_and_db = rest.rpartition("@")[2]
    return f"postgresql://{WRITER_ROLE}:{password}@{host_and_db}"


@pytest.fixture
def repo(owner):
    """A clean schema for every test, with the writer role re-fenced."""
    repository = Repository(owner)
    repository.setup(WRITER_PASSWORD)
    with psycopg.connect(owner) as conn:
        conn.execute(f"DELETE FROM {PROMOTIONS}")
        conn.execute(f"DELETE FROM {SUBMISSIONS}")
        conn.commit()
    return repository


@pytest.fixture
def writer_url(owner) -> str:
    return _writer_url(owner)


@pytest.fixture
def sink(repo, writer_url):
    return PostgresSink(writer_url)


def capture(**overrides) -> Capture:
    return Capture(
        **{
            "job_id": f"job-{uuid.uuid4().hex[:8]}",
            "verdict": "yes",
            "question": "How many stores are there?",
            "sql_code": "SELECT count(*) FROM dim_store;",
            "tables": "dim_store",
            "row_count": 1,
            "columns": ("count",),
            "agent_version": "4.2.0",
            **overrides,
        }
    )


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


def test_the_schema_is_created_on_a_database_that_has_none(owner):
    with psycopg.connect(owner) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {PROMOTIONS}")
        conn.execute(f"DROP TABLE IF EXISTS {SUBMISSIONS}")
        conn.commit()

    Repository(owner).setup(WRITER_PASSWORD)

    with psycopg.connect(owner) as conn:
        found = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
    assert {SUBMISSIONS, PROMOTIONS} <= found


def test_setting_up_twice_changes_nothing(repo, owner):
    repo.setup(WRITER_PASSWORD)
    repo.setup(WRITER_PASSWORD)
    assert repo.counts() == {state: 0 for state in STATES}


def test_the_agent_writes_exactly_the_columns_the_schema_has(repo, owner):
    """The two modules live in different images and cannot import each other.

    A column added to the schema and not to the API is a field silently
    dropped at capture time; the reverse is an INSERT that fails at runtime.
    """
    with psycopg.connect(owner) as conn:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (SUBMISSIONS,),
            ).fetchall()
        }
    assert set(SUBMISSION_COLUMNS) <= columns


def test_a_table_from_before_the_third_verdict_is_widened_in_place(repo, owner):
    """The staging table on a running stack was created when a verdict was
    yes or no, with an inline CHECK that `CREATE TABLE IF NOT EXISTS` never
    revisits. Setup must widen it, keeping the rows it already holds."""
    with psycopg.connect(owner) as conn:
        conn.execute(f"ALTER TABLE {SUBMISSIONS} DROP CONSTRAINT {VERDICT_CHECK}")
        conn.execute(
            f"ALTER TABLE {SUBMISSIONS} ADD CONSTRAINT {VERDICT_CHECK} CHECK (verdict IN ('yes', 'no'))"
        )
        conn.execute(
            f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question) VALUES ('old', 'job-old', 'no', 'q')"
        )
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question) "
                "VALUES ('new', 'job-new', 'incomplete', 'q')"
            )
        conn.rollback()

    repo.setup(WRITER_PASSWORD)

    with psycopg.connect(owner) as conn:
        conn.execute(
            f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question) "
            "VALUES ('new', 'job-new', 'incomplete', 'q')"
        )
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question) "
                "VALUES ('bad', 'job-bad', 'maybe', 'q')"
            )
    assert repo.get_by_job("job-old").verdict == "no"


# ---------------------------------------------------------------------------
# Capture, as the writer role
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", VERDICTS)
def test_each_verdict_can_be_recorded_by_the_writer(repo, sink, verdict):
    item = capture(verdict=verdict)
    sink.record(item)
    assert repo.get_by_job(item.job_id).verdict == verdict
    assert len(repo.listing(verdict=verdict)) == 1


def test_a_verdict_can_be_recorded_and_read_back_by_the_owner(repo, sink):
    item = capture(comment="looks right")
    sink.record(item)

    held = repo.get_by_job(item.job_id)
    assert held is not None
    assert held.verdict == "yes"
    assert held.comment == "looks right"
    assert held.columns == ["count"]
    assert held.state == "pending"


def test_voting_again_replaces_the_verdict_rather_than_adding_one(repo, sink):
    item = capture()
    sink.record(item)
    sink.record(capture(job_id=item.job_id, verdict="no", comment="changed my mind"))

    rows = repo.listing()
    assert len(rows) == 1
    assert rows[0].verdict == "no"
    assert rows[0].comment == "changed my mind"


def test_a_verdict_can_be_withdrawn(repo, sink):
    item = capture()
    sink.record(item)
    assert sink.withdraw(item.job_id) is True
    assert repo.listing() == []


def test_withdrawing_something_that_was_never_there_says_so(repo, sink):
    assert sink.withdraw("never-voted") is False


def test_the_writer_can_reach_the_database(sink):
    assert sink.check() == (True, "reachable")


def test_an_unreachable_database_is_reported_not_raised():
    ok, detail = PostgresSink("postgresql://nobody@127.0.0.1:1/none").check()
    assert ok is False
    assert detail


# ---------------------------------------------------------------------------
# The fence: what the public process cannot do
# ---------------------------------------------------------------------------


def test_a_reviewed_verdict_cannot_be_changed_by_the_public_process(repo, sink):
    item = capture()
    sink.record(item)
    repo.review(repo.get_by_job(item.job_id).id, state="accepted", reviewer="sam")

    with pytest.raises(AlreadyReviewed):
        sink.record(capture(job_id=item.job_id, verdict="no"))

    assert repo.get_by_job(item.job_id).verdict == "yes"


def test_a_reviewed_verdict_cannot_be_withdrawn_by_the_public_process(repo, sink):
    item = capture()
    sink.record(item)
    repo.review(repo.get_by_job(item.job_id).id, state="rejected")

    assert sink.withdraw(item.job_id) is False
    assert repo.get_by_job(item.job_id) is not None


def test_reviewed_rows_are_invisible_to_the_writer_role(repo, sink, writer_url):
    """Not merely protected: not there, as far as that role is concerned."""
    pending = capture()
    reviewed = capture()
    sink.record(pending)
    sink.record(reviewed)
    repo.review(repo.get_by_job(reviewed.job_id).id, state="accepted")

    with psycopg.connect(writer_url) as conn:
        visible = conn.execute(f"SELECT job_id FROM {SUBMISSIONS}").fetchall()
    assert [row[0] for row in visible] == [pending.job_id]
    assert len(repo.listing()) == 2


@pytest.mark.parametrize(
    "statement",
    [
        f"SELECT question FROM {SUBMISSIONS}",
        f"SELECT comment FROM {SUBMISSIONS}",
        f"SELECT sql_code FROM {SUBMISSIONS}",
        f"UPDATE {SUBMISSIONS} SET state = 'promoted'",
        f"UPDATE {SUBMISSIONS} SET reviewer = 'me'",
        f"SELECT * FROM {PROMOTIONS}",
        f"INSERT INTO {PROMOTIONS} (pair_id, submission_id, chunk_id, markdown) VALUES ('Q46','x','y','z')",
        f"DROP TABLE {SUBMISSIONS}",
        f"ALTER TABLE {SUBMISSIONS} DISABLE ROW LEVEL SECURITY",
    ],
)
def test_the_writer_role_is_refused_everything_it_should_be(repo, statement, writer_url):
    with psycopg.connect(writer_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
        conn.rollback()


def test_the_writer_cannot_create_a_row_that_is_already_accepted(repo, writer_url):
    """The policy's WITH CHECK, not just its USING.

    Without it, the public process could insert a row that a reviewer would
    never see in the pending queue.
    """
    with psycopg.connect(writer_url) as conn:
        with pytest.raises(psycopg.errors.Error):
            conn.execute(
                f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question, state) "
                "VALUES ('x', 'j', 'yes', 'q', 'accepted')"
            )
        conn.rollback()


def test_the_writer_role_is_re_fenced_on_every_start(repo, owner, writer_url):
    """A grant widened by hand should not survive a restart."""
    with psycopg.connect(owner) as conn:
        conn.execute(f"GRANT SELECT ON {SUBMISSIONS} TO {WRITER_ROLE}")
        conn.commit()

    repo.setup(WRITER_PASSWORD)

    with psycopg.connect(writer_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT question FROM {SUBMISSIONS}")


# ---------------------------------------------------------------------------
# The review side, as the owner
# ---------------------------------------------------------------------------


def test_the_queue_filters_and_counts(repo, sink):
    yes, no = capture(), capture(verdict="no")
    sink.record(yes)
    sink.record(no)
    repo.review(repo.get_by_job(no.job_id).id, state="rejected")

    assert len(repo.listing(state="pending")) == 1
    assert len(repo.listing(verdict="no")) == 1
    assert repo.counts() == {"pending": 1, "accepted": 0, "rejected": 1, "promoted": 0, "corrected": 0}
    by_verdict = repo.counts_by_verdict()
    assert set(by_verdict) == set(VERDICTS)
    assert by_verdict["yes"]["pending"] == 1 and by_verdict["no"]["rejected"] == 1
    assert by_verdict["incomplete"] == {state: 0 for state in STATES}


def test_a_fixed_submission_is_corrected_and_names_its_fix(repo, sink):
    wrong = capture(verdict="no")
    sink.record(wrong)
    held = repo.get_by_job(wrong.job_id)

    after = repo.mark_corrected(held.id, fix_id="W0001", reviewer="ada", review_note="no names")
    assert (after.state, after.promoted_pair_id, after.reviewer, after.review_note) == (
        "corrected", "W0001", "ada", "no names",
    )
    assert after.reviewed_at is not None
    assert repo.counts_by_verdict()["no"]["corrected"] == 1
    assert repo.mark_corrected("no-such-id", fix_id="W0002", reviewer="", review_note="") is None


def test_a_corrected_verdict_is_out_of_the_writers_reach(repo, sink):
    """Like a promoted one: once fixed, a revote cannot rewrite it."""
    wrong = capture(verdict="no")
    sink.record(wrong)
    repo.mark_corrected(repo.get_by_job(wrong.job_id).id, fix_id="W0001", reviewer="", review_note="")
    with pytest.raises(AlreadyReviewed):
        sink.record(capture(job_id=wrong.job_id, verdict="yes"))
    assert repo.get_by_job(wrong.job_id).verdict == "no"


def test_a_table_from_before_the_corrected_state_is_widened_in_place(repo, owner):
    with psycopg.connect(owner) as conn:
        conn.execute(f"ALTER TABLE {SUBMISSIONS} DROP CONSTRAINT {STATE_CHECK}")
        conn.execute(
            f"ALTER TABLE {SUBMISSIONS} ADD CONSTRAINT {STATE_CHECK} "
            "CHECK (state IN ('pending', 'accepted', 'rejected', 'promoted'))"
        )
        conn.execute(
            f"INSERT INTO {SUBMISSIONS} (id, job_id, verdict, question, state) "
            "VALUES ('old', 'job-old', 'no', 'q', 'accepted')"
        )
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f"UPDATE {SUBMISSIONS} SET state = 'corrected' WHERE id = 'old'")
        conn.rollback()

    repo.setup(WRITER_PASSWORD)

    assert repo.mark_corrected("old", fix_id="W0001", reviewer="", review_note="").state == "corrected"
    with psycopg.connect(owner) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(f"UPDATE {SUBMISSIONS} SET state = 'done' WHERE id = 'old'")


def test_the_queue_is_newest_first(repo, sink):
    first, second = capture(), capture()
    sink.record(first)
    sink.record(second)
    assert [s.job_id for s in repo.listing()] == [second.job_id, first.job_id]


def test_a_review_leaves_the_submitted_evidence_alone(repo, sink):
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)

    repo.review(held.id, state="accepted", reviewer="sam", review_note="good", draft={"title": "t"})
    after = repo.get(held.id)

    assert (after.question, after.sql_code, after.verdict) == (held.question, held.sql_code, held.verdict)
    assert after.draft == {"title": "t"}
    assert after.reviewed_at is not None


def test_a_review_with_nothing_to_change_is_a_read(repo, sink):
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)
    assert repo.review(held.id) == held


def test_reviewing_something_that_is_not_there_gives_nothing_back(repo):
    assert repo.review("no-such-id", state="accepted") is None


def test_a_promotion_is_recorded_and_the_submission_moves_with_it(repo, sink):
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)

    repo.mark_promoted(
        held.id, pair_id="Q46", chunk_id="eval:q46", suite="Suite 26", title="t",
        markdown="## Q46 - t\n", reviewer="sam", reloaded=True, reload_detail="ok",
    )

    after = repo.get(held.id)
    assert after.state == "promoted"
    assert after.promoted_pair_id == "Q46"
    assert repo.promotions()[0]["pair_id"] == "Q46"


def test_the_promotion_log_will_not_orphan_itself(repo, sink, owner):
    """ON DELETE RESTRICT: the log says how a golden pair got there.

    A row that could be deleted out from under it would leave a pair in the
    question document with no record of who put it there or why.
    """
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)
    repo.mark_promoted(
        held.id, pair_id="Q46", chunk_id="eval:q46", suite="", title="",
        markdown="", reviewer="", reloaded=True, reload_detail="",
    )

    with psycopg.connect(owner) as conn:
        # RESTRICT raises its own error, not the generic foreign-key one.
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(f"DELETE FROM {SUBMISSIONS} WHERE id = %s", (held.id,))
        conn.rollback()


def test_get_returns_nothing_for_an_id_that_is_not_there(repo):
    assert repo.get("nope") is None
    assert repo.get_by_job("nope") is None


def test_the_readiness_ping_round_trips(repo):
    """What `/readyz` calls. It has to touch the database rather than report
    that a connection object was constructed."""
    assert repo.ping() is None


def test_the_readiness_ping_raises_when_there_is_nothing_there():
    """Raised rather than swallowed: the route turns it into the detail line
    that tells somebody what is actually wrong."""
    with pytest.raises(Exception):
        Repository("postgresql://nobody@127.0.0.1:1/none").ping()


# ---------------------------------------------------------------------------
# That this module cannot damage what it is run against
# ---------------------------------------------------------------------------


def test_the_tests_do_not_run_against_the_real_staging_database(owner):
    """Every test here deletes rows and re-fences a role.

    Run against the configured database, that wipes feedback somebody
    actually staged. The scratch database is what makes "must not" a
    property of the setup rather than of each test remembering.
    """
    assert owner != ADMIN_URL
    assert urlsplit(owner).path.lstrip("/").startswith("t_feedback_")
    assert urlsplit(owner).netloc == urlsplit(ADMIN_URL).netloc


def test_a_rotated_password_is_put_back_by_the_test_that_rotates_it(repo, owner):
    """A role is cluster-level, so no database contains this one.

    The first version of this module left the writer's password set to the
    test value, and every vote against the running stack failed
    authentication until the review service was restarted. Whatever changes
    it here has to change it back.
    """
    _set_writer_password(owner, "temporarily-different")
    assert PostgresSink(_writer_url(owner, "temporarily-different")).check()[0] is True

    _set_writer_password(owner, WRITER_PASSWORD)
    assert PostgresSink(_writer_url(owner)).check()[0] is True



# ---------------------------------------------------------------------------
# Reopening and deleting (5.4)
# ---------------------------------------------------------------------------


def _promoted(repo, sink, pair_id: str = "Q46"):
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)
    repo.review(held.id, state="accepted", reviewer="sam", review_note="good one")
    repo.mark_promoted(
        held.id, pair_id=pair_id, chunk_id=f"eval:{pair_id.lower()}", suite="", title="t",
        markdown=f"## {pair_id} - t\n", reviewer="sam", reloaded=True, reload_detail="ok",
    )
    return repo.get(held.id)


def test_reopening_puts_a_promoted_submission_back_and_forgets_the_promotion(repo, sink):
    held = _promoted(repo, sink)

    reopened = repo.reopen(held.id, draft={"title": "the pair, as it was"})

    assert reopened.state == "pending"
    assert reopened.promoted_pair_id is None
    assert reopened.reviewed_at is None
    assert (reopened.reviewer, reopened.review_note) == ("sam", "good one")
    assert reopened.draft == {"title": "the pair, as it was"}
    assert repo.promotions() == []


def test_reopening_without_a_draft_keeps_the_one_it_had(repo, sink):
    item = capture()
    sink.record(item)
    held = repo.get_by_job(item.job_id)
    repo.review(held.id, state="rejected", draft={"title": "kept"})
    assert repo.reopen(held.id).draft == {"title": "kept"}


def test_a_reopened_submission_is_the_users_to_revote_again(repo, sink):
    """Pending is pending: the row-level policies hand it back to the public
    process, so the person who voted can change their mind again, as they
    could before anyone looked at it."""
    held = _promoted(repo, sink)
    repo.reopen(held.id)

    sink.record(capture(job_id=held.job_id, verdict="no", comment="on reflection"))

    again = repo.get_by_job(held.job_id)
    assert (again.verdict, again.comment, again.state) == ("no", "on reflection", "pending")


def test_a_pair_id_given_back_can_be_promoted_again(repo, sink):
    """The log's key is the pair id, so a log entry left behind would refuse
    the next promotion that took the same id."""
    held = _promoted(repo, sink, "Q46")
    repo.reopen(held.id)
    again = _promoted(repo, sink, "Q46")
    assert [row["pair_id"] for row in repo.promotions()] == ["Q46"]
    assert again.state == "promoted"


def test_deleting_takes_the_promotion_log_with_it(repo, sink):
    """The log restricts deletes of the rows it names; the delete clears it
    first, in the same transaction."""
    held = _promoted(repo, sink)

    deleted = repo.delete(held.id)

    assert (deleted.id, deleted.state) == (held.id, "promoted")
    assert repo.get(held.id) is None
    assert repo.promotions() == []
    assert repo.delete(held.id) is None


def test_reopening_a_submission_that_is_not_there_is_none(repo):
    assert repo.reopen("not-a-submission") is None


def test_the_submission_behind_a_pair_or_a_fix_is_found_by_its_id(repo, sink):
    """What the curation interface asks before taking a pair or a fix out:
    did the review queue produce it, and which submission goes back."""
    good, wrong, open_ = capture(), capture(verdict="no"), capture()
    for item in (good, wrong, open_):
        sink.record(item)
    promoted = repo.get_by_job(good.job_id)
    repo.mark_promoted(
        promoted.id, pair_id="Q90", chunk_id="eval:q90", suite="", title="t",
        markdown="## Q90 - t\n", reviewer="", reloaded=False, reload_detail="",
    )
    corrected = repo.get_by_job(wrong.job_id)
    repo.mark_corrected(corrected.id, fix_id="W0007", reviewer="", review_note="")

    assert repo.get_by_outcome("Q90").id == promoted.id
    assert repo.get_by_outcome("W0007").id == corrected.id
    assert repo.get_by_outcome("Q91") is None
    assert repo.outcomes() == {"Q90": promoted.id, "W0007": corrected.id}
    repo.reopen(promoted.id)
    assert repo.get_by_outcome("Q90") is None and "Q90" not in repo.outcomes()
