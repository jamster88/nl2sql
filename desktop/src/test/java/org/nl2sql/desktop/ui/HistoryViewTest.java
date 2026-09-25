package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class HistoryViewTest {

    private static Models.Job answered(String id, String question) {
        Models.Job base = Fakes.answered();
        return new Models.Job(id, base.status(), question, base.metadata(), base.created_at(),
                base.started_at(), base.finished_at(), base.duration_ms(), base.progress(),
                base.answer(), null, base.links());
    }

    @Test
    void the_newest_answer_is_at_the_top_and_the_list_is_bounded() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 3);

            for (int index = 0; index < 5; index++) {
                history.remember(answered("job-" + index, "question " + index));
            }

            assertEquals(3, history.list().getItems().size());
            assertEquals("job-4", history.list().getItems().get(0).id());
        });
    }

    @Test
    void asking_the_same_question_again_replaces_its_row() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);

            history.remember(answered("job-1", "first wording"));
            history.remember(answered("job-2", "another"));
            history.remember(answered("job-1", "second wording"));

            assertEquals(2, history.list().getItems().size());
            assertEquals("second wording", history.list().getItems().get(0).question());
        });
    }

    @Test
    void picking_one_reports_it() {
        FxToolkit.onFx(() -> {
            List<String> picked = new ArrayList<>();
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            history.setOnSelect(job -> picked.add(job.id()));
            history.remember(answered("job-1", "a question"));

            history.list().getSelectionModel().select(0);

            assertEquals(List.of("job-1"), picked);
        });
    }

    @Test
    void showing_which_one_is_on_screen_is_not_a_click() {
        // Otherwise setting the selection re-enters the handler that set it.
        FxToolkit.onFx(() -> {
            List<String> picked = new ArrayList<>();
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            history.remember(answered("job-1", "a question"));
            history.setOnSelect(job -> picked.add(job.id()));

            history.setCurrent("job-1");

            assertEquals(List.of(), picked);
            assertEquals("job-1", history.list().getSelectionModel().getSelectedItem().id());
        });
    }

    @Test
    void nothing_on_screen_means_nothing_selected() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            history.remember(answered("job-1", "a question"));
            history.setCurrent("job-1");

            history.setCurrent(null);

            assertNull(history.list().getSelectionModel().getSelectedItem());
        });
    }

    @Test
    void an_id_that_is_not_in_the_list_selects_nothing_rather_than_the_wrong_row() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            history.remember(answered("job-1", "a question"));

            history.setCurrent("job-999");

            assertNull(history.list().getSelectionModel().getSelectedItem());
        });
    }

    @Test
    void a_row_carries_the_verdict_given_to_it() {
        // The only place a user can see at a glance what they have already
        // said about what.
        FxToolkit.onFx(() -> {
            FeedbackStore store = Stores.of(new Stores.Recording());
            HistoryView history = new HistoryView(store, 10);
            history.remember(answered("job-1", "a good one"));
            history.remember(answered("job-2", "a bad one"));
            history.remember(answered("job-3", "no opinion"));
            store.vote("job-1", "a good one", Models.Verdict.YES);
            store.vote("job-2", "a bad one", Models.Verdict.NO);
            history.refresh();

            javafx.stage.Stage stage = FxToolkit.render(new javafx.scene.layout.VBox(history.node()));

            List<String> rows = Nodes.texts(history.node());
            assertTrue(rows.stream().anyMatch(row -> row.startsWith("✓") && row.endsWith("a good one")));
            assertTrue(rows.stream().anyMatch(row -> row.startsWith("✗") && row.endsWith("a bad one")));
            assertTrue(rows.contains("no opinion"));
            stage.close();
        });
    }

    @Test
    void an_empty_list_says_what_will_appear_in_it() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            javafx.stage.Stage stage = FxToolkit.render(new javafx.scene.layout.VBox(history.node()));

            assertTrue(Nodes.says(history.node(), "Answers appear here"));
            stage.close();
        });
    }


    @Test
    void a_list_nobody_wired_up_still_works() {
        FxToolkit.onFx(() -> {
            HistoryView history = new HistoryView(Stores.of(new Stores.Recording()), 10);
            history.remember(answered("job-1", "a question"));

            history.list().getSelectionModel().select(0);

            assertEquals("job-1", history.list().getSelectionModel().getSelectedItem().id());
        });
    }
}
