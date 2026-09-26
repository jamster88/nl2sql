package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.input.KeyCode;
import javafx.scene.input.KeyEvent;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class AskBoxTest {

    private static KeyEvent key(KeyCode code, boolean shift) {
        return new KeyEvent(KeyEvent.KEY_PRESSED, "", "", code, shift, false, false, false);
    }

    @Test
    void a_question_is_sent_and_the_box_is_cleared() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);

            box.setQuestion("how many stores are there?");
            box.action().fire();

            assertEquals(List.of("how many stores are there?"), asked);
            // Cleared on send, like every other message box: otherwise the
            // next thing typed is appended to the last question.
            assertEquals("", box.question());
        });
    }

    @Test
    void the_button_is_dead_until_there_is_something_to_ask() {
        FxToolkit.onFx(() -> {
            AskBox box = new AskBox();

            assertTrue(box.action().isDisable());
            box.setQuestion("   ");
            assertTrue(box.action().isDisable());
            box.setQuestion("anything");
            assertFalse(box.action().isDisable());
        });
    }

    @Test
    void enter_sends_and_shift_enter_does_not() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);
            box.setQuestion("how many stores");

            box.field().fireEvent(key(KeyCode.ENTER, true));
            assertEquals(List.of(), asked);

            box.field().fireEvent(key(KeyCode.ENTER, false));
            assertEquals(List.of("how many stores"), asked);
        });
    }

    @Test
    void another_key_is_left_alone() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);
            box.setQuestion("how many stores");

            box.field().fireEvent(key(KeyCode.A, false));

            assertEquals(List.of(), asked);
        });
    }

    @Test
    void while_a_question_is_running_the_button_stops_it_instead() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            int[] cancelled = {0};
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);
            box.setOnCancel(() -> cancelled[0]++);
            box.setQuestion("how many stores");

            box.setBusy(true);

            assertEquals("Stop", box.action().getText());
            assertFalse(box.action().isDisable());
            assertTrue(box.field().isDisable());
            box.action().fire();
            assertEquals(1, cancelled[0]);
            assertEquals(List.of(), asked);

            box.setBusy(false);
            assertEquals("Ask", box.action().getText());
        });
    }

    @Test
    void the_server_s_own_length_limit_is_enforced_in_the_box() {
        // Read from `/v1/meta`, so an over-long question is refused where the
        // user can still do something about it rather than coming back as a
        // 422 they cannot act on.
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);
            box.setMaxQuestionLength(10);

            box.setQuestion("this is very much longer than ten");

            assertTrue(box.action().isDisable());
            assertTrue(Nodes.says(box.node(), "this server takes 10"));
            box.submit();
            assertEquals(List.of(), asked);

            box.setQuestion("short");
            assertFalse(box.action().isDisable());
            assertFalse(Nodes.says(box.node(), "this server takes 10"));
        });
    }

    @Test
    void the_examples_ask_their_own_question_and_go_once_something_has_been_asked() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);

            List<Node> chips = Nodes.withClass(box.node(), "chip");
            assertEquals(AskBox.EXAMPLES.size(), chips.size());
            ((javafx.scene.control.Button) chips.get(0)).fire();
            assertEquals(List.of(AskBox.EXAMPLES.get(0)), asked);

            box.setExamplesVisible(false);
            assertFalse(Nodes.oneWithClass(box.node(), "ask-examples").isVisible());
        });
    }

    @Test
    void the_examples_are_four_different_shapes_of_answer() {
        // One number, a bar, a line and a grouped bar, so the first thing a
        // new user clicks shows what this can do rather than four tables.
        assertEquals(4, AskBox.EXAMPLES.size());
        assertEquals(4, AskBox.EXAMPLES.stream().distinct().count());
    }


    @Test
    void a_box_nobody_wired_up_still_works() {
        // The handlers have defaults so the component can be built before it
        // is connected to anything, which is the order the window does it in.
        FxToolkit.onFx(() -> {
            AskBox box = new AskBox();

            box.setQuestion("how many stores");
            box.submit();
            box.setBusy(true);
            box.action().fire();

            assertEquals("", box.question());
        });
    }

    @Test
    void nothing_is_sent_while_a_question_is_already_running() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);
            box.setQuestion("how many stores");
            box.setBusy(true);

            box.submit();
            // And typing while it runs leaves the Stop button alone.
            box.setQuestion("a second question");

            assertEquals(List.of(), asked);
            assertFalse(box.action().isDisable());
            assertEquals("Stop", box.action().getText());
        });
    }


    @Test
    void an_empty_box_sends_nothing_even_when_asked_directly() {
        FxToolkit.onFx(() -> {
            List<String> asked = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnAsk(asked::add);

            box.submit();

            assertEquals(List.of(), asked);
        });
    }

/** The example row's height, in a window of this width. */
    private static double examplesHeight(double width) {
        AskBox box = new AskBox();
        javafx.stage.Stage stage =
                FxToolkit.render(new javafx.scene.layout.VBox(box.node()), width, 500);
        double height = Nodes.heightOf(box.node(), "ask-examples");
        double pane = Nodes.widthOf(box.node(), "ask-examples");
        for (Node chip : Nodes.withClass(box.node(), "chip")) {
            assertTrue(Nodes.width(chip) <= pane + 1,
                    "a chip is " + Nodes.width(chip) + " wide in " + pane);
        }
        stage.close();
        return height;
    }

    @Test
    void a_long_example_wraps_instead_of_setting_a_floor_under_the_window() {
        // A button sizes itself to its text and a FlowPane hands it that
        // width, so the longest example was a five-hundred-pixel floor that
        // nothing else in the window could get under.
        FxToolkit.onFx(() -> {
            double wide = examplesHeight(900);
            double narrow = examplesHeight(380);

            assertTrue(narrow > wide, "the examples did not reflow: " + wide + " -> " + narrow);
        });
    }

    @Test
    void the_question_box_wraps_what_is_typed_into_it() {
        FxToolkit.onFx(() -> assertTrue(new AskBox().field().isWrapText()));
    }

    @Test
    void the_ask_button_keeps_its_label_however_narrow_the_window_is() {
        // An HBox takes the room it needs from whichever child will give it.
        // The text area gives; a button asked to give turns into an ellipsis,
        // which is the one control in the window nobody can guess at.
        FxToolkit.onFx(() -> {
            AskBox box = new AskBox();
            box.setQuestion("something");
            javafx.scene.layout.VBox host = new javafx.scene.layout.VBox(box.node());
            javafx.stage.Stage stage = FxToolkit.render(host, 300, 400);

            double wanted = box.action().prefWidth(-1);
            assertTrue(Nodes.width(box.action()) >= wanted - 1,
                    "the Ask button shrank to " + Nodes.width(box.action())
                            + " from " + wanted);
            stage.close();
        });
    }
}
