package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.control.TitledPane;
import javafx.scene.input.MouseEvent;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class AnswerViewTest {

    private static Models.Job with(Models.Answer answer, Models.JobStatus status, String error) {
        Models.Job base = Fakes.answered();
        return new Models.Job(base.id(), status, base.question(), base.metadata(),
                base.created_at(), base.started_at(), base.finished_at(), base.duration_ms(),
                base.progress(), answer, error, base.links());
    }

    private static Models.Answer answerWith(String verdict, String clarification,
                                            List<Models.Claim> claims,
                                            Models.AuditReport audit) {
        Models.Answer base = Fakes.answer();
        return new Models.Answer(base.answer(), base.narrative(), base.sql(), verdict,
                base.intent(), clarification, base.tables(), base.literals(), base.result(),
                base.chart(), claims, audit, base.plan_cost(), base.attempts(), base.trace(),
                Map.of());
    }

    private static List<String> summaries(Node root) {
        return Nodes.withClass(root, "disclosure").stream()
                .map(node -> ((TitledPane) node).getText())
                .toList();
    }

    @Test
    void the_question_the_sentence_the_picture_and_the_rows_in_that_order() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    Fakes.meta(true).pipeline());

            assertTrue(Nodes.says(view.node(), "What was total net sales for Produce"));
            assertTrue(Nodes.says(view.node(), "Produce net sales were $719,279.97"));
            assertTrue(Nodes.says(view.node(), "61.4s"));
            assertEquals(1, Nodes.withClass(view.node(), "answer-chart").size());
            assertEquals(1, Nodes.withClass(view.node(), "result-table").size());
            assertTrue(Nodes.says(view.node(), "2 rows"));
        });
    }

    @Test
    void the_work_is_folded_away_rather_than_hidden() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    null);

            assertEquals(List.of("SQL", "Matched values (1)", "How long each step took (2 steps)"),
                    summaries(view.node()));
            assertTrue(Nodes.withClass(view.node(), "disclosure").stream()
                    .noneMatch(node -> ((TitledPane) node).isExpanded()));
        });
    }

    @Test
    void the_sql_panel_carries_the_tables_the_cost_and_the_retries() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer retried = new Models.Answer(base.answer(), base.narrative(), base.sql(),
                    base.verdict(), base.intent(), null, base.tables(), base.literals(),
                    base.result(), base.chart(), base.claims(), base.audit(), base.plan_cost(), 3,
                    base.trace(), Map.of());
            AnswerView view = new AnswerView(with(retried, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            TitledPane sql = (TitledPane) Nodes.withClass(view.node(), "disclosure").get(0);
            assertEquals("SQL (3 attempts)", sql.getText());
            assertTrue(Nodes.says(sql.getContent(), "Tables: dim_product, fact_pos_retail_sales"));
            assertTrue(Nodes.says(sql.getContent(), "Planner cost estimate: 125,767.4"));
        });
    }

    @Test
    void a_matched_value_says_what_it_was_matched_to_and_how_well() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    null);

            TitledPane literals = (TitledPane) Nodes.withClass(view.node(), "disclosure").get(1);
            assertTrue(Nodes.says(literals.getContent(),
                    "produse → Produce  in dim_product.department (0.81)"));
        });
    }

    @Test
    void the_trace_is_a_bar_per_node_rather_than_a_chart() {
        // It is a list that happens to be sorted, and the chart machinery
        // would put an axis and a legend around eight numbers nobody
        // compares across questions.
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    null);

            TitledPane trace = (TitledPane) Nodes.withClass(view.node(), "disclosure").get(2);
            assertTrue(Nodes.says(trace.getContent(), "8.12s"));
            assertTrue(Nodes.says(trace.getContent(), "1 model call"));
            assertFalse(Nodes.says(trace.getContent(), "0 model calls"));
            assertEquals(2, Nodes.withClass(trace.getContent(), "trace-bar").size());
        });
    }

    @Test
    void pointing_at_a_sentence_lights_up_the_cells_it_was_read_from() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    null);
            javafx.stage.Stage stage = FxToolkit.render(new javafx.scene.layout.VBox(view.node()));

            Node claim = Nodes.oneWithClass(view.node(), "claim");
            claim.fireEvent(new MouseEvent(MouseEvent.MOUSE_ENTERED, 0, 0, 0, 0,
                    javafx.scene.input.MouseButton.NONE, 0, false, false, false, false, false,
                    false, false, false, false, false, null));
            view.node().applyCss();
            view.node().layout();

            assertEquals(1, Nodes.withClass(view.node(), "table-cell").stream()
                    .filter(cell -> cell.getPseudoClassStates().contains(Styles.HIGHLIGHTED))
                    .count());

            claim.fireEvent(new MouseEvent(MouseEvent.MOUSE_EXITED, 0, 0, 0, 0,
                    javafx.scene.input.MouseButton.NONE, 0, false, false, false, false, false,
                    false, false, false, false, false, null));
            view.node().applyCss();
            view.node().layout();
            assertEquals(0, Nodes.withClass(view.node(), "table-cell").stream()
                    .filter(cell -> cell.getPseudoClassStates().contains(Styles.HIGHLIGHTED))
                    .count());
            stage.close();
        });
    }

    @Test
    void a_claim_pointing_at_nothing_usable_names_no_cells() {
        assertEquals(Set.of(), AnswerView.keysOf(
                new Models.Claim("x", null, List.of(List.of(0)), null)));
        assertEquals(Set.of(), AnswerView.keysOf(
                new Models.Claim("x", null, List.of(Arrays.asList("not a row", "net_sales")), null)));
        assertEquals(Set.of("2:net_sales"), AnswerView.keysOf(
                new Models.Claim("x", null, List.of(List.of(2, "net_sales")), null)));
    }

    @Test
    void with_no_claims_the_narrative_is_one_block() {
        // What happens on a server running without the narrator, and for any
        // answer the audit dropped every claim from.
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(
                    with(answerWith("proceed", null, List.of(), Fakes.answer().audit()),
                            Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertEquals(0, Nodes.withClass(view.node(), "claim").size());
            assertTrue(Nodes.says(view.node(), "Produce net sales were $719,279.97"));
        });
    }

    @Test
    void with_no_narrative_either_the_prose_above_the_markdown_table_is_used() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer bare = new Models.Answer(base.answer(), "", base.sql(), "proceed",
                    base.intent(), null, base.tables(), base.literals(), base.result(),
                    base.chart(), List.of(), base.audit(), base.plan_cost(), 1, base.trace(),
                    Map.of());

            AnswerView view = new AnswerView(with(bare, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "Produce net sales were $719,279.97 in FY2025."));
            assertFalse(Nodes.says(view.node(), "| a |"));
        });
    }

    @Test
    void a_claim_with_a_formula_shows_it() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(
                    with(answerWith("proceed", null,
                                    List.of(new Models.Claim("Up 12%.", 12.0, List.of(), "b - a")),
                                    Fakes.answer().audit()),
                            Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "Up 12%. b - a"));
        });
    }

    @Test
    void an_audit_that_passed_says_the_numbers_were_checked() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                        Fakes.meta(true).pipeline()).node(),
                "checked against the rows above")));
    }

    @Test
    void an_audit_that_found_something_lists_what() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(
                    with(answerWith("proceed", null, Fakes.answer().claims(),
                                    new Models.AuditReport(false, List.of("the total"),
                                            List.of("row 3 was dropped"), List.of(),
                                            "the question asked for a median")),
                            Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "The audit found something."));
            assertTrue(Nodes.says(view.node(), "Unsupported: the total"));
            assertTrue(Nodes.says(view.node(), "row 3 was dropped"));
            assertTrue(Nodes.says(view.node(), "the question asked for a median"));
        });
    }

    @Test
    void a_server_running_without_the_auditor_shows_no_badge_at_all() {
        FxToolkit.onFx(() -> {
            Models.Pipeline pipeline = Fakes.meta(true).pipeline();
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    new Models.Pipeline(pipeline.supervisor(), pipeline.literals(),
                            pipeline.narrate(), false, pipeline.schema_retrieval(),
                            pipeline.nodes()));

            assertFalse(Nodes.says(view.node(), "checked against the rows above"));
        });
    }

    @Test
    void a_refusal_is_a_success_and_shows_no_empty_table() {
        // The agent decided the question was out of scope and said so;
        // `status` is still succeeded and no query ran.
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer refused = new Models.Answer(base.answer(), "", base.sql(),
                    "out_of_domain", base.intent(), null, base.tables(), List.of(), null, null,
                    List.of(), base.audit(), null, 1, List.of(), Map.of());

            AnswerView view = new AnswerView(with(refused, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "outside what this database covers"));
            assertEquals(0, Nodes.withClass(view.node(), "result-table").size());
            assertNull(view.table());
        });
    }

    @Test
    void an_ambiguous_question_comes_back_as_a_question() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer ambiguous = new Models.Answer(base.answer(), "", base.sql(), "ambiguous",
                    base.intent(), "Which fiscal year did you mean?", base.tables(), List.of(),
                    null, null, List.of(), base.audit(), null, 1, List.of(), Map.of());

            AnswerView view = new AnswerView(with(ambiguous, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "more than one answer"));
            assertTrue(Nodes.says(view.node(), "Which fiscal year did you mean?"));
        });
    }

    @Test
    void a_verdict_this_client_has_no_wording_for_still_says_the_agent_did_not_answer() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer odd = new Models.Answer(base.answer(), "", base.sql(), "deferred",
                    base.intent(), null, base.tables(), List.of(), null, null, List.of(),
                    base.audit(), null, 1, List.of(), Map.of());

            assertTrue(Nodes.says(new AnswerView(with(odd, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null).node(), "did not answer"));
        });
    }

    @Test
    void a_failed_run_still_shows_the_sql_it_tried() {
        // Which is the useful part.
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(
                    with(Fakes.answer(), Models.JobStatus.FAILED, "the plan cost too much"),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "The run failed."));
            assertTrue(Nodes.says(view.node(), "the plan cost too much"));
            assertTrue(Nodes.says(view.node(), "SELECT department"));
        });
    }

    @Test
    void a_failure_the_server_did_not_explain_says_that() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                new AnswerView(with(null, Models.JobStatus.FAILED, null),
                        Stores.of(new Stores.Recording()), null).node(),
                "did not say why")));
    }

    // --- the verdict -------------------------------------------------------

    @Test
    void voting_records_it_and_sends_it() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            FeedbackStore store = Stores.of(sender);
            AnswerView view = new AnswerView(Fakes.answered(), store, null);

            view.feedback().yes().fire();
            view.refreshFeedback();

            assertEquals(List.of("job-1:yes:"), sender.sent);
            assertTrue(view.feedback().yes().getPseudoClassStates().contains(Styles.CHOSEN));
        });
    }

    @Test
    void clicking_the_verdict_already_recorded_withdraws_it() {
        // A user who misclicks "No" on a good answer should not have to hunt
        // for an undo.
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            FeedbackStore store = Stores.of(sender);
            AnswerView view = new AnswerView(Fakes.answered(), store, null);

            view.feedback().no().fire();
            view.feedback().no().fire();

            assertEquals(List.of("job-1"), sender.withdrawn);
            assertTrue(store.get("job-1").isEmpty());
        });
    }

    @Test
    void changing_your_mind_replaces_the_verdict_rather_than_withdrawing_it() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(sender), null);

            view.feedback().yes().fire();
            view.feedback().no().fire();

            assertEquals(List.of("job-1:yes:", "job-1:no:"), sender.sent);
            assertEquals(List.of(), sender.withdrawn);
        });
    }

    @Test
    void a_comment_amends_the_verdict_that_is_recorded() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(sender), null);

            view.feedback().no().fire();
            view.refreshFeedback();
            view.feedback().comment().setText("off by one");
            view.feedback().send().fire();

            assertEquals(List.of("job-1:no:", "job-1:no:off by one"), sender.sent);
        });
    }

    @Test
    void a_comment_with_no_verdict_behind_it_goes_nowhere() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(sender), null);

            view.feedback().comment().setText("unprompted");
            view.feedback().send().fire();

            assertEquals(List.of(), sender.sent);
        });
    }

    @Test
    void a_verdict_that_did_not_send_can_be_sent_again_from_here() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            sender.failure = new org.nl2sql.desktop.api.ApiException(0, "unreachable", "no route");
            FeedbackStore store = Stores.of(sender);
            AnswerView view = new AnswerView(Fakes.answered(), store, null);

            view.feedback().yes().fire();
            view.refreshFeedback();
            assertTrue(view.feedback().retry().isVisible());

            sender.failure = null;
            view.feedback().retry().fire();
            view.refreshFeedback();

            assertEquals(2, sender.sent.size());
            assertFalse(view.feedback().retry().isVisible());
        });
    }

    @Test
    void on_a_server_that_stages_nothing_there_is_nowhere_for_a_comment_to_go() {
        FxToolkit.onFx(() -> {
            Stores.Recording sender = new Stores.Recording();
            sender.on = false;
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(sender), null);

            view.feedback().yes().fire();
            view.refreshFeedback();

            assertFalse(Nodes.oneWithClass(view.node(), "feedback-comment").isVisible());
            assertEquals(List.of(), sender.sent);
        });
    }


    @Test
    void a_job_that_succeeded_with_nothing_on_it_is_shown_as_a_failure() {
        // There is no answer to draw, and an empty one would report a result
        // the agent never produced.
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                new AnswerView(with(null, Models.JobStatus.SUCCEEDED, "gone"),
                        Stores.of(new Stores.Recording()), null).node(),
                "The run failed.")));
    }

    @Test
    void a_failure_whose_reason_is_an_empty_string_says_the_server_did_not_say() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                new AnswerView(with(null, Models.JobStatus.FAILED, ""),
                        Stores.of(new Stores.Recording()), null).node(),
                "did not say why")));
    }

    @Test
    void a_failed_run_with_no_sql_to_show_shows_none() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer noSql = new Models.Answer(base.answer(), base.narrative(), "",
                    base.verdict(), base.intent(), null, List.of(), List.of(), base.result(),
                    base.chart(), base.claims(), base.audit(), null, 1, List.of(), Map.of());

            AnswerView view = new AnswerView(with(noSql, Models.JobStatus.FAILED, "no plan"),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "The run failed."));
            assertEquals(List.of(), summaries(view.node()));
        });
    }

    @Test
    void an_answer_with_no_sql_no_tables_and_no_cost_folds_nothing_away() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer bare = new Models.Answer(base.answer(), base.narrative(), "",
                    "proceed", base.intent(), null, List.of(), List.of(), base.result(), null,
                    List.of(), base.audit(), null, 1, List.of(), Map.of());

            AnswerView view = new AnswerView(with(bare, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertEquals(List.of(), summaries(view.node()));
            // The rows are still there: they are the answer.
            assertEquals(1, Nodes.withClass(view.node(), "result-table").size());
        });
    }

    @Test
    void a_sql_panel_for_an_answer_with_no_tables_and_no_cost_shows_neither() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer thin = new Models.Answer(base.answer(), base.narrative(), base.sql(),
                    "proceed", base.intent(), null, List.of(), List.of(), base.result(),
                    base.chart(), base.claims(), base.audit(), null, 1, List.of(), Map.of());

            AnswerView view = new AnswerView(with(thin, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            TitledPane sql = (TitledPane) Nodes.withClass(view.node(), "disclosure").get(0);
            assertFalse(Nodes.says(sql.getContent(), "Tables:"));
            assertFalse(Nodes.says(sql.getContent(), "Planner cost"));
        });
    }

    @Test
    void a_result_the_pipeline_asked_for_no_chart_of_is_drawn_as_rows_alone() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer tabular = new Models.Answer(base.answer(), base.narrative(), base.sql(),
                    "proceed", base.intent(), null, base.tables(), base.literals(), base.result(),
                    new Models.ChartSpec("table", null, List.of("net_sales"), null), base.claims(),
                    base.audit(), base.plan_cost(), 1, base.trace(), Map.of());

            AnswerView view = new AnswerView(with(tabular, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertEquals(0, Nodes.withClass(view.node(), "answer-chart").size());
            assertEquals(1, Nodes.withClass(view.node(), "result-table").size());
        });
    }

    @Test
    void a_job_with_no_duration_shows_no_timing() {
        FxToolkit.onFx(() -> {
            Models.Job base = Fakes.answered();
            Models.Job untimed = new Models.Job(base.id(), base.status(), base.question(),
                    base.metadata(), base.created_at(), base.started_at(), base.finished_at(),
                    null, base.progress(), base.answer(), null, base.links());

            assertEquals(0, Nodes.withClass(
                    new AnswerView(untimed, Stores.of(new Stores.Recording()), null).node(),
                    "answer-timing").size());
        });
    }

    @Test
    void pointing_at_a_sentence_when_there_are_no_rows_does_nothing() {
        // A narrator that produced claims for an answer with no result: the
        // cells they name are not on screen, and there is nothing to light.
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer rowless = new Models.Answer(base.answer(), base.narrative(), base.sql(),
                    "proceed", base.intent(), null, base.tables(), List.of(), null, null,
                    base.claims(), base.audit(), null, 1, List.of(), Map.of());
            AnswerView view = new AnswerView(with(rowless, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            Nodes.oneWithClass(view.node(), "claim").fireEvent(new MouseEvent(
                    MouseEvent.MOUSE_ENTERED, 0, 0, 0, 0,
                    javafx.scene.input.MouseButton.NONE, 0, false, false, false, false, false,
                    false, false, false, false, false, null));

            assertNull(view.table());
        });
    }

    @Test
    void an_audit_that_passed_but_dropped_something_still_lists_it() {
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(
                    with(answerWith("proceed", null, Fakes.answer().claims(),
                                    new Models.AuditReport(true, List.of(),
                                            List.of("row 3 was dropped"), List.of(), "")),
                            Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            assertTrue(Nodes.says(view.node(), "The audit found something."));
            assertFalse(Nodes.says(view.node(), "checked against the rows above"));
        });
    }

    @Test
    void a_node_that_made_several_model_calls_says_calls() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer busy = new Models.Answer(base.answer(), base.narrative(), base.sql(),
                    "proceed", base.intent(), null, base.tables(), base.literals(), base.result(),
                    base.chart(), base.claims(), base.audit(), base.plan_cost(), 1,
                    List.of(new Models.TraceEntry("generate_sql", 100, 3, "")), Map.of());

            AnswerView view = new AnswerView(with(busy, Models.JobStatus.SUCCEEDED, null),
                    Stores.of(new Stores.Recording()), null);

            TitledPane trace = (TitledPane) Nodes.withClass(view.node(), "disclosure")
                    .get(summaries(view.node()).indexOf("How long each step took (1 steps)"));
            assertTrue(Nodes.says(trace.getContent(), "3 model calls"));
        });
    }

    private static final String LONG_CLAIM =
            "Produce net sales were $719,279.97 in fiscal year 2025, which is an increase of "
                    + "eleven per cent over the previous year across every banner in the group.";

    /** The answer, laid out at one width. Heights of the three prose blocks. */
    private double[] prose(double width) {
        Models.Job job = with(answerWith("proceed", null,
                        List.of(new Models.Claim(LONG_CLAIM, 719279.97,
                                List.of(List.of(0, "net_sales")), null)),
                        new Models.AuditReport(false, List.of(LONG_CLAIM), List.of(),
                                List.of(), null)),
                Models.JobStatus.SUCCEEDED, null);
        Models.Job wordy = new Models.Job(job.id(), job.status(),
                "What were our total net sales for the Produce department in fiscal year 2025, "
                        + "broken down by banner and by state?",
                job.metadata(), job.created_at(), job.started_at(), job.finished_at(),
                job.duration_ms(), job.progress(), job.answer(), null, job.links());
        AnswerView view = new AnswerView(wordy, Stores.of(new Stores.Recording()), null);
        javafx.stage.Stage stage =
                FxToolkit.render(new javafx.scene.layout.VBox(view.node()), width, 900);

        // A few pixels of slack for the space each sentence ends with, which
        // separates it from the next and is allowed to hang past the margin.
        // The defect this guards against overran by five hundred.
        double flow = Nodes.widthOf(view.node(), "answer-headline");
        for (Node sentence : Nodes.withClass(view.node(), "claim")) {
            assertTrue(Nodes.width(sentence) <= flow + 8,
                    "a sentence is " + Nodes.width(sentence) + " wide in " + flow);
        }
        double[] heights = {
                Nodes.heightOf(view.node(), "answer-question"),
                Nodes.heightOf(view.node(), "answer-headline"),
                Nodes.heightOf(view.node(), "audit-failed"),
        };
        stage.close();
        return heights;
    }

    @Test
    void the_question_the_answer_and_the_audit_all_reflow_as_the_window_narrows() {
        // The defect this pins: a TextFlow breaks the Text nodes it is given
        // across lines and lays anything else out at its preferred width as
        // one unbreakable box. With a Label per claim the answer -- the most
        // important thing in the window -- was drawn nearly a thousand pixels
        // wide inside a four-hundred-pixel column and simply cut off. The
        // question and the audit had the second half of the same problem:
        // wrapText on its own leaves a label asking for the width its text
        // wants, and being given it.
        FxToolkit.onFx(() -> {
            double[] wide = prose(900);
            double[] narrow = prose(480);

            assertTrue(narrow[0] > wide[0], "the question did not reflow");
            assertTrue(narrow[1] > wide[1], "the answer did not reflow");
            assertTrue(narrow[2] > wide[2], "the audit did not reflow");
        });
    }

    @Test
    void a_refusal_and_a_failure_reflow_too() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer refused = new Models.Answer(base.answer(), "", base.sql(),
                    "out_of_domain", base.intent(), LONG_CLAIM, base.tables(), List.of(), null,
                    null, List.of(), base.audit(), null, 1, List.of(), Map.of());

            assertTrue(noticeHeight(refused, null, 380) > noticeHeight(refused, null, 900));
            assertTrue(noticeHeight(null, LONG_CLAIM, 380) > noticeHeight(null, LONG_CLAIM, 900));
        });
    }

    private double noticeHeight(Models.Answer answer, String error, double width) {
        AnswerView view = new AnswerView(
                with(answer, error == null ? Models.JobStatus.SUCCEEDED : Models.JobStatus.FAILED,
                        error),
                Stores.of(new Stores.Recording()), null);
        javafx.stage.Stage stage =
                FxToolkit.render(new javafx.scene.layout.VBox(view.node()), width, 700);
        double height = Nodes.oneWithClass(view.node(),
                error == null ? "notice-verdict" : "notice-error").getLayoutBounds().getHeight();
        stage.close();
        return height;
    }

    @Test
    void nothing_in_the_trace_sets_a_floor_under_the_window() {
        // A node name is allowed to be shortened; a row that cannot be made
        // to fit is a window that cannot be made narrow.
        FxToolkit.onFx(() -> {
            AnswerView view = new AnswerView(Fakes.answered(), Stores.of(new Stores.Recording()),
                    null);
            TitledPane trace = (TitledPane) Nodes.withClass(view.node(), "disclosure").get(2);
            trace.setExpanded(true);
            javafx.stage.Stage stage =
                    FxToolkit.render(new javafx.scene.layout.VBox(view.node()), 420, 700);

            for (Node name : Nodes.withClass(trace.getContent(), "trace-node")) {
                double floor = ((javafx.scene.layout.Region) name).minWidth(-1);
                assertTrue(floor < 100, "a trace row will not shrink: " + floor);
            }
            stage.close();
        });
    }
}
