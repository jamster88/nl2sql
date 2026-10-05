"""The curation routes (5.6): golden pairs, fixes and SQL snippets, written directly.

The same arrangement as `test_app.py`: a fake staging repository and fake
stores, real copies of the real documents. What these hold the routes to is
the rule the interface is built around -- nothing is written that has not
been run against the retail database, and the route that writes runs it
again -- and to the one that keeps the review queue honest: taking out a
pair or a fix the queue produced puts its submission back in the queue.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from nl2sql_review import snippets as snippets_module
from nl2sql_review.corrections import Fix
from nl2sql_review.promote import PromotionError
from nl2sql_review.snippet_validation import SnippetValidation
from nl2sql_review.validation import Validation

from .conftest import BASE_PAIRS, NEXT_ID, REAL_SNIPPETS, FakeSnippetValidator, FakeValidator, complete

SNIPPETS = len(re.findall(r"^## S\d{2,} - ", REAL_SNIPPETS.read_text(), re.M))
NEXT_SNIPPET = snippets_module.next_snippet_id(REAL_SNIPPETS.read_text())


def empty_result() -> FakeValidator:
    return FakeValidator(
        Validation(sql="", valid=True, warnings=["it ran and returned no rows -- make sure"], row_count=0)
    )


def refused() -> FakeValidator:
    return FakeValidator(Validation(sql="", valid=False, problems=["the database refused it: no such column"]))


# ---------------------------------------------------------------------------
# Golden pairs
# ---------------------------------------------------------------------------


def test_a_golden_pairs_sql_has_to_run_and_return_rows(make_client):
    ran = make_client().post("/v1/golden/validate", json={"sql": "SELECT 1;"}).json()
    assert ran["valid"] is True and ran["sql"] == "SELECT 1"

    empty = make_client(validator=empty_result()).post("/v1/golden/validate", json={"sql": "SELECT 1 WHERE false"}).json()
    assert empty["valid"] is False
    assert empty["problems"] == [
        "it ran and returned no rows; a golden pair's SQL returns its answer, and in the golden set "
        "an empty result reads as a failure"
    ]
    assert empty["warnings"] == []


def test_a_hand_written_pair_is_previewed_without_writing(client, draft, document):
    before = document.read_text()
    body = client.post("/v1/golden/preview", json={"draft": complete(draft)}).json()
    assert body["valid"] is True and body["pair_id"] == NEXT_ID
    assert body["markdown"].startswith(f"## {NEXT_ID} - ")
    assert document.read_text() == before


def test_a_hand_written_pair_is_run_then_written_into_the_golden_set(make_client, draft, document, validator):
    response = make_client().post("/v1/golden", json={"draft": complete(draft)})
    assert response.status_code == 200
    body = response.json()
    assert body["promotion"]["pair_id"] == NEXT_ID
    assert body["promotion"]["pairs_after"] == BASE_PAIRS + 1
    assert body["validation"]["valid"] is True
    # The SQL written is the SQL that ran: cleaned, its semicolon gone.
    assert validator.calls == [(draft.sql_code, "")]
    assert "d.fiscal_year = 2025\n```" in document.read_text()


def test_a_hand_written_pair_whose_sql_does_not_run_is_not_written(make_client, draft, document):
    before = document.read_text()
    response = make_client(validator=refused()).post("/v1/golden", json={"draft": complete(draft)})
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "not_valid", "message": "the database refused it: no such column"}
    assert document.read_text() == before


def test_a_hand_written_pair_the_document_cannot_take_is_refused(make_client, draft):
    response = make_client().post("/v1/golden", json={"draft": {**complete(draft), "keywords": ""}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_promotable"


def test_promoting_a_submission_now_runs_its_sql_first(make_client, draft, repository):
    response = make_client(validator=empty_result()).post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_valid"
    assert repository.get("sub-1").state == "pending"


def test_the_golden_listing_carries_the_sql_and_where_each_pair_came_from(client, draft, repository):
    client.post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})
    pairs = {p["pair_id"]: p for p in client.get("/v1/golden").json()["pairs"]}
    assert pairs[NEXT_ID]["submission_id"] == "sub-1"
    assert pairs[NEXT_ID]["sql_code"].startswith("SELECT ROUND(SUM(s.net_sales_amt), 2)")
    assert pairs["Q01"]["submission_id"] is None and pairs["Q01"]["reasoning_target"]


def test_the_listing_reads_without_the_staging_database(make_client, repository):
    repository.fail_outcome = OSError("down")
    body = make_client().get("/v1/golden").json()
    assert body["count"] == BASE_PAIRS and all(p["submission_id"] is None for p in body["pairs"])


def test_removing_a_hand_written_pair_takes_it_out_of_the_document(make_client, draft, document):
    client = make_client()
    client.post("/v1/golden", json={"draft": complete(draft)})
    response = client.delete(f"/v1/golden/{NEXT_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["submission"] is None and body["withdrawal"]["kind"] == "golden"
    assert (body["withdrawal"]["found"], body["withdrawal"]["pairs_after"]) == (True, BASE_PAIRS)
    assert f"## {NEXT_ID} - " not in document.read_text()


def test_removing_a_promoted_pair_puts_its_submission_back_in_the_queue(client, draft, repository, document):
    client.post("/v1/submissions/sub-1/promote", json={"draft": complete(draft)})
    body = client.delete(f"/v1/golden/{NEXT_ID}").json()
    assert body["submission"]["id"] == "sub-1" and body["submission"]["state"] == "pending"
    assert body["submission"]["draft"]["title"] == draft.title
    assert repository.get("sub-1").promoted_pair_id is None
    assert f"## {NEXT_ID} - " not in document.read_text()


def test_removing_a_pair_that_is_not_there_is_a_404(client):
    response = client.delete("/v1/golden/Q999")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "no pair Q999 in the golden set"


def test_a_removal_that_cannot_ask_the_staging_database_changes_nothing(make_client, repository, document):
    repository.fail_outcome = OSError("connection refused")
    before = document.read_text()
    response = make_client().delete("/v1/golden/Q01")
    assert response.status_code == 503
    assert "could not say whether Q01 came from a submission" in response.json()["error"]["message"]
    assert document.read_text() == before


def test_a_removal_the_document_refuses_is_a_422(make_client):
    def refuse(settings, pair_id):
        raise PromotionError(["the document does not parse"])

    response = make_client(withdrawer=refuse).delete("/v1/golden/Q01")
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "not_withdrawable", "message": "the document does not parse"}


# ---------------------------------------------------------------------------
# Fixes, straight into a store
# ---------------------------------------------------------------------------

FIX = {"question": "top 10 SKUs by net sales", "sql": "SELECT sku_id, product_name FROM dim_product;"}


def test_a_curated_fix_is_validated_against_the_query_it_replaces(make_client, validator):
    client = make_client()
    client.post("/v1/fixes/corrections/validate", json={"sql": "SELECT 2", "incorrect_sql": "SELECT 1"})
    assert validator.calls == [("SELECT 2", "SELECT 1")]


def test_a_curated_fix_is_stored_with_no_submission_and_embedded(make_client, fix_stores):
    response = make_client().post(
        "/v1/fixes/completions", json={**FIX, "incorrect_sql": "SELECT sku_id FROM dim_product", "review_note": "n"},
        headers={"X-Reviewer": "ann"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "completions" and body["embedded"] is True
    assert body["fix"]["fix_id"] == "I0001" and body["fix"]["submission_id"] is None
    assert body["fix"]["source"] == "curated" and body["fix"]["reviewer"] == "token:review-token"
    [saved] = fix_stores["completions"].saved
    assert (saved.corrected_sql, saved.incorrect_sql) == ("SELECT sku_id, product_name FROM dim_product", "SELECT sku_id FROM dim_product")


def test_a_curated_fix_that_does_not_run_is_not_stored(make_client, fix_stores):
    response = make_client(validator=refused()).post("/v1/fixes/corrections", json=FIX)
    assert response.status_code == 422 and fix_stores["corrections"].saved == []


def test_a_curated_fix_a_store_cannot_take_is_a_503(make_client, fix_stores):
    fix_stores["corrections"].fail_save = OSError("disk full")
    response = make_client().post("/v1/fixes/corrections", json=FIX)
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "the corrections store could not be written: OSError: disk full"


def test_a_curated_fix_is_stored_when_the_embedder_cannot_even_be_built(make_client, settings):
    def broken():
        raise ModuleNotFoundError("no ragproc")

    body = make_client(embedder_factory=broken).post("/v1/fixes/corrections", json=FIX).json()
    assert body["embedded"] is False and body["embed_detail"] == "not embedded: ModuleNotFoundError: no ragproc"
    off = make_client(settings=replace(settings, embed_fixes=False)).post("/v1/fixes/corrections", json=FIX).json()
    assert off["embed_detail"] == "embedding is off"


def test_a_question_is_required():
    from nl2sql_review.models import CuratedFixRequest

    with pytest.raises(ValueError):
        CuratedFixRequest(question="", sql="SELECT 1")


def test_removing_a_curated_fix_deletes_it_by_its_own_id(make_client, fix_stores):
    client = make_client()
    client.post("/v1/fixes/corrections", json=FIX)
    body = client.delete("/v1/fixes/corrections/W0001").json()
    assert (body["found"], body["submission"], body["fix"]["question"]) == (True, None, FIX["question"])
    assert fix_stores["corrections"].saved == []


def test_removing_a_reviewed_fix_puts_its_submission_back_in_the_queue(make_client, repository, wrong_submission, fix_stores):
    repository.submissions[wrong_submission.id] = wrong_submission
    client = make_client()
    client.post(f"/v1/submissions/{wrong_submission.id}/fix", json={"sql": "SELECT sku_id, product_name FROM dim_product"})
    body = client.delete("/v1/fixes/corrections/W0001").json()
    assert body["submission"]["id"] == "sub-w" and body["submission"]["state"] == "pending"
    assert body["submission"]["draft"] == {"sql_code": "SELECT sku_id, product_name FROM dim_product"}
    assert fix_stores["corrections"].saved == []


def test_a_fix_whose_submission_is_gone_is_deleted_by_its_id(make_client, fix_stores):
    fix_stores["corrections"].saved.append(Fix(fix_id="W0009", submission_id="sub-gone", job_id="j", question="q"))
    body = make_client().delete("/v1/fixes/corrections/W0009").json()
    assert body["found"] is True and body["submission"] is None


def test_a_fix_another_curator_removed_first_is_reported_as_not_found(make_client, fix_stores, monkeypatch):
    """Read, then deleted by someone else, then asked to be deleted here: the
    delete finds nothing, and says so rather than claiming it removed it."""
    store = fix_stores["corrections"]
    gone = Fix(fix_id="W0007", submission_id=None, job_id="", question="q")
    monkeypatch.setattr(store, "get", lambda fix_id: gone)
    body = make_client().delete("/v1/fixes/corrections/W0007").json()
    assert (body["found"], body["fix"], body["submission"]) == (False, None, None)


def test_a_fix_that_is_not_there_is_a_404_and_a_store_that_is_down_a_503(make_client, fix_stores):
    client = make_client()
    assert client.delete("/v1/fixes/corrections/W0404").status_code == 404
    fix_stores["corrections"].fail_get = OSError("down")
    assert client.delete("/v1/fixes/corrections/W0001").status_code == 503
    fix_stores["corrections"].fail_get = None
    fix_stores["corrections"].saved.append(Fix(fix_id="W0002", submission_id=None, job_id="", question="q"))
    fix_stores["corrections"].fail_delete = OSError("down")
    response = client.delete("/v1/fixes/corrections/W0002")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "the corrections store could not be written: OSError: down"


# ---------------------------------------------------------------------------
# SQL snippets
# ---------------------------------------------------------------------------


def snippet(draft) -> dict:
    return {"draft": draft.as_dict()}


def test_the_snippets_are_listed_from_their_document_with_the_store_beside_them(client, snippet_book):
    body = client.get("/v1/snippets").json()
    assert body["count"] == SNIPPETS and body["next_snippet_id"] == NEXT_SNIPPET
    assert body["kinds"] == ["join", "filter", "measure", "dimension"]
    assert body["store"] == {"reachable": True, "snippets": 32, "embedded": 32, "current": True, "detail": "current"}
    first = body["snippets"][0]
    assert (first["snippet_id"], first["kind"], first["tables"]) == ("S01", "join", ["fact_pos_retail_sales", "dim_date"])
    [(url, digest)] = snippet_book.status_calls
    assert url.endswith("@nowhere:5432/nl2sql_snippets") and len(digest) == 64


def test_a_snippet_document_that_does_not_parse_is_reported_not_listed(client, snippet_document):
    snippet_document.write_text(snippet_document.read_text().replace("**Means:** ", "**Meaning:** ", 1))
    body = client.get("/v1/snippets").json()
    assert body["count"] == 0 and "does not parse" in body["error"]


def test_validating_a_snippet_runs_it_and_holds_its_tables_to_the_ones_it_uses(client, snippet_draft, snippet_validator):
    body = client.post("/v1/snippets/validate", json=snippet(snippet_draft)).json()
    assert body["valid"] is True and body["tables"] == ["fact_pos_retail_sales", "dim_date"]
    assert snippet_validator.calls == [(snippet_draft.kind, snippet_draft.applies_to, snippet_draft.sql)]

    unlisted = client.post("/v1/snippets/validate", json=snippet(replace(snippet_draft, tables="dim_date"))).json()
    assert unlisted["valid"] is False
    assert unlisted["problems"] == [
        "the SQL uses fact_pos_retail_sales, which tables does not list -- the agent shows a snippet only "
        "when every table it lists is in scope"
    ]
    extra = client.post("/v1/snippets/validate", json=snippet(replace(snippet_draft, tables=f"{snippet_draft.tables}, dim_store"))).json()
    assert extra["valid"] is True and extra["warnings"] == ["tables lists dim_store, which the SQL does not use"]


def test_a_snippet_that_does_not_run_says_why_and_is_not_written(make_client, snippet_draft, snippet_document):
    before = snippet_document.read_text()
    validator = FakeSnippetValidator(SnippetValidation(kind="", valid=False, problems=["the database refused it: x"]))
    client = make_client(snippet_validator=validator)
    assert client.post("/v1/snippets/validate", json=snippet(snippet_draft)).json()["problems"] == ["the database refused it: x"]
    response = client.post("/v1/snippets", json=snippet(snippet_draft))
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "not_valid", "message": "the database refused it: x"}
    assert snippet_document.read_text() == before


def test_a_new_snippet_is_previewed_then_written_into_its_section(client, snippet_draft, snippet_document):
    preview = client.post("/v1/snippets/preview", json=snippet(snippet_draft)).json()
    assert preview["valid"] is True and preview["snippet_id"] == NEXT_SNIPPET
    assert preview["markdown"].startswith(f"## {NEXT_SNIPPET} - Holiday sales")

    response = client.post("/v1/snippets", json=snippet(snippet_draft))
    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["snippet_id"], body["snippets_after"]) == ("added", NEXT_SNIPPET, SNIPPETS + 1)
    assert body["validation"]["valid"] is True and body["reloaded"] is False
    assert body["steps"] == [{"name": "load_snippets", "ran": False, "ok": True, "detail": ""}]
    text = snippet_document.read_text()
    assert text.index("# Filters") < text.index(f"## {NEXT_SNIPPET} - ") < text.index("# Measures")


def test_a_snippet_with_no_tables_listed_takes_the_ones_its_sql_uses(client, snippet_draft, snippet_document):
    body = client.post("/v1/snippets", json=snippet(replace(snippet_draft, tables=""))).json()
    assert "tables: fact_pos_retail_sales, dim_date" in body["markdown"]


def test_a_snippet_the_document_cannot_take_is_refused(client, snippet_draft):
    response = client.post("/v1/snippets", json=snippet(replace(snippet_draft, means="")))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_writable"


def test_a_preview_says_why_a_snippet_could_not_be_written(client, snippet_draft, snippet_document):
    missing = client.post("/v1/snippets/preview", json={**snippet(snippet_draft), "snippet_id": "S99"}).json()
    assert missing == {"snippet_id": "S99", "markdown": "", "valid": False, "problems": ["there is no snippet S99 in the document"]}
    snippet_document.unlink()
    gone = client.post("/v1/snippets/preview", json={**snippet(snippet_draft), "snippet_id": "S11"}).json()
    assert gone["snippet_id"] == "S11" and gone["problems"][0].startswith("cannot read the snippet document")


def test_a_snippet_is_changed_in_place_and_a_missing_one_is_a_404(client, snippet_draft, snippet_document):
    body = client.put("/v1/snippets/S11", json=snippet(replace(snippet_draft, name="Public holidays"))).json()
    assert (body["action"], body["snippet_id"], body["snippets_after"]) == ("changed", "S11", SNIPPETS)
    assert "## S11 - Public holidays" in snippet_document.read_text()
    missing = client.put("/v1/snippets/S99", json=snippet(snippet_draft))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_a_snippet_is_removed_and_a_missing_one_is_a_404(client, snippet_document):
    body = client.delete("/v1/snippets/S11").json()
    assert (body["action"], body["validation"], body["snippets_after"]) == ("removed", None, SNIPPETS - 1)
    assert "## S11 - " not in snippet_document.read_text()
    assert client.delete("/v1/snippets/S11").status_code == 404


def test_the_retail_schema_is_listed_for_writing_snippets_against(make_client):
    body = make_client().get("/v1/schema").json()
    assert body == {"tables": [{"name": "dim_date", "columns": [{"name": "date_key", "type": "integer"}]}], "error": None}

    def down():
        raise OSError("connection refused")

    assert make_client(schema_reader=down).get("/v1/schema").json() == {"tables": [], "error": "OSError: connection refused"}


def test_readiness_reports_the_snippet_document_and_the_store(make_client, snippet_book, snippet_document):
    ready = make_client().get("/readyz").json()["checks"]
    assert ready["snippets_document"] == {"ok": True, "detail": f"{SNIPPETS} snippets, next is {NEXT_SNIPPET}"}
    assert ready["snippets_store"] == {"ok": True, "detail": "current"}

    snippet_book.status = {"reachable": False, "detail": "OSError: refused"}
    snippet_document.write_text("## S01 - broken\n")
    response = make_client().get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["snippets_document"]["ok"] is False and "does not parse" in checks["snippets_document"]["detail"]
    assert checks["snippets_store"] == {"ok": False, "detail": "OSError: refused"}


# ---------------------------------------------------------------------------
# The defaults the routes are built with
# ---------------------------------------------------------------------------


def test_the_default_snippet_validator_runs_against_the_configured_retail_database(settings, monkeypatch):
    from nl2sql_review import app as app_module

    seen: dict = {}

    def fake(kind, applies_to, sql, **kwargs):
        seen.update(kwargs, kind=kind, applies_to=applies_to, sql=sql)
        return SnippetValidation(kind=kind, valid=True)

    monkeypatch.setattr(app_module.snippet_validation_module, "validate_snippet", fake)
    run = app_module.default_snippet_validator(replace(settings, retail_db_url="postgresql://r/db", validate_timeout_ms=900))
    assert run("filter", "dim_date d", "d.is_holiday").valid is True
    assert seen == {
        "kind": "filter", "applies_to": "dim_date d", "sql": "d.is_holiday",
        "url": "postgresql://r/db", "timeout_ms": 900, "principal": None,
    }
    run("filter", "dim_date d", "d.is_holiday", principal="cora")
    assert seen["principal"] == "cora", "a signed-in curator's snippet runs as them"


def test_the_default_schema_reader_lists_each_table_with_its_columns_in_order(settings, monkeypatch):
    import psycopg

    from nl2sql_review.app import default_schema_reader

    rows = [("dim_date", "date_key", "integer"), ("dim_date", "fiscal_year", "integer"), ("dim_store", "store_id", "text")]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement):
            assert "information_schema.columns" in statement and "ordinal_position" in statement
            return self

        def fetchall(self):
            return rows

    monkeypatch.setattr(psycopg, "connect", lambda url, **kwargs: Conn())
    assert default_schema_reader(replace(settings, retail_db_url="postgresql://r/db"))() == [
        {"name": "dim_date", "columns": [{"name": "date_key", "type": "integer"}, {"name": "fiscal_year", "type": "integer"}]},
        {"name": "dim_store", "columns": [{"name": "store_id", "type": "text"}]},
    ]
