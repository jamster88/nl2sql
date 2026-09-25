package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.control.TableCell;
import javafx.scene.layout.VBox;
import javafx.stage.Stage;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;

import java.util.Arrays;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class ResultTableViewTest {

    private static List<TableCell<?, ?>> cells(Node root) {
        List<TableCell<?, ?>> found = new java.util.ArrayList<>();
        for (Node node : Nodes.withClass(root, "table-cell")) {
            if (node instanceof TableCell<?, ?> cell) {
                found.add(cell);
            }
        }
        return found;
    }

    @Test
    void the_rows_are_shown_exactly_as_they_arrived() {
        // A numeric crosses JSON as a string so it does not lose precision,
        // and reformatting it here would undo that to line two columns up.
        FxToolkit.onFx(() -> {
            ResultTableView table = new ResultTableView(Fakes.table());
            Stage stage = FxToolkit.render(new VBox(table.node()));

            List<String> shown = cells(table.node()).stream()
                    .map(TableCell::getText).filter(text -> text != null && !text.isEmpty()).toList();
            assertTrue(shown.contains("719279.97"));
            assertTrue(shown.contains("Produce"));
            stage.close();
        });
    }

    @Test
    void a_cell_a_claim_was_read_from_lights_up_and_goes_out_again() {
        FxToolkit.onFx(() -> {
            ResultTableView table = new ResultTableView(Fakes.table());
            Stage stage = FxToolkit.render(new VBox(table.node()));

            table.highlight(Set.of(ResultTableView.cellKey(0, "net_sales")));
            table.node().applyCss();
            table.node().layout();
            assertEquals(1, cells(table.node()).stream()
                    .filter(cell -> cell.getPseudoClassStates().contains(Styles.HIGHLIGHTED))
                    .count());

            table.highlight(Set.of());
            table.node().applyCss();
            table.node().layout();
            assertEquals(0, cells(table.node()).stream()
                    .filter(cell -> cell.getPseudoClassStates().contains(Styles.HIGHLIGHTED))
                    .count());
            stage.close();
        });
    }

    @Test
    void a_query_that_matched_nothing_says_so_rather_than_showing_an_empty_grid() {
        // An empty table means the query ran and matched nothing, which is a
        // sentence rather than a header with no rows under it.
        FxToolkit.onFx(() -> {
            ResultTableView table =
                    new ResultTableView(new Models.ResultTable(List.of("a"), List.of(), 0, false));

            assertTrue(Nodes.says(table.node(), "matched no rows"));
            // Highlighting one of no cells is allowed and does nothing.
            table.highlight(Set.of("0:a"));
        });
    }

    @Test
    void a_result_with_no_columns_says_that_instead() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                new ResultTableView(new Models.ResultTable(List.of(), List.of(), 0, false)).node(),
                "no columns")));
    }

    @Test
    void a_row_shorter_than_the_header_leaves_a_dash_rather_than_failing() {
        FxToolkit.onFx(() -> {
            ResultTableView table = new ResultTableView(new Models.ResultTable(
                    List.of("a", "b"), List.of(List.of("only one"), Arrays.asList("x", null)),
                    2, false));
            Stage stage = FxToolkit.render(new VBox(table.node()));

            assertTrue(cells(table.node()).stream()
                    .anyMatch(cell -> "—".equals(cell.getText())));
            stage.close();
        });
    }

    @Test
    void the_caption_counts_the_rows_and_says_when_the_server_cut_them_short() {
        FxToolkit.onFx(() -> {
            assertEquals("2 rows", ResultTableView.caption(Fakes.table()).getText());
            assertEquals("1 row", ResultTableView.caption(
                    new Models.ResultTable(List.of("a"), List.of(List.of(1)), 1, false)).getText());
            assertTrue(ResultTableView.caption(
                    new Models.ResultTable(List.of("a"), List.of(List.of(1)), 500, true))
                    .getText().contains("truncated"));
        });
    }

    @Test
    void a_cell_key_is_the_row_and_the_column_name() {
        assertEquals("3:net_sales", ResultTableView.cellKey(3, "net_sales"));
        assertFalse(ResultTableView.cellKey(3, "a").equals(ResultTableView.cellKey(30, "a")));
    }
}
