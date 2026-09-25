package org.nl2sql.desktop.ui;

import javafx.beans.property.ReadOnlyObjectWrapper;
import javafx.collections.FXCollections;
import javafx.scene.control.Label;
import javafx.scene.control.TableCell;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.layout.Region;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.chart.Values;

import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The rows, as a table.
 *
 * <p>The table is always right, which is why it is always drawn: a chart is a
 * reading of the result and this is the result. It is also what discharges the
 * obligation the palette carries -- three of the series colours are below the
 * contrast a graphic needs on its own, which is allowed only where the values
 * are readable as text as well.
 *
 * <p>Cells are shown exactly as they arrived. A {@code numeric} crosses JSON as
 * a string so it does not lose precision, and reformatting it here would undo
 * that for the sake of making two columns line up.
 *
 * <p>The one piece of behaviour is {@link #highlight}: the audit ties each
 * sentence of the narrative to the cells it was read from, so pointing at a
 * sentence lights up its cells here. That is the clearest thing this API makes
 * possible, and it costs a set of keys and a redraw.
 */
public final class ResultTableView {

    private final Region node;
    private final TableView<List<Object>> table;
    private final Set<String> lit = new HashSet<>();

    public ResultTableView(Models.ResultTable result) {
        if (result.columns().isEmpty()) {
            this.table = null;
            this.node = note("The query returned no columns.");
            return;
        }
        if (result.rows().isEmpty()) {
            this.table = null;
            this.node = note("The query ran and matched no rows.");
            return;
        }

        TableView<List<Object>> built = new TableView<>();
        built.getStyleClass().add("result-table");
        for (int index = 0; index < result.columns().size(); index++) {
            built.getColumns().add(column(result.columns().get(index), index));
        }
        built.setItems(FXCollections.observableArrayList(result.rows()));
        built.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY_FLEX_LAST_COLUMN);
        // Tall enough to be a table and short enough to leave the answer on
        // screen. Anything longer is what the scroll bar is for.
        built.setPrefHeight(Math.min(360, 28.0 * (result.rows().size() + 1) + 8));
        this.table = built;
        this.node = built;
    }

    public Region node() {
        return node;
    }

    /** Light up these cells, and only these. Keys come from {@link #cellKey}. */
    public void highlight(Collection<String> keys) {
        lit.clear();
        lit.addAll(keys);
        if (table != null) {
            // The cell factory reads the set, so the redraw is the update.
            // Binding each cell to an observable set instead would mean one
            // listener per cell on a table that can hold a thousand.
            table.refresh();
        }
    }

    /** How a claim names one cell: the row it is in and the column's name. */
    public static String cellKey(int row, String column) {
        return row + ":" + column;
    }

    /** What the row count says, including the part the server had to leave out. */
    public static Label caption(Models.ResultTable result) {
        String counted = result.row_count() == 1 ? "1 row" : result.row_count() + " rows";
        Label label = new Label(result.truncated()
                ? counted + ", truncated to the server's row limit"
                : counted);
        label.getStyleClass().add("muted");
        return label;
    }

    private TableColumn<List<Object>, String> column(String name, int index) {
        TableColumn<List<Object>, String> header = new TableColumn<>(name);
        header.setCellValueFactory(row ->
                new ReadOnlyObjectWrapper<>(Values.toLabel(cell(row.getValue(), index))));
        header.setCellFactory(unused -> new TableCell<>() {
            @Override
            protected void updateItem(String value, boolean empty) {
                super.updateItem(value, empty);
                setText(empty ? null : value);
                boolean on = !empty && lit.contains(cellKey(getIndex(), name));
                pseudoClassStateChanged(Styles.HIGHLIGHTED, on);
            }
        });
        return header;
    }

    private static Object cell(List<Object> row, int column) {
        return column < row.size() ? row.get(column) : null;
    }

    private static Region note(String what) {
        Label label = new Label(what);
        label.getStyleClass().add("muted");
        return label;
    }
}
