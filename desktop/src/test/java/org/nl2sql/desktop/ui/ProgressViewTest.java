package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class ProgressViewTest {

    @Test
    void the_steps_still_to_come_are_shown_before_they_happen() {
        // From `/v1/meta`, so the pipeline is a list that fills in rather
        // than steps appearing one at a time out of nowhere.
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen", "generate_sql", "run_sql"));

            assertTrue(Nodes.says(progress.node(), "0 / 3"));
            assertEquals(3, Nodes.withClass(progress.node(), "pending").size());
            assertTrue(Nodes.says(progress.node(), "Queued"));
        });
    }

    @Test
    void a_step_that_has_happened_says_what_it_did() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen", "generate_sql"));

            progress.add(Fakes.step(1, "generate_sql", "sql", "3 tables"));

            assertTrue(Nodes.says(progress.node(), "sql — 3 tables"));
            assertTrue(Nodes.says(progress.node(), "1 / 2"));
            assertEquals(1, Nodes.withClass(progress.node(), "done").size());
        });
    }

    @Test
    void a_step_the_server_did_not_list_is_still_shown() {
        // The stream is the truth and the node list is only a prediction.
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen"));

            progress.add(Fakes.step(1, "something_new", "new"));

            assertTrue(Nodes.says(progress.node(), "new"));
            assertTrue(Nodes.says(progress.node(), "1 / 2"));
        });
    }

    @Test
    void finishing_counts_what_happened() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen", "run_sql"));
            progress.add(Fakes.step(1, "screen", "screening"));
            progress.add(Fakes.step(2, "run_sql", "rows"));

            progress.finished();

            assertTrue(Nodes.says(progress.node(), "Finished — 2 steps"));
        });
    }

    @Test
    void one_step_is_a_step_rather_than_one_steps() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.add(Fakes.step(1, "screen", "screening"));

            progress.finished();

            assertTrue(Nodes.says(progress.node(), "Finished — 1 step"));
        });
    }

    @Test
    void resetting_puts_it_back_to_queued() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen"));
            progress.add(Fakes.step(1, "screen", "screening"));

            progress.reset();

            assertTrue(Nodes.says(progress.node(), "Queued"));
            assertTrue(Nodes.says(progress.node(), "0 / 1"));
            assertEquals(1, Nodes.withClass(progress.node(), "pending").size());
        });
    }

    @Test
    void with_no_node_list_at_all_there_is_no_count_to_show() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();

            assertTrue(Nodes.texts(progress.node()).stream().noneMatch(text -> text.contains("/")));
        });
    }

private static double currentStepHeight(double width) {
        ProgressView progress = new ProgressView();
        progress.setNodes(List.of("generate_sql"));
        progress.add(Fakes.step(1, "generate_sql", "sql",
                "3 tables, 2 worked examples, 14 knowledge chunks and one repair"));
        javafx.stage.Stage stage =
                FxToolkit.render(new javafx.scene.layout.VBox(progress.node()), width, 300);
        double height = Nodes.heightOf(progress.node(), "progress-current");
        stage.close();
        return height;
    }

    @Test
    void a_step_that_has_a_lot_to_say_wraps_rather_than_being_cut_off() {
        FxToolkit.onFx(() -> assertTrue(currentStepHeight(320) > currentStepHeight(900)));
    }
}
