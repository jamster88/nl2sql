package org.nl2sql.desktop.ui;

import javafx.scene.chart.BarChart;
import javafx.scene.chart.CategoryAxis;
import javafx.scene.chart.LineChart;
import javafx.scene.chart.NumberAxis;
import javafx.scene.chart.ScatterChart;
import javafx.scene.chart.XYChart;
import javafx.scene.control.Label;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.chart.ChartData;
import org.nl2sql.desktop.chart.ChartKind;
import org.nl2sql.desktop.chart.Values;

/**
 * The chart, dispatched on what the pipeline asked for.
 *
 * <p>The agent's Visual Formatter already decided the form from the shape of
 * the result, so this does not decide again. It draws what was asked for and
 * returns nothing when the request cannot be honoured -- which is a normal
 * outcome, not a failure: {@code kind: "table"} means the rows are the
 * presentation, and the table below is there either way.
 *
 * <p>JavaFX's own charts rather than a drawing of our own. The web interface
 * draws its marks by hand because bending a charting library towards a mark
 * specification costs more than a linear scale does; here the library is part
 * of the toolkit the application is already built on, its axes and legends
 * follow the platform, and the alternative would be porting several hundred
 * lines of scale arithmetic for a second time to get to the same picture.
 */
public final class ChartView {

    private ChartView() {
    }

    /** A chart for this result, or null when there is nothing to draw. */
    public static Region of(Models.ResultTable result, Models.ChartSpec spec) {
        ChartKind kind = ChartKind.of(spec.kind());
        if (kind == ChartKind.SCALAR) {
            return scalar(result, spec);
        }
        if (kind == ChartKind.TABLE) {
            return null;
        }
        ChartData data = ChartData.of(result, spec);
        if (data == null) {
            return null;
        }
        return kind == ChartKind.SCATTER ? scatter(data) : banded(data, kind);
    }

    /**
     * One number, drawn as one number.
     *
     * <p>A single value is not a chart, and drawing it as a bar of one is the
     * most common way to make a result harder to read than the sentence above
     * it.
     */
    private static Region scalar(Models.ResultTable result, Models.ChartSpec spec) {
        String column = !spec.y().isEmpty() ? spec.y().get(0)
                : result.columns().isEmpty() ? "" : result.columns().get(0);
        Double value = Values.toNumber(ChartData.cell(result, 0, result.columns().indexOf(column)));
        if (value == null) {
            return null;
        }
        Label number = new Label(Values.formatValue(value));
        number.getStyleClass().add("scalar-value");
        Label caption = new Label(column);
        caption.getStyleClass().add("scalar-label");
        VBox tile = new VBox(number, caption);
        tile.getStyleClass().add("scalar");
        return tile;
    }

    private static Region banded(ChartData data, ChartKind kind) {
        CategoryAxis x = new CategoryAxis();
        x.setLabel(data.xLabel());
        x.getCategories().addAll(data.categories());
        NumberAxis y = new NumberAxis();
        y.setLabel(data.yLabel());

        XYChart<String, Number> chart = kind == ChartKind.LINE
                ? new LineChart<>(x, y)
                : new BarChart<>(x, y);
        for (ChartData.Series series : data.series()) {
            XYChart.Series<String, Number> drawn = new XYChart.Series<>();
            drawn.setName(series.name());
            for (ChartData.Point point : series.points()) {
                XYChart.Data<String, Number> datum = new XYChart.Data<>(point.label(), point.y());
                drawn.getData().add(datum);
                describe(datum, data, series, point);
            }
            chart.getData().add(drawn);
        }
        return style(chart, data);
    }

    private static Region scatter(ChartData data) {
        NumberAxis x = new NumberAxis();
        x.setLabel(data.xLabel());
        x.setForceZeroInRange(false);
        NumberAxis y = new NumberAxis();
        y.setLabel(data.yLabel());
        y.setForceZeroInRange(false);

        ScatterChart<Number, Number> chart = new ScatterChart<>(x, y);
        for (ChartData.Series series : data.series()) {
            XYChart.Series<Number, Number> drawn = new XYChart.Series<>();
            drawn.setName(series.name());
            for (ChartData.Point point : series.points()) {
                XYChart.Data<Number, Number> datum = new XYChart.Data<>(point.x(), point.y());
                drawn.getData().add(datum);
                describe(datum, data, series, point);
            }
            chart.getData().add(drawn);
        }
        return style(chart, data);
    }

    /**
     * The tooltip, which is also the accessible name for the mark.
     *
     * <p>Installed on the datum rather than the node because the node does not
     * exist until the chart has been laid out, and a chart that is never shown
     * never lays out -- which is exactly the case a test is.
     */
    private static void describe(XYChart.Data<?, ?> datum, ChartData data, ChartData.Series series,
                                 ChartData.Point point) {
        String value = Values.formatValue(point.y());
        String text = data.series().size() > 1
                ? point.label() + " · " + series.name() + ": " + value
                : point.label() + " · " + value;
        datum.setExtraValue(text);
        datum.nodeProperty().addListener((observable, before, node) -> {
            if (node != null) {
                Tooltip.install(node, new Tooltip(text));
            }
        });
    }

    private static Region style(XYChart<?, ?> chart, ChartData data) {
        chart.getStyleClass().add("answer-chart");
        chart.setAnimated(false);
        // A chart asks for room for its axis labels and holds the whole
        // column open at that width. It is the one thing here that can be
        // read at any size, so it gives way to the prose beside it.
        chart.setMinWidth(0);
        // One series needs no key to itself; more than one does.
        chart.setLegendVisible(data.series().size() > 1);
        chart.setPrefHeight(320);
        return chart;
    }
}
