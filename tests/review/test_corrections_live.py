"""The corrections and completions stores, against real Postgres + pgvector.

The HTTP routes are tested against a fake store; this is where the store is
tested as itself, because what it promises is made of things only a real
server does: a UNIQUE that turns a double click into one record, ids drawn
from a sequence that never hands one out twice, a vector column sized by the model, and a cosine search.

Each test gets a throwaway database inside the store's own container,
created from template0 and dropped afterwards, so nothing here touches a
fix somebody actually saved. The embedder is a deterministic fake -- the
real one is exercised by the review service's end-to-end run -- so a
question's vector is a function of its words and a search result can be
asserted exactly.

The pure parts -- the kinds, the embed report, the hashing -- run without
Docker.
"""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from nl2sql_review.corrections import (
    BY_SLUG,
    COMPLETIONS,
    CORRECTIONS,
    KINDS,
    AlreadyFixed,
    EmbedResult,
    Fix,
    FixStore,
    content_hash,
    vector_literal,
)
from tests import live_stores

CORRECTIONS_ADMIN = live_stores.url("corrections", variable="CORRECTIONS_DB_URL", driver="postgresql")
COMPLETIONS_ADMIN = live_stores.url("completions", variable="COMPLETIONS_DB_URL", driver="postgresql")


# ---------------------------------------------------------------------------
# Without a database
# ---------------------------------------------------------------------------


def test_each_verdict_fixes_into_its_own_store_and_correct_into_neither():
    assert KINDS == {"no": CORRECTIONS, "incomplete": COMPLETIONS}
    assert BY_SLUG == {"corrections": CORRECTIONS, "completions": COMPLETIONS}
    assert "yes" not in KINDS


def test_the_two_stores_share_no_table_and_no_id_prefix():
    assert (CORRECTIONS.table, CORRECTIONS.vector_table) == ("sql_corrections", "sql_corrections_vectors")
    assert (COMPLETIONS.table, COMPLETIONS.vector_table) == ("sql_completions", "sql_completions_vectors")
    assert (CORRECTIONS.prefix, COMPLETIONS.prefix) == ("W", "I")


@pytest.mark.parametrize(
    "result,detail",
    [
        (EmbedResult(), "embedding is off"),
        (EmbedResult(ran=False, error="no ragproc"), "not embedded: no ragproc"),
        (EmbedResult(ran=True, embedded=2), "embedded 2; 0 still to embed"),
        (EmbedResult(ran=True, pending=3, error="timeout"), "FAILED: timeout; 3 still to embed"),
    ],
)
def test_the_embed_report_says_what_happened(result, detail):
    assert result.detail == detail


def test_a_question_hash_and_a_vector_literal_are_stable():
    assert content_hash("top 10 SKUs") == hashlib.sha256(b"top 10 SKUs").hexdigest()
    assert vector_literal([1, 0.5]) == "[1.0,0.5]"


# ---------------------------------------------------------------------------
# Against the stores
# ---------------------------------------------------------------------------


class WordEmbedder:
    """A vector per question from its words: shared words, nearby vectors."""

    DIMENSION = 16

    def __init__(self, model_name: str = "fake-words") -> None:
        self.model_name = model_name
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * self.DIMENSION
            for word in text.lower().split():
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIMENSION] += 1.0
            vectors.append(vector if any(vector) else [1.0] + [0.0] * (self.DIMENSION - 1))
        return vectors


class Unreachable:
    model_name = "down"

    def embed(self, texts):
        raise ConnectionError("no route to the embedding host")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _scratch(admin_url: str):
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except Exception as exc:  # noqa: BLE001
        live_stores.unreachable("store", admin_url, exc)
    name = f"t_fixes_{uuid.uuid4().hex[:12]}"
    admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    return admin, name, _with_database(admin_url, name)


@pytest.fixture(params=[CORRECTIONS, COMPLETIONS], ids=["corrections", "completions"])
def store(request):
    kind = request.param
    admin, name, url = _scratch(CORRECTIONS_ADMIN if kind is CORRECTIONS else COMPLETIONS_ADMIN)
    try:
        fix_store = FixStore(kind, url)
        fix_store.setup()
        yield fix_store
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def fix(submission_id: str = "sub-1", question: str = "top 10 SKUs", **overrides) -> Fix:
    return Fix(
        **{
            "fix_id": "",
            "submission_id": submission_id,
            "job_id": f"job-{submission_id}",
            "question": question,
            "incorrect_sql": "SELECT sku_id FROM dim_product",
            "incorrect_answer": "| sku_id |",
            "incorrect_columns": ["sku_id"],
            "incorrect_row_count": 10,
            "corrected_sql": "SELECT sku_id, product_name FROM dim_product",
            "corrected_columns": ["sku_id", "product_name"],
            "corrected_rows": [["SKU1", "Whole Milk"]],
            "corrected_row_count": 1,
            "plan_cost": 4.5,
            "reviewer": "ada",
            **overrides,
        }
    )


@pytest.mark.docker
def test_setting_up_twice_changes_nothing(store):
    store.setup()
    store.setup()
    assert store.count() == 0
    store.ping()


@pytest.mark.docker
def test_a_fix_is_stored_whole_under_the_next_id_of_its_store(store):
    first = store.save(fix("sub-1"))
    second = store.save(fix("sub-2", question="top ten stores"))
    prefix = store.kind.prefix
    assert (first.fix_id, second.fix_id) == (f"{prefix}0001", f"{prefix}0002")
    assert first.created_at is not None

    held = {f.fix_id: f for f in store.listing()}
    one = held[f"{prefix}0001"]
    assert (one.question, one.incorrect_sql, one.corrected_sql) == (
        "top 10 SKUs", "SELECT sku_id FROM dim_product", "SELECT sku_id, product_name FROM dim_product",
    )
    assert (one.incorrect_columns, one.corrected_rows, one.plan_cost) == (
        ["sku_id"], [["SKU1", "Whole Milk"]], 4.5,
    )
    assert one.embedded is False
    assert [f.fix_id for f in store.listing(limit=1)] == [f"{prefix}0002"]
    assert store.find_by_submission("sub-2") == f"{prefix}0002"
    assert store.find_by_submission("nope") is None


@pytest.mark.docker
def test_one_submission_is_one_fix(store):
    store.save(fix("sub-1"))
    with pytest.raises(AlreadyFixed) as refused:
        store.save(fix("sub-1", corrected_sql="SELECT 2"))
    assert refused.value.fix_id == f"{store.kind.prefix}0001"
    assert store.count() == 1


@pytest.mark.docker
def test_the_rag_half_embeds_every_question_it_has_not_embedded(store):
    store.save(fix("sub-1", question="top 10 SKUs by net sales"))
    store.save(fix("sub-2", question="which vendor supplies dairy"))
    embedder = WordEmbedder()

    first = store.embed_pending(embedder)
    assert (first.ran, first.embedded, first.pending, first.error) == (True, 2, 0, None)
    assert all(f.embedded for f in store.listing())

    # Nothing changed, so nothing is embedded again.
    again = store.embed_pending(embedder)
    assert (again.embedded, again.pending) == (0, 0)
    assert len(embedder.calls) == 1

    # A different model re-embeds everything: vectors from two models are
    # not comparable, and a search across both would be noise.
    other = store.embed_pending(WordEmbedder(model_name="another-model"))
    assert other.embedded == 2


@pytest.mark.docker
def test_the_nearest_question_is_found_with_both_queries_beside_it(store):
    store.save(fix("sub-1", question="top 10 SKUs by net sales"))
    store.save(fix("sub-2", question="which vendor supplies dairy", corrected_sql="SELECT vendor_name"))
    embedder = WordEmbedder()
    assert store.search(embedder.embed(["anything"])[0]) == []  # no vectors yet
    store.embed_pending(embedder)

    hits = store.search(embedder.embed(["top SKUs by net sales"])[0], limit=2)
    assert hits[0]["question"] == "top 10 SKUs by net sales"
    assert hits[0]["incorrect_sql"] == "SELECT sku_id FROM dim_product"
    assert hits[0]["corrected_sql"] == "SELECT sku_id, product_name FROM dim_product"
    assert hits[0]["distance"] < hits[1]["distance"]


@pytest.mark.docker
def test_an_embedding_host_that_is_down_costs_the_vector_not_the_record(store):
    store.save(fix("sub-1"))
    result = store.embed_pending(Unreachable())
    assert result.ran is True and result.embedded == 0 and result.pending == 1
    assert result.error == "ConnectionError: no route to the embedding host"
    assert store.count() == 1 and store.listing()[0].embedded is False

    # The next save that can reach a host catches it up.
    store.save(fix("sub-2", question="top ten stores"))
    caught_up = store.embed_pending(WordEmbedder())
    assert (caught_up.embedded, caught_up.pending) == (2, 0)


@pytest.mark.docker
def test_an_empty_store_has_nothing_to_embed(store):
    result = store.embed_pending(WordEmbedder())
    assert (result.embedded, result.pending, result.error) == (0, 0, None)


@pytest.mark.docker
def test_deleting_a_submissions_fix_takes_its_vector_with_it(store):
    """Reopening a corrected submission deletes its fix. A vector left behind
    would keep the deleted fix retrievable by whatever reads the store next."""
    store.save(fix("sub-1", question="top 10 SKUs by net sales"))
    store.save(fix("sub-2", question="which vendor supplies dairy"))
    embedder = WordEmbedder()
    store.embed_pending(embedder)

    removed = store.delete("sub-1")

    assert (removed.fix_id, removed.question) == (f"{store.kind.prefix}0001", "top 10 SKUs by net sales")
    assert removed.corrected_sql == "SELECT sku_id, product_name FROM dim_product"
    assert store.count() == 1
    hits = store.search(embedder.embed(["top SKUs by net sales"])[0], limit=5)
    assert [hit["question"] for hit in hits] == ["which vendor supplies dairy"]


@pytest.mark.docker
def test_a_deleted_fix_can_be_saved_again(store):
    store.save(fix("sub-1"))
    store.delete("sub-1")
    again = store.save(fix("sub-1", corrected_sql="SELECT 2"))
    assert again.corrected_sql == "SELECT 2"
    assert store.find_by_submission("sub-1") == again.fix_id


@pytest.mark.docker
def test_a_deleted_fixs_id_is_never_given_to_another(store):
    """V6-29: the highest id plus one gave a deleted fix's id to the next,
    and anything that had quoted it then meant a different fix."""
    prefix = store.kind.prefix
    store.save(fix("sub-1"))
    store.save(fix("sub-2"))
    store.delete("sub-2")
    assert store.save(fix("sub-3")).fix_id == f"{prefix}0003"


@pytest.mark.docker
def test_a_store_from_before_the_sequence_carries_on_from_its_highest_id(store):
    prefix = store.kind.prefix
    with psycopg.connect(store.url, autocommit=True) as conn:
        conn.execute(f"DROP SEQUENCE {store.kind.sequence}")
    # No sequence, as a store whose schema is someone else's may have none:
    # the highest plus one, as before.
    assert [store.save(fix(f"sub-{n}")).fix_id for n in (1, 2)] == [f"{prefix}0001", f"{prefix}0002"]
    store.setup()
    assert store.save(fix("sub-3")).fix_id == f"{prefix}0003", "made, and moved past what is there"
    store.setup()
    assert store.save(fix("sub-4")).fix_id == f"{prefix}0004", "and never moved back"


@pytest.mark.docker
def test_deleting_what_the_store_does_not_hold_is_none(store):
    assert store.delete("never-fixed") is None


# ---------------------------------------------------------------------------
# 5.6: fixes written straight into a store, with no submission behind them
# ---------------------------------------------------------------------------


@pytest.mark.docker
def test_curated_fixes_have_no_submission_and_any_number_of_them_can_be_stored(store):
    """NULL is distinct under UNIQUE, so the one-fix-per-submission rule
    still holds for submissions and does not limit curated fixes to one."""
    first = store.save(fix(None, question="top 10 SKUs", job_id="", source="curated"))
    second = store.save(fix(None, question="which vendor supplies dairy", job_id="", source="curated"))
    held = {f.fix_id: f for f in store.listing()}
    assert {held[first.fix_id].source, held[second.fix_id].source} == {"curated"}
    assert held[first.fix_id].submission_id is None
    reviewed = store.save(fix("sub-1"))
    assert store.get(reviewed.fix_id).source == "review"


@pytest.mark.docker
def test_a_fix_is_read_and_removed_by_its_own_id_with_its_vector(store):
    curated = store.save(fix(None, question="top 10 SKUs by net sales", job_id="", source="curated"))
    store.save(fix("sub-2", question="which vendor supplies dairy"))
    embedder = WordEmbedder()
    store.embed_pending(embedder)

    assert store.get(curated.fix_id).question == "top 10 SKUs by net sales"
    assert store.delete_by_id(curated.fix_id).fix_id == curated.fix_id
    assert store.get(curated.fix_id) is None and store.delete_by_id(curated.fix_id) is None
    hits = store.search(embedder.embed(["top SKUs by net sales"])[0], limit=5)
    assert [hit["question"] for hit in hits] == ["which vendor supplies dairy"]


@pytest.mark.docker
def test_a_store_from_before_curated_fixes_is_widened_in_place(store):
    """5.1-5.5 created `submission_id NOT NULL` and no `source`; the next
    start's setup relaxes the one and adds the other, keeping every row."""
    import psycopg

    table = store.kind.table
    with psycopg.connect(store.url) as conn:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN source")
        conn.execute(f"ALTER TABLE {table} ALTER COLUMN submission_id SET NOT NULL")
        conn.execute(
            f"INSERT INTO {table} (fix_id, submission_id, job_id, question, corrected_sql) "
            f"VALUES ('{store.kind.prefix}0001', 'old', 'job-old', 'q', 'SELECT 1')"
        )
        conn.commit()
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(f"INSERT INTO {table} (fix_id, job_id, question, corrected_sql) VALUES ('X', 'j', 'q', 'SELECT 1')")
        conn.rollback()

    store.setup()

    assert store.get(f"{store.kind.prefix}0001").source == "review"
    assert store.save(fix(None, job_id="", source="curated")).fix_id == f"{store.kind.prefix}0002"
