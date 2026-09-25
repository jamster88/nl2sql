package org.nl2sql.desktop.ui;

import javafx.scene.chart.BarChart;
import javafx.scene.chart.LineChart;
import javafx.scene.chart.ScatterChart;
import javafx.scene.chart.XYChart;
import javafx.scene.layout.Region;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.Models;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class ChartViewTest {

    private static Models.ChartSpec spec(String kind, String x, List<String> y, String series) {
        return new Models.ChartSpec(kind, x, y, series);
    }

    private static Models.ResultTable table(List<String> columns, List<List<Object>> rows) {
        return new Models.ResultTable(columns, rows, rows.size(), false);
    }

    @Test
    void a_bar_is_a_bar_chart_over_the_categories() {
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(Fakes.table(),
                    spec("bar", "department", List.of("net_sales"), null));

            BarChart<?, ?> chart = assertInstanceOf(BarChart.class, drawn);
            assertEquals(1, chart.getData().size());
            assertEquals(2, chart.getData().get(0).getData().size());
            // One series needs no key to itself.
            assertFalse(chart.isLegendVisible());
        });
    }

    @Test
    void a_line_is_a_line_chart_and_several_series_get_a_legend() {
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(
                    table(List.of("month", "region", "sales"),
                            List.of(List.of("Jan", "East", 1), List.of("Jan", "West", 2))),
                    spec("line", "month", List.of("sales"), "region"));

            LineChart<?, ?> chart = assertInstanceOf(LineChart.class, drawn);
            assertEquals(2, chart.getData().size());
            assertTrue(chart.isLegendVisible());
        });
    }

    @Test
    void a_scatter_puts_numbers_on_both_axes() {
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(
                    table(List.of("price", "units"), List.of(List.of(2.5, 10), List.of(3.0, 8))),
                    spec("scatter", "price", List.of("units"), null));

            ScatterChart<?, ?> chart = assertInstanceOf(ScatterChart.class, drawn);
            assertEquals(2, chart.getData().get(0).getData().size());
        });
    }

    @Test
    void one_number_is_drawn_as_one_number() {
        // A single value is not a chart, and a bar of one is the commonest
        // way to make a result harder to read than the sentence above it.
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(
                    table(List.of("stores"), List.of(List.of(1284))),
                    spec("scalar", null, List.of("stores"), null));

            assertTrue(Nodes.says(drawn, "1,284"));
            assertTrue(Nodes.says(drawn, "stores"));
        });
    }

    @Test
    void a_scalar_with_no_column_named_falls_back_to_the_first_one() {
        FxToolkit.onFx(() -> assertTrue(Nodes.says(
                ChartView.of(table(List.of("stores"), List.of(List.of(7))),
                        spec("scalar", null, List.of(), null)),
                "7")));
    }

    @Test
    void a_scalar_that_is_not_a_number_is_not_drawn() {
        FxToolkit.onFx(() -> {
            assertNull(ChartView.of(table(List.of("name"), List.of(List.of("Produce"))),
                    spec("scalar", null, List.of("name"), null)));
            assertNull(ChartView.of(table(List.of(), List.of()),
                    spec("scalar", null, List.of(), null)));
        });
    }

    @Test
    void a_table_kind_means_the_rows_are_the_presentation() {
        FxToolkit.onFx(() -> assertNull(
                ChartView.of(Fakes.table(), spec("table", null, List.of("net_sales"), null))));
    }

    @Test
    void a_kind_this_client_has_not_heard_of_draws_nothing_rather_than_failing() {
        FxToolkit.onFx(() -> assertNull(
                ChartView.of(Fakes.table(), spec("sunburst", "department", List.of("net_sales"), null))));
    }

    @Test
    void a_spec_that_cannot_be_drawn_from_these_rows_draws_nothing() {
        FxToolkit.onFx(() -> assertNull(ChartView.of(
                table(List.of("month", "sales"), List.of(Arrays.asList("Jan", null))),
                spec("bar", "month", List.of("sales"), null))));
    }

    @Test
    void every_mark_carries_what_it_is_for_the_tooltip() {
        FxToolkit.onFx(() -> {
            BarChart<?, ?> chart = (BarChart<?, ?>) ChartView.of(Fakes.table(),
                    spec("bar", "department", List.of("net_sales"), null));

            XYChart.Data<?, ?> first = chart.getData().get(0).getData().get(0);
            assertEquals("Produce · 719,280", first.getExtraValue());
        });
    }

    @Test
    void with_several_series_a_mark_says_which_one_it_is_in() {
        FxToolkit.onFx(() -> {
            LineChart<?, ?> chart = (LineChart<?, ?>) ChartView.of(
                    table(List.of("month", "region", "sales"),
                            List.of(List.of("Jan", "East", 1), List.of("Jan", "West", 2))),
                    spec("line", "month", List.of("sales"), "region"));

            assertEquals("Jan · East: 1",
                    chart.getData().get(0).getData().get(0).getExtraValue());
        });
    }

    @Test
    void a_mark_gets_its_tooltip_once_the_chart_has_been_laid_out() {
        // The node does not exist until then, which is why the tooltip is
        // installed from a listener rather than at build time -- a chart
        // that is never shown never lays out, and a test is exactly that.
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(Fakes.table(),
                    spec("bar", "department", List.of("net_sales"), null));
            javafx.stage.Stage stage = FxToolkit.render(new javafx.scene.layout.VBox(drawn));

            XYChart.Data<?, ?> first = ((BarChart<?, ?>) drawn).getData().get(0).getData().get(0);
            assertNotNull(first.getNode());
            assertTrue(first.getNode().getProperties().keySet().stream()
                            .anyMatch(key -> String.valueOf(key).contains("Tooltip")),
                    "the mark carries no tooltip: " + first.getNode().getProperties().keySet());
            stage.close();
        });
    }


    @Test
    void a_mark_that_loses_its_node_is_left_alone_rather_than_given_a_tooltip() {
        // The listener is told about every change to the node, including the
        // one back to nothing -- and installing a tooltip on nothing throws.
        FxToolkit.onFx(() -> {
            Region drawn = ChartView.of(Fakes.table(),
                    spec("bar", "department", List.of("net_sales"), null));
            javafx.stage.Stage stage = FxToolkit.render(new javafx.scene.layout.VBox(drawn));
            XYChart.Data<?, ?> first = ((BarChart<?, ?>) drawn).getData().get(0).getData().get(0);
            assertNotNull(first.getNode());

            first.setNode(null);

            assertNull(first.getNode());
            stage.close();
        });
    }
}
