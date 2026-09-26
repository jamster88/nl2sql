package org.nl2sql.desktop.ui;

import javafx.scene.Scene;
import javafx.scene.layout.VBox;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class StylesTest {

    @Test
    void the_stylesheet_ships_with_the_classes() {
        assertTrue(Styles.url().endsWith("/org/nl2sql/desktop/nl2sql.css"));
    }

    @Test
    void a_build_without_it_says_so_rather_than_drawing_an_unstyled_window() {
        assertTrue(assertThrows(IllegalStateException.class, () -> Styles.url("/absent.css"))
                .getMessage().contains("/absent.css"));
    }

    @Test
    void a_scene_comes_back_dressed() {
        FxToolkit.onFx(() -> {
            Scene scene = Styles.apply(new Scene(new VBox()));

            assertEquals(List.of(Styles.url()), scene.getStylesheets());
        });
    }

    @Test
    void the_two_states_that_cannot_be_class_names_have_pseudo_classes() {
        assertEquals("highlighted", Styles.HIGHLIGHTED.getPseudoClassName());
        assertEquals("chosen", Styles.CHOSEN.getPseudoClassName());
    }
}
