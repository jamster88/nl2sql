package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class StatusBarTest {

    @Test
    void before_anything_answers_it_says_so() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(new StatusBar("verified").node(), "Connecting")));
    }

    @Test
    void it_names_the_things_asked_of_every_answer_that_looks_wrong() {
        // Which model answered this, and how many tables were in scope.
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified against nl2sql-api.crt");
            bar.show(Fakes.meta(true));

            assertTrue(Nodes.says(bar.node(), "nl2sql-agent 4.5.0"));
            assertTrue(Nodes.says(bar.node(), "qwen3.8-256k"));
            assertTrue(Nodes.says(bar.node(), "19 tables"));
            assertTrue(Nodes.says(bar.node(), "token required"));
            assertTrue(Nodes.says(bar.node(), Fakes.SCOPE));
        });
    }

    @Test
    void it_says_how_the_certificate_is_being_checked() {
        // A browser decides that for itself and shows a padlock. This was
        // told, by a flag, and the answer can be "not at all".
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("NOT VERIFIED");
            bar.show(Fakes.meta(true));

            assertTrue(Nodes.says(bar.node(), "NOT VERIFIED"));
        });
    }

    @Test
    void it_says_whether_a_verdict_goes_anywhere() {
        FxToolkit.onFx(() -> {
            StatusBar staged = new StatusBar("verified");
            staged.show(Fakes.meta(true));
            assertTrue(Nodes.says(staged.node(), "feedback staged for review"));

            StatusBar local = new StatusBar("verified");
            local.show(Fakes.meta(false));
            assertTrue(Nodes.says(local.node(), "feedback kept locally"));
        });
    }

    @Test
    void one_table_is_a_table_and_no_scope_is_no_line() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            Models.Meta meta = Fakes.meta(true);
            bar.show(new Models.Meta(meta.service(), meta.version(), meta.model(), meta.intents(),
                    List.of("dim_store"), "", meta.limits(), meta.pipeline(), Map.of(), "none",
                    false));

            assertTrue(Nodes.says(bar.node(), "1 table"));
            assertTrue(Nodes.says(bar.node(), "no token"));
            assertFalse(Nodes.says(bar.node(), "Retail"));
        });
    }

    @Test
    void warnings_are_shown_beside_everything_else() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            bar.show(Fakes.meta(true));

            bar.setWarnings(List.of("ollama: connection refused"));

            assertTrue(Nodes.says(bar.node(), "ollama: connection refused"));
        });
    }

    @Test
    void warnings_that_arrive_before_meta_wait_for_it() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");

            bar.setWarnings(List.of("ollama: connection refused"));
            assertTrue(Nodes.says(bar.node(), "Connecting"));

            bar.show(Fakes.meta(true));
            assertTrue(Nodes.says(bar.node(), "ollama: connection refused"));
        });
    }

    @Test
    void a_client_that_cannot_reach_its_api_says_so_in_words() {
        // Every other part of the window will simply look empty.
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            bar.show(Fakes.meta(true));

            bar.showError("cannot reach the API at https://localhost:8443");

            assertTrue(Nodes.says(bar.node(), "Not connected."));
            assertTrue(Nodes.says(bar.node(), "cannot reach the API"));
            assertTrue(Nodes.says(bar.node(), "./launch.sh --api"));
            assertFalse(Nodes.says(bar.node(), "qwen2.5-coder:32b"));
        });
    }

    @Test
    void going_back_to_connecting_clears_what_was_there() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            bar.show(Fakes.meta(true));

            bar.connecting();

            assertFalse(Nodes.says(bar.node(), "qwen2.5-coder:32b"));
            // A warning set now has nothing to hang on, and must not redraw
            // the meta that is no longer there.
            bar.setWarnings(List.of("late"));
            assertTrue(Nodes.says(bar.node(), "Connecting"));
        });
    }

private static double barHeight(double width, boolean failed) {
        StatusBar bar = new StatusBar("verified against nl2sql-api.crt");
        if (failed) {
            bar.showError("cannot reach the API at https://nl2sql.example.com:8443: "
                    + "no route to host after ten seconds");
        } else {
            bar.show(Fakes.meta(true));
        }
        javafx.stage.Stage stage =
                FxToolkit.render(new javafx.scene.layout.VBox(bar.node()), width, 200);
        double height = bar.node().getLayoutBounds().getHeight();
        for (javafx.scene.Node label : Nodes.withClass(bar.node(), "muted")) {
            assertTrue(Nodes.width(label) <= width, "a status line is " + Nodes.width(label));
        }
        stage.close();
        return height;
    }

    @Test
    void the_sentence_describing_the_database_wraps_rather_than_being_cut_off() {
        FxToolkit.onFx(() -> assertTrue(barHeight(300, false) > barHeight(900, false)));
    }

    @Test
    void the_reason_it_is_not_connected_wraps_too() {
        FxToolkit.onFx(() -> assertTrue(barHeight(300, true) > barHeight(900, true)));
    }


    @Test
    void the_line_naming_the_tables_does_not_set_a_floor_under_the_window() {
        // It runs to four hundred characters, and a label's minimum width is
        // the width its text wants -- so this one asked for two thousand
        // pixels and got them, at the expense of everything to its right.
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified against nl2sql-api.crt");
            bar.show(Fakes.meta(true));
            bar.setWarnings(Fakes.WARNINGS);
            javafx.stage.Stage stage =
                    FxToolkit.render(new javafx.scene.layout.VBox(bar.node()), 600, 300);

            double floor = bar.node().minWidth(-1);
            assertTrue(floor < 200, "the status bar's floor is " + floor);
            for (javafx.scene.Node label : Nodes.withClass(bar.node(), "muted")) {
                assertTrue(Nodes.width(label) <= 600,
                        "a status line is " + Nodes.width(label) + " wide in 600");
            }
            stage.close();
        });
    }
}
