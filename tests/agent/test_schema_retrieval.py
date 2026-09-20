"""The Schema Retriever: what it picks, what it adds, and what it gives up.

Proves the four steps of section 4.2 without a model call anywhere: top-k
tables by cosine distance, hinted tables merged, foreign-key closure filling in
the join tables no question ever names, and a hard cap that spends leaves
before bridges. It also proves the degraded paths, because retrieval is
best-effort: an unreachable vector store and an unreadable catalog both have to
return a usable selection rather than raise.

The offline tests run against the real retail foreign-key graph as a literal,
so a bridge asserted here is a bridge in the database. The handful of
`@pytest.mark.docker` tests at the end re-prove the load-bearing one against
the live catalog and the live vector store, which is the only way to know the
literal is still true.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from nl2sql_agent.database import Database
from nl2sql_agent.retrieval import KnowledgeBase, build_embedder
from nl2sql_agent.schema_retrieval import (
    SchemaRetriever,
    SchemaSelection,
    read_foreign_keys,
)

from .conftest import FakeDatabase, FakeKnowledgeBase, make_chunk

# The 19 tables of data_gen/ddl.sql.
RETAIL_TABLES = [
    "dim_ad_channel",
    "dim_ad_placement",
    "dim_allowance_type",
    "dim_competitor",
    "dim_date",
    "dim_geography",
    "dim_product",
    "dim_promo_calendar",
    "dim_promotion",
    "dim_store",
    "dim_vendor",
    "fact_ad_performance",
    "fact_competitor_pricing",
    "fact_item_cogs",
    "fact_item_prices",
    "fact_market_share_weekly",
    "fact_pos_retail_sales",
    "fact_promo_performance",
    "fact_vendor_allowances",
]

# Every foreign key in data_gen/ddl.sql, child -> parent, as pg_constraint
# reports them. `test_the_live_catalog_still_has_these_edges` keeps this honest.
RETAIL_EDGES = [
    ("dim_ad_placement", "dim_ad_channel"),
    ("fact_ad_performance", "dim_ad_placement"),
    ("fact_ad_performance", "dim_date"),
    ("fact_ad_performance", "dim_product"),
    ("fact_ad_performance", "dim_store"),
    ("fact_competitor_pricing", "dim_competitor"),
    ("fact_competitor_pricing", "dim_date"),
    ("fact_competitor_pricing", "dim_product"),
    ("fact_competitor_pricing", "dim_store"),
    ("fact_item_cogs", "dim_date"),
    ("fact_item_cogs", "dim_product"),
    ("fact_item_cogs", "dim_store"),
    ("fact_item_prices", "dim_date"),
    ("fact_item_prices", "dim_product"),
    ("fact_item_prices", "dim_store"),
    ("fact_market_share_weekly", "dim_competitor"),
    ("fact_market_share_weekly", "dim_date"),
    ("fact_market_share_weekly", "dim_geography"),
    ("fact_market_share_weekly", "dim_product"),
    ("fact_pos_retail_sales", "dim_date"),
    ("fact_pos_retail_sales", "dim_product"),
    ("fact_pos_retail_sales", "dim_store"),
    ("fact_promo_performance", "dim_date"),
    ("fact_promo_performance", "dim_product"),
    ("fact_promo_performance", "dim_promo_calendar"),
    ("fact_promo_performance", "dim_promotion"),
    ("fact_promo_performance", "dim_store"),
    ("fact_vendor_allowances", "dim_allowance_type"),
    ("fact_vendor_allowances", "dim_date"),
    ("fact_vendor_allowances", "dim_product"),
    ("fact_vendor_allowances", "dim_store"),
    ("fact_vendor_allowances", "dim_vendor"),
]

B15 = (
    "How many ad impressions and clicks did each type of advertising channel "
    "-- Print Flyer, Paid Social and so on -- generate in fiscal year 2025?"
)


def ddl_chunks(tables: list[str | None]) -> list:
    """One DDL chunk per table, already ordered best match first.

    A `None` stands for the collection's header chunk, which carries no table.
    """
    return [
        make_chunk(
            collection="ddl_index_embeddings",
            source_doc="ddl_index",
            heading_path=f"DDL Index > {table}" if table else "DDL Index",
            meta={"table": table} if table else {},
            distance=0.30 + 0.02 * position,
        )
        for position, table in enumerate(tables)
    ]


def make_retriever(
    ranked: list[str | None],
    *,
    edges: list[tuple[str, str]] | None = None,
    tables: list[str] | None = None,
    top_k: int = 6,
    max_tables: int = 10,
    error: str | None = None,
) -> SchemaRetriever:
    knowledge_base = FakeKnowledgeBase(chunks=ddl_chunks(ranked), error=error)
    database = FakeDatabase(tables=list(tables if tables is not None else RETAIL_TABLES))
    return SchemaRetriever(
        knowledge_base,
        database,
        top_k=top_k,
        max_tables=max_tables,
        foreign_keys=lambda: list(RETAIL_EDGES if edges is None else edges),
    )


# ---------------------------------------------------------------------------
# Step 1: top-k tables by cosine distance
# ---------------------------------------------------------------------------


def test_the_top_k_tables_are_taken_in_cosine_distance_order():
    retriever = make_retriever(
        ["fact_pos_retail_sales", "dim_store", "dim_date", "dim_product"],
        edges=[],
        top_k=3,
    )
    assert retriever.select("Which stores sold the most?").tables == [
        "fact_pos_retail_sales",
        "dim_store",
        "dim_date",
    ]


def test_the_table_less_header_chunk_does_not_spend_a_slot():
    """The DDL collection holds a header chunk with no `table` in its meta, so a
    k-chunk search can return fewer than k tables. The retriever asks for a
    couple of extra chunks to cover it and still returns k tables.
    """
    retriever = make_retriever(
        [None, "fact_pos_retail_sales", "dim_store", "dim_date"],
        edges=[],
        top_k=3,
    )
    selection = retriever.select("Which stores sold the most?")

    assert selection.tables == ["fact_pos_retail_sales", "dim_store", "dim_date"]
    assert retriever._knowledge_base.search_calls == [("Which stores sold the most?", 5)]


def test_a_chunk_for_a_table_the_database_does_not_have_is_ignored():
    retriever = make_retriever(
        ["fact_retired_sales_2019", "dim_store"], edges=[], top_k=6
    )
    assert retriever.select("anything").tables == ["dim_store"]


def test_one_question_costs_exactly_one_vector_search_and_no_model_call():
    """The whole point of the module: it replaces the v3 `select_tables` LLM
    call with a single embedding lookup, so nothing here may fan out.
    """
    retriever = make_retriever(["dim_store"], edges=[])
    retriever.select("anything")
    assert len(retriever._knowledge_base.search_calls) == 1


# ---------------------------------------------------------------------------
# Step 2: hinted tables from the Knowledge and Example Retrievers
# ---------------------------------------------------------------------------


def test_hinted_tables_are_merged_behind_the_ranked_ones():
    retriever = make_retriever(["fact_pos_retail_sales", "dim_store"], edges=[], top_k=2)
    selection = retriever.select("anything", hinted_tables=["dim_date", "dim_product"])
    assert selection.tables == [
        "fact_pos_retail_sales",
        "dim_store",
        "dim_date",
        "dim_product",
    ]


def test_a_hinted_table_that_does_not_exist_is_ignored():
    """Hints come from chunk metadata and golden-pair text, neither of which is
    checked against the catalog, so a stale document cannot invent a table.
    """
    retriever = make_retriever(["dim_store"], edges=[], top_k=1)
    selection = retriever.select("anything", hinted_tables=["fact_sales_2019", "dim_date"])
    assert selection.tables == ["dim_store", "dim_date"]


def test_a_hinted_table_already_ranked_is_not_repeated():
    retriever = make_retriever(["dim_store", "dim_date"], edges=[], top_k=2)
    selection = retriever.select("anything", hinted_tables=["dim_date"])
    assert selection.tables == ["dim_store", "dim_date"]


# ---------------------------------------------------------------------------
# Step 3: foreign-key closure
# ---------------------------------------------------------------------------


def test_the_bridge_between_the_ad_fact_and_the_ad_channel_is_added():
    """B15, the question this module exists for. `fact_ad_performance` has no
    channel key; the channel hangs off `dim_ad_placement`. Nothing in the
    question names that table, so only the foreign keys can supply it.
    """
    retriever = make_retriever(
        ["fact_ad_performance", "dim_ad_channel", "fact_promo_performance"], top_k=2
    )
    selection = retriever.select(B15)

    assert selection.bridges == ["dim_ad_placement"]
    assert selection.tables == [
        "fact_ad_performance",
        "dim_ad_channel",
        "dim_ad_placement",
    ]


def test_a_bridge_is_found_for_a_pair_that_only_exists_after_the_hints():
    """Closure runs over the merged set, not the ranked one, so a table the
    Example Retriever contributed can be the far end of a join path.
    """
    retriever = make_retriever(["fact_ad_performance"], top_k=1)
    selection = retriever.select(B15, hinted_tables=["dim_ad_channel"])
    assert selection.bridges == ["dim_ad_placement"]


def test_directly_joined_tables_need_no_bridge():
    retriever = make_retriever(["fact_pos_retail_sales", "dim_store"], top_k=2)
    selection = retriever.select("Which stores sold the most?")
    assert selection.bridges == []
    assert selection.tables == ["fact_pos_retail_sales", "dim_store"]


def test_no_bridge_is_added_when_many_tables_join_the_pair_equally_well():
    """`dim_product` and `dim_store` are two hops apart through all seven fact
    tables that reference both, and none of them is the one the question is
    about. Adding every table on a shortest path would add all seven and crowd
    out the ranked tables, so a bridge has to be the *only* way through.
    """
    retriever = make_retriever(["dim_product", "dim_store"], top_k=2)
    assert retriever.select("anything").bridges == []


def test_tables_with_no_join_path_between_them_produce_no_bridge():
    retriever = make_retriever(
        ["dim_store", "dim_vendor"],
        edges=[("fact_pos_retail_sales", "dim_store")],
        top_k=2,
    )
    selection = retriever.select("anything")
    assert selection.bridges == []
    assert selection.tables == ["dim_store", "dim_vendor"]


def test_the_foreign_key_graph_is_read_once_and_reused():
    """The catalog cannot change under a run, and the closure is per pair, so
    re-reading it would be a round trip per question for a constant.
    """
    reads = []

    def count_reads():
        reads.append(1)
        return list(RETAIL_EDGES)

    retriever = SchemaRetriever(
        FakeKnowledgeBase(chunks=ddl_chunks(["fact_ad_performance", "dim_ad_channel"])),
        FakeDatabase(tables=list(RETAIL_TABLES)),
        top_k=2,
        foreign_keys=count_reads,
    )
    retriever.select(B15)
    retriever.select(B15)
    assert len(reads) == 1


# ---------------------------------------------------------------------------
# Step 4: the cap
# ---------------------------------------------------------------------------


def test_the_cap_drops_the_lowest_ranked_tables():
    ranked = [
        "fact_pos_retail_sales",
        "dim_store",
        "dim_date",
        "dim_product",
        "fact_item_cogs",
        "fact_item_prices",
        "dim_vendor",
        "dim_allowance_type",
        "dim_competitor",
        "dim_geography",
        "dim_promotion",
        "dim_promo_calendar",
    ]
    retriever = make_retriever(ranked, edges=[], top_k=12, max_tables=10)
    selection = retriever.select("anything")

    assert selection.tables == ranked[:10]
    assert selection.dropped == ["dim_promo_calendar", "dim_promotion"]


def test_the_cap_drops_a_weakly_ranked_leaf_before_a_bridge():
    """A bridge is the only way to express a join; a leaf at rank ten is a
    guess. The cap spends the guess.
    """
    ranked = [
        "fact_ad_performance",
        "dim_ad_channel",
        "fact_pos_retail_sales",
        "fact_item_cogs",
        "fact_item_prices",
        "fact_promo_performance",
        "fact_competitor_pricing",
        "fact_market_share_weekly",
        "dim_product",
        "dim_store",
    ]
    retriever = make_retriever(ranked, top_k=10, max_tables=10)
    selection = retriever.select(B15)

    assert selection.bridges == ["dim_ad_placement"]
    assert selection.dropped == ["dim_store"]
    assert len(selection.tables) == 10
    assert "dim_ad_placement" in selection.tables


def test_a_cap_below_the_length_of_the_join_path_still_returns_a_legal_selection():
    """A chain of five tables closed from both ends is all bridge and no leaf,
    so a cap of one has nothing safe to spend and has to start on the bridges.
    The guarantee that survives is the one consumers rely on: `bridges` stays a
    subset of `tables`, and everything removed is named in `dropped`.
    """
    retriever = make_retriever(
        ["t_a", "t_e"],
        edges=[("t_a", "t_b"), ("t_b", "t_c"), ("t_c", "t_d"), ("t_d", "t_e")],
        tables=["t_a", "t_b", "t_c", "t_d", "t_e"],
        top_k=2,
        max_tables=1,
    )
    selection = retriever.select("anything")

    assert selection.tables == ["t_b"]
    assert selection.bridges == ["t_b"]
    assert selection.dropped == ["t_e", "t_a", "t_d", "t_c"]
    assert set(selection.bridges) <= set(selection.tables)


# ---------------------------------------------------------------------------
# Step 5: best-effort degradation
# ---------------------------------------------------------------------------


def test_an_unreachable_vector_store_degrades_to_the_whole_schema():
    """With no ranking and no hints there is nothing to cap against, so the
    fallback is every table: the first ten alphabetically are ten dimensions
    and not one fact table, which would fail every question.
    """
    retriever = make_retriever([], error="vectordb refused the connection")
    selection = retriever.select("anything")

    assert selection.error == "vectordb refused the connection"
    assert selection.tables == sorted(RETAIL_TABLES)
    assert len(selection.tables) > 10


def test_an_unreachable_vector_store_still_closes_over_the_hints():
    retriever = make_retriever([], error="embedding model not found", top_k=6)
    selection = retriever.select(B15, hinted_tables=["fact_ad_performance", "dim_ad_channel"])

    assert selection.error == "embedding model not found"
    assert selection.bridges == ["dim_ad_placement"]
    assert selection.tables == [
        "fact_ad_performance",
        "dim_ad_channel",
        "dim_ad_placement",
    ]


def test_an_empty_store_falls_back_without_calling_it_an_error():
    retriever = make_retriever([], edges=[])
    selection = retriever.select("anything")

    assert selection.error is None
    assert selection.tables == sorted(RETAIL_TABLES)


def test_a_catalog_that_will_not_answer_costs_the_bridges_not_the_run():
    def explode():
        raise RuntimeError("permission denied for pg_constraint")

    retriever = SchemaRetriever(
        FakeKnowledgeBase(chunks=ddl_chunks(["fact_ad_performance", "dim_ad_channel"])),
        FakeDatabase(tables=list(RETAIL_TABLES)),
        top_k=2,
        foreign_keys=explode,
    )
    selection = retriever.select(B15)

    assert selection.tables == ["fact_ad_performance", "dim_ad_channel"]
    assert selection.bridges == []
    assert "permission denied for pg_constraint" in (selection.error or "")


def test_the_empty_selection_is_a_usable_value():
    assert SchemaSelection().tables == []
    assert SchemaSelection().error is None


# ---------------------------------------------------------------------------
# read_foreign_keys
# ---------------------------------------------------------------------------


class _RecordingConnection:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return SimpleNamespace(all=lambda: list(self.rows))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RecordingEngine:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.connection = _RecordingConnection(rows)

    def connect(self):
        return self.connection


def test_read_foreign_keys_asks_only_for_the_configured_schema():
    """A second schema with the same table names would otherwise fuse two
    unrelated graphs into one and invent join paths that do not exist.
    """
    engine = _RecordingEngine([("fact_ad_performance", "dim_ad_placement")])
    database = SimpleNamespace(_engine=engine, _schema="retail")

    assert read_foreign_keys(database) == [("fact_ad_performance", "dim_ad_placement")]
    statement, params = engine.connection.calls[0]
    assert params == {"schema": "retail"}
    assert "contype = 'f'" in statement


def test_read_foreign_keys_defaults_to_public():
    engine = _RecordingEngine([])
    read_foreign_keys(SimpleNamespace(_engine=engine))
    assert engine.connection.calls[0][1] == {"schema": "public"}


# ---------------------------------------------------------------------------
# Live: the real catalog and the real vector store (pytest --run-docker)
# ---------------------------------------------------------------------------

RETAIL_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail",
)
VECTOR_DB_URL = os.environ.get(
    "TEST_VECTOR_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors"
)
EMBED_BASE_URL = os.environ.get("TEST_EMBED_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("TEST_EMBED_MODEL", "bge-m3")
DDL_COLLECTION = "ddl_index_embeddings"


class _Settings:
    embed_model = EMBED_MODEL
    embed_base_url = EMBED_BASE_URL


@pytest.fixture(scope="module")
def live_database() -> Database:
    database = Database(RETAIL_DB_URL)
    try:
        database.table_names()
    except Exception as exc:
        pytest.skip(f"no reachable retail database at {RETAIL_DB_URL}: {exc}")
    return database


@pytest.fixture(scope="module")
def live_knowledge_base() -> KnowledgeBase:
    knowledge_base = KnowledgeBase(
        VECTOR_DB_URL, build_embedder(_Settings()), collections=[DDL_COLLECTION]
    )
    try:
        knowledge_base.search("smoke test", top_k=1)
    except Exception as exc:
        pytest.skip(f"no reachable DDL vectors at {VECTOR_DB_URL}: {exc}")
    return knowledge_base


@pytest.mark.docker
def test_the_live_catalog_still_has_these_edges(live_database: Database):
    """Guards RETAIL_EDGES, which every offline bridge assertion rests on."""
    edges = read_foreign_keys(live_database)
    assert sorted(edges) == sorted(RETAIL_EDGES)


@pytest.mark.docker
def test_live_closure_supplies_the_ad_placement_bridge(live_database, live_knowledge_base):
    """The mechanism, end to end and against real data: similarity ranks the ad
    fact and the ad channel, nothing ranks the table that joins them, and the
    foreign keys add it. k is held at 2 because the DDL chunk for
    `dim_ad_placement` carries ad keywords of its own and ranks third at k=6 --
    so at the default k this question is covered twice over, and only a tight k
    isolates the closure as the thing that supplied the table.
    """
    retriever = SchemaRetriever(live_knowledge_base, live_database, top_k=2)
    selection = retriever.select(B15)

    assert selection.error is None
    assert selection.bridges == ["dim_ad_placement"]
    assert selection.tables == [
        "fact_ad_performance",
        "dim_ad_channel",
        "dim_ad_placement",
    ]


@pytest.mark.docker
def test_live_default_selection_covers_the_b15_join_path_within_the_cap(
    live_database, live_knowledge_base
):
    retriever = SchemaRetriever(live_knowledge_base, live_database)
    selection = retriever.select(B15)

    assert {"fact_ad_performance", "dim_ad_placement", "dim_ad_channel"} <= set(
        selection.tables
    )
    assert len(selection.tables) <= 10


@pytest.mark.docker
def test_live_selection_is_smaller_than_the_schema_it_came_from(
    live_database, live_knowledge_base
):
    """Pruning is the other half of the job: a selection that returned all 19
    tables would be the schema-only baseline wearing a retriever's name.
    """
    retriever = SchemaRetriever(live_knowledge_base, live_database)
    selection = retriever.select("Which stores had the highest net sales in fiscal 2025?")

    assert selection.error is None
    assert "fact_pos_retail_sales" in selection.tables
    assert len(selection.tables) < len(live_database.table_names())


# ---------------------------------------------------------------------------
# close_and_cap: the entry point the Context Aggregator uses
# ---------------------------------------------------------------------------


def test_closing_over_a_given_table_set_makes_no_vector_search():
    """The aggregator is the only place all three table proposals are known,
    so it is the only place the closure can be applied to their union. Going
    back through `select` there would pay for a second embedding call to
    learn something already in state.
    """
    retriever = make_retriever(["dim_store"])
    searches: list[str] = []
    inner = retriever._knowledge_base.search
    retriever._knowledge_base.search = lambda q, *a, **k: (searches.append(q), inner(q, *a, **k))[1]

    selection = retriever.close_and_cap(["fact_ad_performance", "dim_ad_channel"])

    assert searches == [], f"close_and_cap embedded the question: {searches}"
    assert "dim_ad_placement" in selection.bridges
    # And the counter really does catch a search, so the assertion above can fail.
    retriever.select("anything")
    assert searches == ["anything"]


def test_closing_pulls_in_the_bridge_between_two_proposed_tables():
    retriever = make_retriever([])
    selection = retriever.close_and_cap(["fact_ad_performance", "dim_ad_channel"])
    assert selection.bridges == ["dim_ad_placement"]
    assert set(selection.tables) >= {"fact_ad_performance", "dim_ad_channel", "dim_ad_placement"}


def test_closing_keeps_the_order_the_aggregator_proposed():
    """The union arrives ranked -- vectors first, then the tables the
    knowledge chunks and worked examples named -- and the cap drops from the
    bottom, so the order carries meaning.
    """
    retriever = make_retriever([])
    selection = retriever.close_and_cap(["dim_store", "dim_product", "dim_date"])
    assert selection.tables[:3] == ["dim_store", "dim_product", "dim_date"]


def test_closing_ignores_a_table_the_catalog_does_not_have():
    retriever = make_retriever([])
    selection = retriever.close_and_cap(["dim_store", "dim_storefront"])
    assert "dim_storefront" not in selection.tables


def test_closing_deduplicates_what_three_retrievers_proposed_twice():
    """Every Stage 1 retriever may name the same table; the fan-in is where
    that stops mattering.
    """
    retriever = make_retriever([])
    selection = retriever.close_and_cap(["dim_store", "dim_store", "dim_product", "dim_store"])
    assert selection.tables.count("dim_store") == 1


def test_closing_applies_the_cap_and_says_what_it_dropped():
    retriever = make_retriever([], max_tables=3)
    selection = retriever.close_and_cap(
        ["dim_store", "dim_product", "dim_date", "dim_vendor", "dim_promotion"]
    )
    assert len(selection.tables) <= 3
    assert selection.dropped


def test_closing_an_empty_set_falls_back_to_the_catalog():
    """Every retriever failed. Schema-only is v1's behaviour and still
    answers a good many questions, so it beats returning nothing.
    """
    retriever = make_retriever([], max_tables=4)
    selection = retriever.close_and_cap([])
    assert selection.tables
    assert len(selection.tables) <= 4


def test_two_tables_with_no_join_path_between_them_need_no_bridge():
    """An unreachable pair is not an error: the question may legitimately
    want two unrelated tables, and inventing a path would add noise.
    """
    retriever = make_retriever([], edges=[("dim_store", "dim_geography")])
    selection = retriever.close_and_cap(["dim_store", "dim_product"])
    assert selection.bridges == []
    assert set(selection.tables) == {"dim_store", "dim_product"}


def test_a_self_referencing_foreign_key_is_not_an_edge():
    """A table that references itself cannot bridge anything, and treating
    it as an edge would make every BFS visit it twice.
    """
    retriever = make_retriever(
        [], edges=[("dim_product", "dim_product"), ("dim_product", "dim_store")]
    )
    selection = retriever.close_and_cap(["dim_product", "dim_store"])
    assert selection.bridges == []


def test_a_foreign_key_to_a_table_outside_the_catalog_is_ignored():
    retriever = make_retriever([], edges=[("dim_store", "some_other_schema_table")])
    selection = retriever.close_and_cap(["dim_store"])
    assert selection.tables == ["dim_store"]


def test_asking_for_no_ranked_tables_returns_none_without_embedding():
    """`SCHEMA_TOP_K=0` is the configuration that leaves table selection
    entirely to the knowledge and example hints.
    """
    retriever = make_retriever(["dim_store", "dim_product"], top_k=0)
    selection = retriever.select("anything")
    assert selection.tables == sorted(RETAIL_TABLES)
