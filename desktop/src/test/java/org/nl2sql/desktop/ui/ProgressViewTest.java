package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.control.Label;
import javafx.scene.layout.FlowPane;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
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

    // --- under the ensemble (7.0): a lane for every run -------------------

    private static ProgressView laned(int paraphrases) {
        ProgressView progress = new ProgressView();
        progress.setNodes(Fakes.ensembleNodes());
        progress.setEnsemble(Fakes.ensembleSettings(true, paraphrases));
        return progress;
    }

    private static List<String> laneStates(ProgressView progress) {
        return Nodes.withClass(progress.node(), "progress-lane").stream()
                .map(lane -> ((Label) Nodes.oneWithClass(lane, "progress-lane-name")).getText()
                        + ":" + lane.getStyleClass().get(1))
                .toList();
    }

    private static List<String> chips(Node row) {
        return ((FlowPane) row).getChildren().stream()
                // A label's own class comes first; the step's state is the last.
                .map(chip -> ((Label) chip).getText() + ":"
                        + chip.getStyleClass().get(chip.getStyleClass().size() - 1))
                .toList();
    }

    @Test
    void a_waiting_lane_is_drawn_for_every_run_the_server_said_to_expect() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);

            assertTrue(Nodes.says(progress.node(), "0 / 4 runs"));
            assertEquals(List.of("Run 1:waiting", "Run 2:waiting", "Run 3:waiting", "Run 4:waiting"),
                    laneStates(progress));
            // Nothing says yet which of its own steps come before the runs,
            // so they are all above the lanes and nothing is below.
            List<Node> rows = Nodes.withClass(progress.node(), "progress-steps");
            assertEquals(5, rows.size());
            assertEquals(11, chips(rows.get(0)).size());
            assertEquals(List.of("supervise:pending", "generate_sql:pending", "finish:pending"),
                    chips(rows.get(1)));
        });
    }

    @Test
    void one_of_the_ensembles_own_steps_is_reported_with_no_run_named() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);

            progress.add(Fakes.own(1, "paraphrase", "3 rewording(s)"));

            assertTrue(Nodes.says(progress.node(), "paraphrase — 3 rewording(s)"));
            assertFalse(Nodes.says(progress.node(), "As asked"));
        });
    }

    @Test
    void a_runs_step_goes_in_that_runs_lane_and_names_the_run() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);
            progress.add(Fakes.own(1, "screen"));
            progress.add(Fakes.own(2, "plan_wave"));

            progress.add(Fakes.run(3, 0, "generate_sql", "3 tables"));

            assertTrue(Nodes.says(progress.node(), "As asked: generate_sql — 3 tables"));
            assertTrue(Nodes.says(progress.node(), "1 / 4 runs"));
            assertEquals("As asked:active", laneStates(progress).get(0));
            Node lane = Nodes.withClass(progress.node(), "progress-lane").get(0);
            assertEquals(List.of("supervise:pending", "generate_sql:done", "finish:pending"),
                    chips(Nodes.oneWithClass(lane, "progress-steps")));
        });
    }

    @Test
    void its_own_steps_divide_at_the_first_run_into_those_before_and_those_after() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(1);
            progress.add(Fakes.own(1, "screen"));
            progress.add(Fakes.own(2, "plan_wave"));
            progress.add(Fakes.run(3, 0, "supervise"));

            List<Node> rows = Nodes.withClass(progress.node(), "progress-steps");
            // `refuse` never ran and stays with its neighbours: the division
            // is by place in the server's list, not by what has finished.
            assertEquals(List.of("screen:done", "refuse:pending", "paraphrase:pending",
                    "screen_paraphrase:pending", "plan_wave:done"), chips(rows.get(0)));
            assertEquals(List.of("answer:pending", "validate:pending", "judge:pending",
                    "vote:pending", "fuse:pending", "deliver:pending"),
                    chips(rows.get(rows.size() - 1)));
        });
    }

    @Test
    void a_lane_is_kept_for_each_run_in_the_order_they_began() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);
            progress.add(Fakes.own(1, "plan_wave"));
            progress.add(Fakes.run(2, 0, "supervise"));
            progress.add(Fakes.run(3, 0, "finish"));

            progress.add(Fakes.run(4, 2, "supervise", "seeded"));

            assertEquals(List.of("As asked:reported", "Rewording 2:active", "Run 3:waiting",
                    "Run 4:waiting"), laneStates(progress));
            assertTrue(Nodes.says(progress.node(), "Rewording 2: supervise — seeded"));
        });
    }

    @Test
    void it_stops_waiting_for_runs_once_the_ensemble_has_moved_on_to_their_answers() {
        // Three rewordings were asked for and one was faithful: the other
        // lanes would otherwise wait for runs that are not coming.
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);
            progress.add(Fakes.own(1, "plan_wave"));
            progress.add(Fakes.run(2, 0, "finish"));
            progress.add(Fakes.run(3, 1, "finish"));

            progress.add(Fakes.own(4, "answer", "2 run(s)"));

            assertEquals(List.of("As asked:reported", "Rewording 1:reported"), laneStates(progress));
            assertTrue(Nodes.says(progress.node(), "2 / 2 runs"));
            List<Node> rows = Nodes.withClass(progress.node(), "progress-steps");
            assertEquals("answer:done", chips(rows.get(rows.size() - 1)).get(0));
        });
    }

    @Test
    void finishing_says_how_many_steps_and_runs_it_took() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(1);
            progress.add(Fakes.own(1, "screen"));
            progress.add(Fakes.run(2, 0, "finish"));
            progress.add(Fakes.run(3, 1, "finish"));

            progress.finished();

            assertTrue(Nodes.says(progress.node(), "Finished — 3 steps in 2 runs"));
            // Nothing is still reporting.
            assertEquals(List.of("As asked:reported", "Rewording 1:reported"), laneStates(progress));
        });
    }

    @Test
    void one_run_is_a_run_rather_than_one_runs() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(0);
            progress.add(Fakes.run(1, 0, "finish"));

            progress.finished();

            assertTrue(Nodes.says(progress.node(), "Finished — 1 step in 1 run"));
        });
    }

    @Test
    void a_question_refused_before_any_run_shows_no_lanes_and_mentions_no_runs() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(3);
            progress.add(Fakes.own(1, "screen", "injection"));
            progress.add(Fakes.own(2, "refuse"));

            progress.finished();

            assertEquals(0, Nodes.withClass(progress.node(), "progress-lane").size());
            assertTrue(Nodes.texts(progress.node()).contains("Finished — 2 steps"));
            assertTrue(Nodes.texts(progress.node()).stream().noneMatch(text -> text.contains("/")));
        });
    }

    @Test
    void lanes_are_drawn_from_the_stream_alone_when_meta_never_said_there_would_be_runs() {
        // The stream is the truth. With no node list every step is shown as
        // it arrives, in the lane of the run that reported it, and there are
        // no steps of the ensemble's own to draw above or below.
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();

            progress.add(Fakes.run(1, 0, "supervise"));
            progress.add(Fakes.run(2, 0, "repair"));

            assertEquals(List.of("As asked:active"), laneStates(progress));
            List<Node> rows = Nodes.withClass(progress.node(), "progress-steps");
            assertEquals(1, rows.size());
            assertEquals(List.of("supervise:done", "repair:done"), chips(rows.get(0)));
            assertTrue(Nodes.says(progress.node(), "1 / 1 runs"));
        });
    }

    @Test
    void a_node_list_shorter_than_the_ensembles_own_leaves_a_run_no_steps_to_predict() {
        // A proxy that cut the list, or a server that sent only its own
        // nodes: nothing is subtracted past the end of it.
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("screen"));
            progress.setEnsemble(Fakes.ensembleSettings(true, 0));

            progress.add(Fakes.run(1, 0, "supervise"));

            Node lane = Nodes.oneWithClass(progress.node(), "progress-lane");
            assertEquals(List.of("supervise:done"), chips(Nodes.oneWithClass(lane, "progress-steps")));
        });
    }

    @Test
    void with_the_ensemble_off_it_is_one_row_of_steps_as_before() {
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("supervise", "finish"));
            progress.setEnsemble(Fakes.ensembleSettings(false, 3));

            progress.add(Fakes.step(1, "supervise", "screen"));

            assertEquals(0, Nodes.withClass(progress.node(), "progress-lane").size());
            assertTrue(Nodes.says(progress.node(), "1 / 2"));
        });
    }

    @Test
    void a_run_reported_by_a_server_that_said_the_ensemble_was_off_still_gets_its_lane() {
        // The stream is the truth, and meta is what the server said earlier.
        // No run is waited for that it did not say to expect.
        FxToolkit.onFx(() -> {
            ProgressView progress = new ProgressView();
            progress.setNodes(List.of("supervise", "finish"));
            progress.setEnsemble(Fakes.ensembleSettings(false, 3));

            progress.add(Fakes.run(1, 0, "supervise"));

            assertEquals(List.of("As asked:active"), laneStates(progress));
            assertTrue(Nodes.says(progress.node(), "1 / 1 runs"));
        });
    }

    @Test
    void resetting_clears_the_lanes_for_the_next_question() {
        FxToolkit.onFx(() -> {
            ProgressView progress = laned(1);
            progress.add(Fakes.run(1, 0, "finish"));
            progress.finished();

            progress.reset();

            assertEquals(List.of("Run 1:waiting", "Run 2:waiting"), laneStates(progress));
            assertTrue(Nodes.says(progress.node(), "Queued"));
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
