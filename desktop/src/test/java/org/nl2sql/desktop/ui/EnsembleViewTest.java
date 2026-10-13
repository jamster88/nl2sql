package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.control.Label;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The ensemble's surfaces (7.0): the badge, the notes, the runs.
 *
 * <p>What these hold is that the window draws what the server said and no
 * more: the sentence about the vote is the server's, not one composed here
 * from the counts, and everything a model wrote is drawn as text.
 */
@ExtendWith(FxToolkit.class)
class EnsembleViewTest {

    private static final Models.Ensemble BASE = Fakes.ensemble();

    /** The sample ensemble, with one part of it replaced. */
    private static Models.Ensemble with(Models.EnsembleAgreement agreement, String line) {
        return new Models.Ensemble(agreement, line, BASE.chosen(), BASE.fused_from(),
                BASE.columns_fused(), BASE.joined_columns(), BASE.declined_columns(),
                BASE.claims_added(), BASE.claims_dropped(), BASE.dissent(), BASE.judged(),
                BASE.candidates(), BASE.discarded(), BASE.parallel_calls());
    }

    private static Models.Ensemble withNotes(List<Models.JoinedColumn> joined,
                                             List<Models.DeclinedColumn> declined,
                                             List<Models.EnsembleDissent> dissent) {
        return new Models.Ensemble(BASE.agreement(), BASE.line(), BASE.chosen(), BASE.fused_from(),
                BASE.columns_fused(), joined, declined, BASE.claims_added(), BASE.claims_dropped(),
                dissent, BASE.judged(), BASE.candidates(), BASE.discarded(), BASE.parallel_calls());
    }

    private static Models.Ensemble withRuns(Integer chosen, List<Integer> fusedFrom,
                                            Models.EnsembleJudgement judged,
                                            List<Models.EnsembleCandidate> candidates,
                                            List<Models.DiscardedRewording> discarded) {
        return new Models.Ensemble(BASE.agreement(), BASE.line(), chosen, fusedFrom,
                BASE.columns_fused(), List.of(), List.of(), 0, 0, List.of(), judged, candidates,
                discarded, BASE.parallel_calls());
    }

    private static Models.EnsembleAgreement level(String level, int agreed, int total, String why) {
        return new Models.EnsembleAgreement(agreed, agreed, total, level, why, 0);
    }

    @Test
    void the_badge_says_the_level_the_count_and_the_agents_own_sentence() {
        FxToolkit.onFx(() -> {
            Region badge = EnsembleView.badge(BASE, new VBox());

            assertEquals(List.of("Most agree · 3 of 4", "3 of 4 runs agreed; 1 answered differently."),
                    Nodes.texts(badge));
            assertTrue(Nodes.oneWithClass(badge, "agreement-badge").getStyleClass()
                    .contains("agreement-majority"));
        });
    }

    @Test
    void there_is_a_word_for_every_level_the_server_can_send() {
        assertEquals(Set.of("unanimous", "majority", "judged", "contested", "single", "none"),
                EnsembleView.LEVEL_LABELS.keySet());
    }

    @Test
    void a_level_this_client_has_no_word_for_is_shown_in_the_servers() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                EnsembleView.badge(with(level("weighted", 2, 5, ""), "x"), new VBox()),
                "weighted · 2 of 5")));
    }

    @Test
    void with_no_line_written_the_badge_says_why_the_vote_went_as_it_did() {
        FxToolkit.onFx(() -> assertEquals(List.of("No answer · 0 of 4", "no run answered"),
                Nodes.texts(EnsembleView.badge(with(level("none", 0, 4, "no run answered"), ""),
                        new VBox()))));
    }

    @Test
    void with_nothing_to_say_beside_it_the_badge_is_alone() {
        FxToolkit.onFx(() -> assertEquals(List.of("One answer · 1 of 1"),
                Nodes.texts(EnsembleView.badge(with(level("single", 1, 1, ""), ""), new VBox()))));
    }

    @Test
    void a_reason_the_judge_wrote_is_drawn_as_text() {
        // A label draws a string; there is no markup for it to become.
        FxToolkit.onFx(() -> {
            String line = "The Judge set aside the answer -- <b>bold</b> &amp; <script>x</script>.";
            assertTrue(Nodes.texts(EnsembleView.badge(with(BASE.agreement(), line), new VBox()))
                    .contains(line));
        });
    }

    @Test
    void the_notes_name_each_answer_that_lost_and_how_its_query_differs() {
        FxToolkit.onFx(() -> assertEquals(
                List.of("•  Run 2 answered differently (Dairy 512004.10): filters on banner_name."),
                Nodes.texts(EnsembleView.notes(BASE, new VBox()))));
    }

    @Test
    void several_runs_are_named_together_and_an_unread_difference_is_left_out() {
        FxToolkit.onFx(() -> {
            Region notes = EnsembleView.notes(withNotes(List.of(), List.of(), List.of(
                    new Models.EnsembleDissent(1, List.of(1, 3), "34.65", ""),
                    new Models.EnsembleDissent(2, List.of(2, 4, 5), "12", ""))), new VBox());

            assertEquals(List.of("•  Runs 1 and 3 answered differently (34.65).",
                    "•  Runs 2, 4 and 5 answered differently (12)."), Nodes.texts(notes));
        });
    }

    @Test
    void a_joined_column_says_which_run_it_is_from_and_a_declined_one_why() {
        FxToolkit.onFx(() -> {
            Region notes = EnsembleView.notes(withNotes(
                    List.of(new Models.JoinedColumn("region", 2, "store_id", "dim_store")),
                    List.of(new Models.DeclinedColumn("units", 1, "two rows matched one key")),
                    List.of()), new VBox());

            assertEquals(List.of(
                    "•  region is from run 2, which agreed, joined on store_id; this answer's "
                            + "query does not return it.",
                    "•  units, which run 1 also returned, was left out: two rows matched one key."),
                    Nodes.texts(notes));
        });
    }

    @Test
    void with_every_run_agreeing_and_nothing_joined_there_are_no_notes() {
        assertNull(EnsembleView.notes(withNotes(List.of(), List.of(), List.of()), new VBox()));
    }

    @Test
    void the_pane_is_titled_by_how_many_ways_the_question_was_asked() {
        assertEquals("The question, asked 4 ways", EnsembleView.title(BASE));
        assertEquals("The question, asked 1 way", EnsembleView.title(withRuns(0, List.of(0), null,
                List.of(BASE.candidates().get(0)), List.of())));
    }

    @Test
    void each_wording_is_listed_with_where_its_run_stands_and_its_sql() {
        FxToolkit.onFx(() -> {
            Region runs = EnsembleView.runs(BASE);
            List<String> texts = Nodes.texts(runs);

            assertTrue(texts.contains("0   What was total net sales for Produce in FY2025?"));
            assertTrue(texts.contains(
                    "As asked · answered · answer A · delivered · says Produce 719279.97 · 31.3s"));
            assertTrue(texts.contains("Reworded: the order of the clauses · answered · answer A · "
                    + "agreed · says Produce 719279.97 · 31.3s"));
            assertTrue(texts.contains(
                    "Reworded: shortened · answered · answer B · says Dairy 512004.10 · 31.3s"));
            assertTrue(texts.contains(
                    "Reworded · answered · answer A · agreed · says Produce 719279.97 · 31.3s"));
            assertTrue(texts.contains("SELECT 2"));
            // The delivered run's wording is the one marked.
            List<Node> wordings = Nodes.withClass(runs, "candidate-wording");
            assertTrue(wordings.get(0).getStyleClass().contains("chosen"));
            assertFalse(wordings.get(1).getStyleClass().contains("chosen"));
        });
    }

    @Test
    void a_run_that_did_not_count_says_why_and_shows_no_sql_it_never_wrote() {
        FxToolkit.onFx(() -> {
            Models.EnsembleCandidate gaveUp = new Models.EnsembleCandidate(1, "Produce, FY2025?",
                    "paraphrase", 2, "", "gave_up", false,
                    List.of("E1 answered: gave up after 7 attempts"), "", "", 7, null, 31250,
                    List.of());
            Region runs = EnsembleView.runs(withRuns(null, List.of(), null, List.of(gaveUp), List.of()));

            assertEquals(List.of("1   Produce, FY2025?", "Reworded · gave up · wave 2 · 7 attempts · 31.3s",
                    "E1 answered: gave up after 7 attempts"), Nodes.texts(runs));
            assertEquals(0, Nodes.withClass(runs, "sql").size());
            // No run was delivered, so none is marked as the one that was.
            assertFalse(Nodes.oneWithClass(runs, "candidate-wording").getStyleClass().contains("chosen"));
        });
    }

    @Test
    void an_outcome_this_client_has_no_word_for_is_shown_in_the_servers() {
        Models.EnsembleCandidate odd = new Models.EnsembleCandidate(9, "q", "paraphrase", 1, "",
                "timed_out", false, List.of(), "", "", 1, null, 1000, List.of());
        assertEquals("Reworded · timed_out · 1.0s", EnsembleView.standing(odd, BASE));
    }

    @Test
    void the_judges_verdicts_are_given_by_the_answers_letter_with_its_reason() {
        FxToolkit.onFx(() -> {
            Models.EnsembleJudgement judged = new Models.EnsembleJudgement(
                    List.of(new Models.EnsembleVerdict(0, false, "it filters on the example channels"),
                            new Models.EnsembleVerdict(1, true, "")),
                    List.of(0, 1, 3), true, 0, "", "");
            Region runs = EnsembleView.runs(withRuns(2, List.of(2), judged, BASE.candidates(), List.of()));
            List<String> texts = Nodes.texts(runs);

            assertTrue(texts.contains("The Judge"));
            assertTrue(texts.contains("Answer A: set aside — it filters on the example channels"));
            assertTrue(texts.contains("Answer B: accepted"));
            assertTrue(texts.contains("As asked · answered · answer A · set aside by the Judge · "
                    + "says Produce 719279.97 · 31.3s"));
            assertEquals("C", EnsembleView.letter(2));
        });
    }

    @Test
    void the_model_that_judged_is_named_when_the_server_says_which() {
        FxToolkit.onFx(() -> assertTrue(
                Nodes.texts(EnsembleView.runs(BASE)).contains("The Judge · judge-model")));
    }

    @Test
    void a_judge_that_could_not_be_asked_is_said_in_words() {
        FxToolkit.onFx(() -> {
            Models.EnsembleJudgement failed = new Models.EnsembleJudgement(List.of(), List.of(),
                    false, null, "", "TimeoutError");
            assertTrue(Nodes.texts(EnsembleView.runs(
                    withRuns(0, List.of(0), failed, BASE.candidates(), List.of())))
                    .contains("It could not be asked, so the runs voted alone: TimeoutError"));
        });
    }

    @Test
    void a_judge_that_was_not_asked_is_left_out() {
        FxToolkit.onFx(() -> assertFalse(Nodes.says(EnsembleView.runs(
                withRuns(0, List.of(0), null, BASE.candidates(), List.of())), "The Judge")));
    }

    @Test
    void the_rewordings_that_were_not_run_are_listed_with_the_check_each_failed() {
        FxToolkit.onFx(() -> {
            Region runs = EnsembleView.runs(withRuns(0, List.of(0), null, BASE.candidates(), List.of(
                    new Models.DiscardedRewording(4, "What did Dairy sell?", "the department", "F2 literals"),
                    new Models.DiscardedRewording(5, "Best department?", "", "F5 distinct"))));
            List<String> texts = Nodes.texts(runs);

            assertTrue(texts.contains("Rewordings that were not run"));
            assertTrue(texts.contains("What did Dairy sell? · changed: the department · F2 literals"));
            assertTrue(texts.contains("Best department? · F5 distinct"));
        });
    }

    @Test
    void with_none_discarded_nothing_is_said_of_them() {
        FxToolkit.onFx(() -> assertFalse(Nodes.says(EnsembleView.runs(
                withRuns(0, List.of(0), null, BASE.candidates(), List.of())), "not run")));
    }

    @Test
    void a_rewording_is_drawn_as_text() {
        FxToolkit.onFx(() -> {
            Models.EnsembleCandidate hostile = new Models.EnsembleCandidate(1,
                    "<script>alert(1)</script>", "paraphrase", 1, "<b>x</b>", "answered", true,
                    List.of(), "", "", 1, 0, 1000, List.of());
            Region runs = EnsembleView.runs(withRuns(0, List.of(0), null, List.of(hostile), List.of()));

            assertTrue(Nodes.texts(runs).contains("1   <script>alert(1)</script>"));
            assertTrue(((Label) Nodes.withClass(runs, "muted").get(0)).getText()
                    .startsWith("Reworded: <b>x</b>"));
        });
    }
}
