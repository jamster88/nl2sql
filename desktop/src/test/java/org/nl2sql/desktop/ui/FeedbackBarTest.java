package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackRecord;
import org.nl2sql.desktop.feedback.SyncState;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class FeedbackBarTest {

    private static FeedbackRecord record(Models.Verdict verdict, SyncState sync, String error) {
        return new FeedbackRecord("job-1", "how many stores", verdict, "2026-09-25T10:00:00Z",
                sync, error);
    }

    @Test
    void with_no_verdict_it_asks_for_one() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            assertTrue(Nodes.says(bar.node(), "Was this answer right?"));
            assertFalse(bar.note().isVisible());
            assertFalse(bar.retry().isVisible());
        });
    }

    @Test
    void either_button_reports_its_verdict() {
        FxToolkit.onFx(() -> {
            List<Models.Verdict> votes = new ArrayList<>();
            FeedbackBar bar = new FeedbackBar();
            bar.setOnVote(votes::add);

            bar.yes().fire();
            bar.no().fire();

            assertEquals(List.of(Models.Verdict.YES, Models.Verdict.NO), votes);
        });
    }

    @Test
    void the_recorded_verdict_is_the_one_marked() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            assertTrue(bar.no().getPseudoClassStates().contains(Styles.CHOSEN));
            assertFalse(bar.yes().getPseudoClassStates().contains(Styles.CHOSEN));
            assertTrue(Nodes.says(bar.node(), "Thanks"));
            assertTrue(Nodes.says(bar.node(), "sent for review"));
        });
    }

    @Test
    void a_verdict_that_did_not_reach_the_server_offers_to_try_again() {
        FxToolkit.onFx(() -> {
            int[] retries = {0};
            FeedbackBar bar = new FeedbackBar();
            bar.setOnRetry(() -> retries[0]++);

            bar.show(record(Models.Verdict.NO, SyncState.FAILED, "cannot reach the API"));

            assertTrue(bar.retry().isVisible());
            assertTrue(Nodes.says(bar.node(), "not sent"));
            assertTrue(Nodes.says(bar.node(), "cannot reach the API"));
            bar.retry().fire();
            assertEquals(1, retries[0]);
        });
    }

    @Test
    void a_failure_with_nothing_to_say_shows_no_empty_line() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            bar.show(record(Models.Verdict.NO, SyncState.FAILED, ""));

            assertTrue(bar.retry().isVisible());
            assertFalse(Nodes.oneWithClass(bar.node(), "feedback-error").isVisible());
        });
    }

    @Test
    void the_comment_box_only_appears_once_there_is_a_verdict_to_explain() {
        // Asking for prose up front turns a one-click courtesy into a form.
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();
            assertFalse(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());

            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            assertTrue(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());
            assertTrue(Nodes.says(bar.node(), "What was wrong?"));
        });
    }

    @Test
    void a_yes_is_asked_a_different_question_from_a_no() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            bar.show(record(Models.Verdict.YES, SyncState.SAVED, ""));

            assertTrue(Nodes.says(bar.node(), "Anything worth noting?"));
        });
    }

    @Test
    void a_comment_is_sent_once_and_then_thanked_for() {
        FxToolkit.onFx(() -> {
            List<String> comments = new ArrayList<>();
            FeedbackBar bar = new FeedbackBar();
            bar.setOnComment(comments::add);
            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            bar.comment().setText("  the fiscal month is off by one  ");
            assertFalse(bar.send().isDisable());
            bar.send().fire();

            assertEquals(List.of("the fiscal month is off by one"), comments);
            assertFalse(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());
            assertTrue(Nodes.says(bar.node(), "Comment sent"));
        });
    }

    @Test
    void pressing_return_in_the_box_sends_it_too() {
        FxToolkit.onFx(() -> {
            List<String> comments = new ArrayList<>();
            FeedbackBar bar = new FeedbackBar();
            bar.setOnComment(comments::add);
            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            bar.comment().setText("off by one");
            bar.comment().fireEvent(new javafx.event.ActionEvent());

            assertEquals(List.of("off by one"), comments);
        });
    }

    @Test
    void an_empty_comment_is_not_sent() {
        FxToolkit.onFx(() -> {
            List<String> comments = new ArrayList<>();
            FeedbackBar bar = new FeedbackBar();
            bar.setOnComment(comments::add);
            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            assertTrue(bar.send().isDisable());
            bar.comment().setText("   ");
            assertTrue(bar.send().isDisable());
            bar.send().fire();

            assertEquals(List.of(), comments);
        });
    }

    @Test
    void a_new_verdict_opens_the_box_again_with_nothing_in_it() {
        // A new verdict is a new opinion, not an amendment to the last note.
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();
            bar.setOnComment(comment -> {
            });
            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));
            bar.comment().setText("off by one");
            bar.send().fire();

            bar.show(record(Models.Verdict.YES, SyncState.SAVED, ""));

            assertEquals("", bar.comment().getText());
            assertTrue(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());
        });
    }

    @Test
    void a_server_that_stages_nothing_is_not_offered_a_comment_box() {
        // A comment with nowhere to go is a lie to the user.
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();
            bar.setCommentable(false);

            bar.show(record(Models.Verdict.NO, SyncState.LOCAL, ""));

            assertFalse(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());
            assertTrue(Nodes.says(bar.node(), "click again to withdraw"));
        });
    }

    @Test
    void the_time_is_shown_in_this_machine_s_own_words_when_it_can_be_read() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            bar.show(record(Models.Verdict.YES, SyncState.SAVED, ""));
            assertTrue(bar.note().getText().contains("·"));

            // A timestamp from a file a previous version wrote. "1970" beside
            // a verdict given a minute ago is worse than no time at all.
            bar.show(new FeedbackRecord("job-1", "q", Models.Verdict.NO, "not a time",
                    SyncState.SAVED, ""));
            assertEquals("sent for review", bar.note().getText());
        });
    }


    @Test
    void a_bar_nobody_wired_up_still_works() {
        FxToolkit.onFx(() -> {
            FeedbackBar bar = new FeedbackBar();

            bar.yes().fire();
            bar.show(record(Models.Verdict.YES, SyncState.FAILED, "no route"));
            bar.retry().fire();
            bar.comment().setText("something");
            bar.send().fire();

            assertTrue(Nodes.says(bar.node(), "Comment sent"));
        });
    }

    @Test
    void return_in_an_empty_box_sends_nothing() {
        FxToolkit.onFx(() -> {
            List<String> comments = new ArrayList<>();
            FeedbackBar bar = new FeedbackBar();
            bar.setOnComment(comments::add);
            bar.show(record(Models.Verdict.NO, SyncState.SAVED, ""));

            bar.comment().fireEvent(new javafx.event.ActionEvent());

            assertEquals(List.of(), comments);
            assertTrue(Nodes.oneWithClass(bar.node(), "feedback-comment").isVisible());
        });
    }
}
