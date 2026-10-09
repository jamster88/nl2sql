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
                                      "drop_reasons": [], "missing_assumptions": [],
                                      "semantic_issue": null},
                            "plan_cost": 125767.4, "attempts": 1,
                            "trace": [{"node": "generate_sql", "ms": 8123.4,
                                       "model_calls": 1, "detail": "...",
                                       "model": "coder:14b", "rung": "standard",
                                       "route": "attempt 1", "hops": ["small:3b"]}],
                            "retrieval_errors": {}, "node_errors": {}},
                 "error": null,
                 "links": {"self": "/v1/questions/3f2c", "events": "/v1/questions/3f2c/events"}}
                """, Models.Job.class);

        assertEquals("3f2c", job.id());
        assertEquals(Models.JobStatus.SUCCEEDED, job.status());
        assertEquals("sql", job.progress().get(0).label());
        assertEquals("SELECT sum(x) FROM y", job.answer().sql());
        assertEquals("coder:14b", job.answer().trace().get(0).model());
        assertEquals(List.of("small:3b"), job.answer().trace().get(0).hops());
        assertEquals("719279.97", job.answer().result().rows().get(0).get(0));
        assertEquals(List.of("net_sales"), job.answer().chart().y());
        assertEquals("/v1/questions/3f2c/events", job.links().events());
    }

    @Test
    void an_answer_asked_several_ways_parses_with_every_run_recorded() {
        // 7.0's ensemble: the vote, the delivered run, each run's record, and
        // the progress event and the server's settings that say how many.
        Models.Job job = Json.read("""
                {"id": "7a", "status": "succeeded", "question": "top store?",
                 "created_at": "2026-10-08T10:00:00Z",
                 "progress": [{"seq": 3, "step": "generate_sql", "label": "sql", "detail": "",
                               "at": "2026-10-08T10:00:10Z", "candidate": 1},
                              {"seq": 9, "step": "deliver", "label": "answer", "detail": "",
                               "at": "2026-10-08T10:01:00Z", "candidate": null}],
                 "answer": {"answer": "Store 7.", "sql": "SELECT 7",
                            "ensemble": {
                              "agreement": {"admissible": 2, "agreed": 2, "total": 3,
                                            "level": "unanimous", "why": "2 of 2", "set_aside": 1},
                              "chosen": 0, "fused_from": [0, 1], "columns_fused": true,
                              "joined_columns": [{"column": "brand", "from_candidate": 1,
                                                  "key": "sku_id", "table": "dim_product"}],
                              "declined_columns": [{"column": "city", "from_candidate": 1,
                                                    "why": "key repeats in run 1"}],
                              "claims_added": 1, "claims_dropped": 0,
                              "dissent": [{"group": 1, "members": [2], "signature": "1 row",
                                           "differs": "filters region"}],
                              "judged": {"verdicts": [{"group": 0, "accepted": true, "why": "asked as written"},
                                                      {"group": 1, "accepted": false, "why": "filters region"}],
                                         "set_aside": [2], "overruled": false, "instead_of": null,
                                         "model": "m", "error": ""},
                              "candidates": [{"index": 1, "wording": "which store tops?",
                                              "origin": "paraphrase", "wave": 1,
                                              "changed": "word order", "outcome": "answered",
                                              "admissible": true, "reasons": [], "sql": "SELECT 7",
                                              "signature": "7", "attempts": 1, "group": 0,
                                              "duration_ms": 900.5,
                                              "trace": [{"node": "finish", "ms": 1.0}]}],
                              "discarded": [{"index": 4, "text": "lowest store?",
                                             "changed": "direction", "reason": "F3 polarity"}],
                              "parallel_calls": 2}},
                 "links": {"self": "/v1/questions/7a", "events": "/v1/questions/7a/events"}}
                """, Models.Job.class);
        Models.Pipeline pipeline = Json.read("""
                {"supervisor": true, "literals": true, "narrate": true, "audit": true,
                 "schema_retrieval": "vector", "nodes": ["screen"],
                 "ensemble": {"enabled": true, "paraphrases": 3, "max_paraphrases": 10, "waves": 2,
                              "parallel_calls": 2, "judge": true, "fuse_columns": true}}
                """, Models.Pipeline.class);

        Models.Ensemble ensemble = job.answer().ensemble();
        assertEquals(1, job.progress().get(0).candidate());
        assertNull(job.progress().get(1).candidate());
        assertEquals("unanimous", ensemble.agreement().level());
        assertEquals(List.of(0, 1), ensemble.fused_from());
        assertEquals("sku_id", ensemble.joined_columns().get(0).key());
        assertEquals("key repeats in run 1", ensemble.declined_columns().get(0).why());
        assertEquals(List.of(2), ensemble.dissent().get(0).members());
        assertEquals(1, ensemble.agreement().set_aside());
        assertEquals("filters region", ensemble.judged().verdicts().get(1).why());
        assertEquals(List.of(2), ensemble.judged().set_aside());
        assertNull(ensemble.judged().instead_of());
        assertEquals("word order", ensemble.candidates().get(0).changed());
        assertEquals("finish", ensemble.candidates().get(0).trace().get(0).node());
        assertEquals("F3 polarity", ensemble.discarded().get(0).reason());
        assertEquals(2, ensemble.parallel_calls());
        assertEquals(10, pipeline.ensemble().max_paraphrases());
    }

    @Test
    void an_ensemble_record_from_a_terser_server_has_no_nulls_in_it() {
        Models.Ensemble ensemble = Json.read("{}", Models.Ensemble.class);
        assertEquals(List.of(), ensemble.fused_from());
        assertEquals(List.of(), ensemble.joined_columns());
        assertEquals(List.of(), ensemble.declined_columns());
        assertEquals(List.of(), ensemble.dissent());
        assertEquals(List.of(), ensemble.candidates());
        assertEquals(List.of(), ensemble.discarded());
        assertNull(ensemble.chosen());
        Models.EnsembleCandidate candidate = Json.read("{}", Models.EnsembleCandidate.class);
        assertEquals(List.of(), candidate.reasons());
        assertEquals(List.of(), candidate.trace());
        assertEquals("", candidate.wording());
        assertEquals(List.of(), Json.read("{}", Models.EnsembleDissent.class).members());
        assertEquals("", Json.read("{}", Models.DiscardedRewording.class).text());
        assertEquals("", Json.read("{}", Models.EnsembleAgreement.class).level());
        Models.EnsembleJudgement judgement = Json.read("{}", Models.EnsembleJudgement.class);
        assertEquals(List.of(), judgement.verdicts());
        assertEquals(List.of(), judgement.set_aside());
        assertEquals("", judgement.error());
        assertEquals("", Json.read("{}", Models.EnsembleVerdict.class).why());
        // A server older than 7.0 sends neither.
        assertNull(Json.read("{}", Models.Answer.class).ensemble());
    }

    @Test
    void a_reload_says_what_was_read_again_and_nothing_is_a_null() {
        // Mirrored for completeness: this client does not reload.
        Models.Reloaded reloaded = Json.read(
                "{\"reloaded\": [\"literals\"], \"sessions_forgotten\": 2}", Models.Reloaded.class);
        assertEquals(List.of("literals"), reloaded.reloaded());
        assertEquals(2, reloaded.sessions_forgotten());
        assertEquals(List.of(), Json.read("{}", Models.Reloaded.class).reloaded());
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
        assertEquals(Map.of(), answer.node_errors());
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

    @Test
    void neither_record_that_carries_a_secret_prints_it() {
        Models.SignIn signIn = new Models.SignIn("ada", "correct horse");
        Models.Token token = Fakes.token("ada", "Ada Lovelace");

        assertEquals("SignIn[username=ada]", signIn.toString());
        assertFalse(token.toString().contains(token.token()), token.toString());
        assertTrue(token.toString().contains("user=ada"));
        // And what goes over the wire is still the whole of it.
        assertTrue(Json.write(signIn).contains("\"password\":\"correct horse\""));
    }

    @Test
    void a_session_from_an_older_or_terser_service_has_no_nulls_in_it() {
        Models.Token token = Json.read("{\"expires_at\": 5}", Models.Token.class);

        assertEquals("", token.user());
        assertEquals("", token.token());
        assertEquals(List.of(), token.roles());
        assertEquals("", token.kind());
        assertEquals(5, token.expires_at());
    }

    @Test
    void who_is_their_name_and_user_name_once_and_only_once() {
        assertEquals("Ada Lovelace (ada)", Fakes.token("ada", "Ada Lovelace").who());
        assertEquals("ada", Fakes.token("ada", "").who());
        assertEquals("ada", Fakes.token("ada", "ada").who());
    }
}
