"""The context-store half of the golden-pair loader, against a real Postgres.

The BM25 ranking is the reason this file exists. Postgres ships `ts_rank`, which
is a length-normalised tf-idf and not BM25, so the formula is written out here
in SQL -- which means nothing but a test comparing it against an independent
implementation can say whether it is actually BM25 or merely something that
sorts plausibly.

Opt-in (`pytest --run-docker`). Every test writes into a throwaway schema, so
the published data in `public` is never touched.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.docker

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")

from ragproc import golden_pairs as gp  # noqa: E402


def load(conn, pairs) -> None:
    gp.ensure_tables(conn)
    gp.upsert_pairs(conn, pairs, source_doc="translated_questions")
    gp.rebuild_bm25_index(conn)
    gp.ensure_bm25_function(conn)


def bm25(conn, query: str) -> dict[str, float]:
    rows = conn.execute(
        f"SELECT chunk_id, score FROM {gp.BM25_FUNCTION}(%s)", (query,)
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


# ---------------------------------------------------------------------------
# Schema and loading
# ---------------------------------------------------------------------------


def test_the_eight_requested_columns_all_exist(chunk_conn):
    gp.ensure_tables(chunk_conn)
    columns = {
        r[0]
        for r in chunk_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (gp.TABLE,),
        )
    }
    assert {
        "chunk_id", "type", "tables", "keywords",
        "question", "reasoning_target", "sql_code", "result",
    } <= columns


def test_creating_the_tables_twice_is_harmless(chunk_conn):
    """run_all.sh and the loader both call this, and a re-run must not wipe
    anything already stored.
    """
    gp.ensure_tables(chunk_conn)
    gp.upsert_pairs(chunk_conn, [], source_doc="x")
    gp.ensure_tables(chunk_conn)
    assert chunk_conn.execute(f"SELECT count(*) FROM {gp.TABLE}").fetchone()[0] == 0


def test_every_parsed_pair_round_trips_through_the_database(chunk_conn, parsed_pairs):
    load(chunk_conn, parsed_pairs)
    stored = {
        r[0]: r
        for r in chunk_conn.execute(
            f"SELECT chunk_id, question, reasoning_target, sql_code, result, keywords, tables, type "
            f"FROM {gp.TABLE}"
        )
    }
    assert len(stored) == len(parsed_pairs)
    for pair in parsed_pairs:
        row = stored[pair.chunk_id]
        assert row[1] == pair.question
        assert row[2] == pair.reasoning_target
        assert row[3] == pair.sql_code  # the whole query, newlines and all
        assert row[4] == pair.result


def test_reloading_updates_in_place_rather_than_duplicating(chunk_conn, parsed_pairs):
    load(chunk_conn, parsed_pairs)
    edited = parsed_pairs[0]
    edited.result = "changed by the test"
    gp.upsert_pairs(chunk_conn, parsed_pairs, source_doc="translated_questions")

    assert chunk_conn.execute(f"SELECT count(*) FROM {gp.TABLE}").fetchone()[0] == len(parsed_pairs)
    assert chunk_conn.execute(
        f"SELECT result FROM {gp.TABLE} WHERE chunk_id = %s", (edited.chunk_id,)
    ).fetchone()[0] == "changed by the test"


def test_pairs_removed_from_the_document_are_removed_from_the_store(chunk_conn, parsed_pairs):
    load(chunk_conn, parsed_pairs)
    keep = [p.chunk_id for p in parsed_pairs[:10]]
    removed = gp.delete_missing(chunk_conn, keep)
    assert removed == len(parsed_pairs) - 10
    assert chunk_conn.execute(f"SELECT count(*) FROM {gp.TABLE}").fetchone()[0] == 10


def test_deleting_a_pair_takes_its_bm25_rows_with_it(chunk_conn, parsed_pairs):
    """Orphaned term rows would keep scoring a pair that is no longer there."""
    load(chunk_conn, parsed_pairs)
    gp.delete_missing(chunk_conn, [p.chunk_id for p in parsed_pairs[:5]])
    orphans = chunk_conn.execute(
        f"SELECT count(*) FROM {gp.TERMS_TABLE} t "
        f"WHERE NOT EXISTS (SELECT 1 FROM {gp.TABLE} g WHERE g.chunk_id = t.chunk_id)"
    ).fetchone()[0]
    assert orphans == 0


def test_load_pairs_returns_what_the_embedder_needs_in_document_order(chunk_conn, parsed_pairs):
    load(chunk_conn, parsed_pairs)
    rows = gp.load_pairs(chunk_conn)
    assert [r["pair_id"] for r in rows] == [p.pair_id for p in parsed_pairs]
    assert all(r["question"] and r["reasoning_target"] and r["content_hash"] for r in rows)


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


def test_the_index_covers_every_pair_and_reports_its_parameters(chunk_conn, parsed_pairs):
    load(chunk_conn, parsed_pairs)
    stats = gp.rebuild_bm25_index(chunk_conn)
    assert stats["documents"] == len(parsed_pairs)
    assert stats["distinct_terms"] > 100
    row = chunk_conn.execute(
        f"SELECT doc_count, avg_doc_len, k1, b, ts_config FROM {gp.PARAMS_TABLE}"
    ).fetchone()
    assert row[0] == len(parsed_pairs)
    assert row[2] == gp.DEFAULT_K1 and row[3] == gp.DEFAULT_B
    assert row[4] == gp.TS_CONFIG


@pytest.mark.parametrize(
    "query",
    [
        "gross margin for the produce department",
        "market share fan out double counting",
        "vendor slotting allowance",
        "rolling average window function",
        "basket affinity co-purchase",
    ],
)
def test_the_sql_function_matches_an_independent_bm25_implementation(
    chunk_conn, parsed_pairs, query
):
    """The whole point of building it by hand. Recomputes Okapi BM25 in Python
    from the same stored lexemes and compares every score.
    """
    load(chunk_conn, parsed_pairs)

    docs: dict[str, dict[str, int]] = {}
    for cid, term, tf in chunk_conn.execute(
        f"SELECT chunk_id, term, tf FROM {gp.TERMS_TABLE}"
    ):
        docs.setdefault(cid, {})[term] = tf
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs.values()) / n
    k1, b = gp.DEFAULT_K1, gp.DEFAULT_B
    lexemes = {
        r[0]
        for r in chunk_conn.execute(
            f"SELECT lexeme FROM unnest(to_tsvector('{gp.TS_CONFIG}', %s))", (query,)
        )
    }

    expected: dict[str, float] = {}
    for cid, tfs in docs.items():
        length = sum(tfs.values())
        score = 0.0
        for term in lexemes:
            freq = tfs.get(term, 0)
            if not freq:
                continue
            df = sum(1 for d in docs.values() if term in d)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * length / avgdl))
        if score:
            expected[cid] = score

    actual = bm25(chunk_conn, query)
    assert set(actual) == set(expected)
    for cid, score in expected.items():
        assert actual[cid] == pytest.approx(score, abs=1e-9)


def test_scores_are_never_negative(chunk_conn, parsed_pairs):
    """The +1 smoothing in the IDF is what guarantees this. Without it a term
    present in more than half the corpus scores negative and pushes good matches
    below unrelated pairs.
    """
    load(chunk_conn, parsed_pairs)
    for query in ("sales", "market share sales cost promotion store", "the and of"):
        assert all(score >= 0 for score in bm25(chunk_conn, query).values())


def test_a_query_with_no_overlap_returns_nothing_rather_than_everything(
    chunk_conn, parsed_pairs
):
    load(chunk_conn, parsed_pairs)
    assert bm25(chunk_conn, "zqxjkv wmbtlp gharn") == {}
    assert bm25(chunk_conn, "") == {}


def test_indexing_and_querying_use_the_same_analyzer(chunk_conn, parsed_pairs):
    """Stemming has to happen on both sides or nothing matches: a question about
    "monthly costs" must reach a pair keyworded "monthly cost". If the two ever
    use different text-search configurations this is what catches it.
    """
    load(chunk_conn, parsed_pairs)
    singular = bm25(chunk_conn, "monthly cost")
    plural = bm25(chunk_conn, "monthly costs")
    assert singular and singular == pytest.approx(plural)


def test_a_rarer_term_scores_higher_than_a_common_one(chunk_conn, parsed_pairs):
    """IDF doing its job. "slotting" appears in one pair's keywords, "sales" in
    many, so the pair matching on the rare term must outrank one matching only
    on the common one.
    """
    load(chunk_conn, parsed_pairs)
    rare = max(bm25(chunk_conn, "slotting").values())
    common = max(bm25(chunk_conn, "sales").values())
    assert rare > common


def test_rebuilding_is_idempotent(chunk_conn, parsed_pairs):
    """Statistics are rebuilt from scratch on every load rather than updated, so
    running the loader twice must not double the term frequencies.
    """
    load(chunk_conn, parsed_pairs)
    first = bm25(chunk_conn, "gross margin produce")
    gp.rebuild_bm25_index(chunk_conn)
    assert bm25(chunk_conn, "gross margin produce") == pytest.approx(first)


def test_the_index_follows_an_edited_keyword_list(chunk_conn, parsed_pairs):
    """The tsvector is a generated column, so the searchable form cannot drift
    from the text -- but the term statistics are materialised and can.
    """
    load(chunk_conn, parsed_pairs)
    target = parsed_pairs[0]
    assert "zebrafish" not in bm25(chunk_conn, "zebrafish")

    chunk_conn.execute(
        f"UPDATE {gp.TABLE} SET keywords = %s WHERE chunk_id = %s",
        ("zebrafish husbandry", target.chunk_id),
    )
    chunk_conn.commit()
    gp.rebuild_bm25_index(chunk_conn)
    assert list(bm25(chunk_conn, "zebrafish")) == [target.chunk_id]
