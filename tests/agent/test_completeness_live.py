"""The Completeness Reviewer against the real database (arch5 section 6.6).

`test_completeness.py` drives every rule with hand-built rows. This asks the
live one, because two things only a real run can show: the values psycopg
actually returns -- `Decimal` sums, integer keys, date types -- which is what
R2's measure detection and R3's year check read; and the label map and
fiscal calendar as the catalog really declares them. The SQL below is the
kind the generator wrote for the two questions arch5 was written for.

Opt-in (`pytest --run-docker`), against POSTGRES_URL as the read-only role,
and skipped rather than failed when nothing is listening.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy
from nl2sql_agent.completeness import R1, R2, R3, review
from nl2sql_agent.contract import build_contract, load_resources
from nl2sql_agent.database import Database
from nl2sql_agent.state import QueryResult

pytestmark = pytest.mark.docker

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"
)

BARE_SKUS = (
    "SELECT p.sku_id FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) "
    "GROUP BY 1 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 10"
)
FULL_SKUS = (
    "SELECT p.sku_id, p.product_name, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
    "FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) "
    "JOIN dim_date d ON d.date_key = s.sales_date_key WHERE d.fiscal_year = 2025 "
    "GROUP BY 1, 2 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 10"
)
# The first live draft of "top ten stores": a date-key range instead of a
# fiscal_year filter, and the surrogate key out of the fact table.
BARE_STORES = (
    "SELECT s.store_key, SUM(net_sales_amt) AS sales FROM fact_pos_retail_sales s "
    "WHERE sales_date_key BETWEEN 20240401 AND 20250331 GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
)
FULL_STORES = (
    "SELECT st.store_id, st.store_name, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
    "FROM fact_pos_retail_sales s JOIN dim_store st ON st.store_key = s.store_key "
    "JOIN dim_date d ON d.date_key = s.sales_date_key WHERE d.fiscal_year = 2025 "
    "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10"
)


@pytest.fixture(scope="module")
def db() -> Database:
    database = Database(POSTGRES_URL)
    try:
        database.table_names()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        pytest.skip(f"no reachable Postgres at {POSTGRES_URL}: {exc}")
    return database


@pytest.fixture(scope="module")
def resources(db: Database):
    return load_resources(db)


def run(db: Database, sql: str) -> QueryResult:
    raw = db.run_select(sql)
    return QueryResult(columns=list(raw.columns), rows=[list(r) for r in raw.rows],
                       truncated=raw.truncated)


def check(db, resources, question, sql, **fields):
    contract = build_contract(question, resources=resources, **fields)
    return review(question=question, sql=sql, result=run(db, sql), contract=contract,
                  label_map=resources.label_map)


def test_the_contract_is_built_from_what_the_catalog_and_calendar_say(resources):
    assert resources.errors == {}
    contract = build_contract("top 10 SKUs", entities=["sku"], resources=resources)
    entity = contract.entities[0]
    assert (entity.key, entity.label, entity.table) == ("sku_id", "product_name", "dim_product")
    assert (contract.period, contract.fiscal_year_start, contract.fiscal_year_end) == (
        "FY2025", "2024-04-01", "2025-03-31"
    )


def test_bare_skus_are_sent_back_for_their_names_their_sales_and_their_year(db, resources):
    outcome = check(db, resources, "top 10 SKUs", BARE_SKUS, entities=["sku"])
    assert [g.rule for g in outcome.report.missing] == [R1, R2, R3]
    assert outcome.issue is not None and outcome.assumptions == []


def test_named_skus_with_their_fy2025_sales_are_complete(db, resources):
    outcome = check(db, resources, "top 10 SKUs", FULL_SKUS, entities=["sku"])
    assert outcome.issue is None and outcome.report.passed
    assert outcome.assumptions[0].startswith("FY2025 (2024-04-01 to 2025-03-31)")


def test_a_store_key_out_of_the_fact_still_wants_the_store_name(db, resources):
    """The date-key range counts as the fiscal year; the surrogate key does
    not count as a name."""
    outcome = check(db, resources, "top ten stores", BARE_STORES, entities=["store"])
    assert [(g.rule, g.column, g.table) for g in outcome.report.missing] == [
        (R1, "store_name", "dim_store")
    ]


def test_named_stores_with_their_fy2025_sales_are_complete(db, resources):
    outcome = check(db, resources, "top ten stores", FULL_STORES, entities=["store"])
    assert outcome.issue is None and outcome.report.passed
    assert len(run(db, FULL_STORES).rows) == 10


def test_a_count_of_stores_is_left_alone(db, resources):
    """B01: one number, no entity, nothing to reflect on and nothing to add."""
    outcome = check(db, resources, "How many stores are there?",
                    "SELECT COUNT(DISTINCT store_id) AS store_count FROM dim_store",
                    entities=["store"], measure="count")
    assert outcome.issue is None and outcome.report.passed and not outcome.report.reflected
    assert outcome.assumptions == []
