package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.nl2sql.desktop.Fakes;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ModelsTest {

    @Test
    void a_job_parses_from_the_document_the_api_documents() {
        // Lifted from agent/API.md, which is the contract this mirrors.
        Models.Job job = Json.read("""
                {"id": "3f2c", "status": "succeeded",
                 "question": "What was total net sales for Produce in FY2025?",
                 "created_at": "2026-09-25T10:00:00Z", "finished_at": "2026-09-25T10:01:02Z",
                 "duration_ms": 61432.5,
                 "progress": [{"seq": 4, "step": "generate_sql", "label": "sql",
                               "detail": "3 tables", "at": "2026-09-25T10:00:30Z"}],
                 "answer": {"answer": "Produce net sales were $719,279.97 in FY2025.",
                            "narrative": "Produce net sales were $719,279.97 in FY2025.",
                            "sql": "SELECT sum(x) FROM y", "verdict": "proceed",
                            "intent": "aggregate", "clarification": null,
                            "tables": ["dim_product"],
                            "literals": [{"phrase": "produse", "table": "dim_product",
                                          "column": "department", "value": "Produce",
                                          "score": 0.81}],
                            "result": {"columns": ["net_sales"], "rows": [["719279.97"]],
                                       "row_count": 1, "truncated": false},
                            "chart": {"kind": "bar", "x": null, "y": ["net_sales"],
                                      "series": null},
                            "claims": [{"text": "...", "value": 719279.97,
                                        "cells": [[0, "net_sales"]], "formula": null}],
                            "audit": {"passed": true, "unsupported_claims": [],
                                      "drop_reasons": [], "redactions": [],
                                      "semantic_issue": null},
                            "plan_cost": 125767.4, "attempts": 1,
                            "trace": [{"node": "generate_sql", "ms": 8123.4,
                                       "model_calls": 1, "detail": "..."}],
                            "retrieval_errors": {}},
                 "error": null,
                 "links": {"self": "/v1/questions/3f2c", "events": "/v1/questions/3f2c/events"}}
                """, Models.Job.class);

        assertEquals("3f2c", job.id());
        assertEquals(Models.JobStatus.SUCCEEDED, job.status());
        assertEquals("sql", job.progress().get(0).label());
        assertEquals("SELECT sum(x) FROM y", job.answer().sql());
        assertEquals("719279.97", job.answer().result().rows().get(0).get(0));
        assertEquals(List.of("net_sales"), job.answer().chart().y());
        assertEquals("/v1/questions/3f2c/events", job.links().events());
    }

    @Test
    void a_field_the_server_did_not_send_arrives_as_a_list_rather_than_a_null() {
        // The server promises it; this keeps the promise even against one
        // older than the field being read. A GUI that null-checks a list it
        // was told is always there would be checking it in four places and
        // forgetting the fifth.
        Models.Answer answer = Json.read("{}", Models.Answer.class);

        assertEquals(List.of(), answer.tables());
        assertEquals(List.of(), answer.literals());
        assertEquals(List.of(), answer.claims());
        assertEquals(List.of(), answer.trace());
        assertEquals(Map.of(), answer.retrieval_errors());
        assertEquals("", answer.sql());
        assertTrue(answer.audit().passed());
        assertEquals(List.of(), answer.audit().drop_reasons());
    }

    @Test
    void the_two_fields_that_are_genuinely_nullable_stay_null() {
        // A refusal has no result and no chart, and filling them in with
        // empties would report a query that never ran.
        Models.Answer answer = Json.read("{}", Models.Answer.class);

        assertNull(answer.result());
        assertNull(answer.chart());
        assertNull(answer.clarification());
    }

    @Test
    void a_null_cell_survives_being_copied() {
        // List.copyOf would refuse it, and an empty column is an ordinary
        // answer rather than a reason to fail while parsing.
        Models.ResultTable table = Json.read(
                "{\"columns\": [\"a\"], \"rows\": [[null]]}", Models.ResultTable.class);

        assertNull(table.rows().get(0).get(0));
    }

    @Test
    void a_job_with_no_links_still_has_somewhere_to_put_them() {
        assertEquals("", Json.read("{\"id\": \"x\"}", Models.Job.class).links().events());
    }

    @ParameterizedTest
    @EnumSource(Models.JobStatus.class)
    void every_status_round_trips_through_its_wire_name(Models.JobStatus status) {
        assertSame(status, Models.JobStatus.of(status.wire()));
        assertEquals('"' + status.wire() + '"', Json.write(status));
    }

    @Test
    void the_three_terminal_statuses_are_the_ones_that_will_not_change_again() {
        assertTrue(Models.JobStatus.SUCCEEDED.terminal());
        assertTrue(Models.JobStatus.FAILED.terminal());
        assertTrue(Models.JobStatus.CANCELLED.terminal());
        assertFalse(Models.JobStatus.QUEUED.terminal());
        assertFalse(Models.JobStatus.RUNNING.terminal());
    }

    @Test
    void a_status_this_client_has_not_heard_of_is_refused_by_name() {
        assertTrue(assertThrows(IllegalArgumentException.class,
                () -> Models.JobStatus.of("pondering")).getMessage().contains("pondering"));
    }

    @ParameterizedTest
    @EnumSource(Models.Verdict.class)
    void a_verdict_round_trips_too(Models.Verdict verdict) {
        assertSame(verdict, Models.Verdict.of(verdict.wire()));
        assertEquals('"' + verdict.wire() + '"', Json.write(verdict));
    }

    @Test
    void a_verdict_this_client_has_not_heard_of_is_refused() {
        assertThrows(IllegalArgumentException.class, () -> Models.Verdict.of("maybe"));
    }

    @Test
    void a_request_carries_only_the_opinion() {
        // The question, the SQL and the result shape are the server's to
        // read off the job. A client that supplied its own snapshot could
        // supply one that never matched it.
        assertEquals("{\"verdict\":\"no\",\"comment\":\"the fiscal month is off by one\"}",
                Json.write(new Models.FeedbackRequest(Models.Verdict.NO,
                        "the fiscal month is off by one")));
    }

    @Test
    void an_ask_carries_the_question_and_whatever_the_caller_wanted_echoed_back() {
        assertEquals("{\"question\":\"how many stores\",\"principal\":null,\"metadata\":{}}",
                Json.write(new Models.AskRequest("how many stores", null, Map.of())));
    }

    @Test
    void a_field_the_server_learned_to_send_since_is_ignored_rather_than_refused() {
        // A client that broke on the next release of a service it does not
        // control would be a client nobody could deploy.
        assertEquals("ok", Json.read(
                "{\"status\": \"ok\", \"version\": \"4.4.0\", \"uptime_seconds\": 1.0,"
                        + " \"something_new\": 42}", Models.Health.class).status());
    }

    @Test
    void the_sample_answer_is_the_shape_the_rest_of_these_tests_assume() {
        Models.Answer answer = Fakes.answer();

        assertEquals("proceed", answer.verdict());
        assertEquals(2, answer.result().rows().size());
        assertEquals("bar", answer.chart().kind());
    }

    @Test
    void an_error_envelope_parses_even_when_it_is_empty() {
        assertEquals(Map.of(), Json.read("{}", Models.ApiErrorBody.class).error());
    }
}
