package org.nl2sql.desktop.chart;

import org.nl2sql.desktop.api.Models;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * From rows to something drawable.
 *
 * <p>This is a port of {@code gui/src/charts/values.ts}, and it is a port
 * rather than a second design on purpose: two clients that shape the same
 * result differently are two clients that disagree about what the agent said,
 * and the one people happen to be looking at decides who is right.
 *
 * <p>The shaping rule worth knowing is that a series which ends up with
 * nothing in it is dropped rather than drawn as a flat line at zero, which
 * would be a claim the data does not make. When that leaves nothing at all,
 * {@link #of} returns null and the caller falls back to the table -- a normal
 * outcome, not a failure: the spec names columns of a result the server has
 * since truncated, or every value in the measure column is null.
 */
public record ChartData(List<Series> series, List<String> categories, String xLabel, String yLabel,
                        boolean numericX) {

    /** One mark. {@code row} is which row of {@code result} it came from. */
    public record Point(String label, double x, double y, int row) {
    }

    public record Series(String name, List<Point> points) {
    }

    /** How many series can be drawn before the tail becomes "Other". */
    public static final int SERIES_CAP = 8;

    /**
     * How many can be drawn when every pair is on screen at once.
     *
     * <p>A scatter puts every series beside every other, so the colours have
     * to be told apart in all directions rather than only from their
     * neighbours. Three is what survives that.
     */
    public static final int SERIES_CAP_ALL_PAIRS = 3;

    /** The label a series past the cap is folded into. */
    public static final String OTHER_LABEL = "Other";

    /** Shape a result for drawing, or null when it cannot be drawn. */
    public static ChartData of(Models.ResultTable result, Models.ChartSpec spec) {
        List<String> measures = new ArrayList<>();
        for (String name : spec.y()) {
            if (result.columns().contains(name)) {
                measures.add(name);
            }
        }
        if (measures.isEmpty() || result.rows().isEmpty()) {
            return null;
        }
        return ChartKind.of(spec.kind()) == ChartKind.SCATTER
                ? scatter(result, spec, measures)
                : bands(result, spec, measures);
    }

    /** One slot on the x axis: its label, and the rows that landed in it. */
    private record Band(String label, List<Integer> rows) {
    }

    /** x is a band: one slot per row, or per distinct value of {@code series}. */
    private static ChartData bands(Models.ResultTable result, Models.ChartSpec spec,
                                   List<String> measures) {
        int xIndex = columnIndex(result, spec.x());
        int seriesIndex = columnIndex(result, spec.series());

        // Without an x column every row is its own slot, numbered from one.
        // That is what the pipeline produces for a bare list of measures, and
        // "1, 2, 3" is a more honest axis than inventing labels for it.
        Map<String, List<Integer>> grouped = new LinkedHashMap<>();
        for (int row = 0; row < result.rows().size(); row++) {
            String label = xIndex >= 0
                    ? Values.toLabel(cell(result, row, xIndex))
                    : String.valueOf(row + 1);
            grouped.computeIfAbsent(label, unused -> new ArrayList<>()).add(row);
        }
        List<Band> bands = new ArrayList<>();
        grouped.forEach((label, rows) -> bands.add(new Band(label, rows)));

        List<Series> series = seriesIndex >= 0
                ? pivot(result, seriesIndex, measures.get(0), bands)
                : measuresAsSeries(result, measures, bands);

        List<Series> drawn = capped(series, false);
        if (drawn.isEmpty()) {
            return null;
        }
        List<String> categories = new ArrayList<>();
        for (Band band : bands) {
            categories.add(band.label());
        }
        return new ChartData(
                List.copyOf(drawn),
                List.copyOf(categories),
                spec.x() == null ? "" : spec.x(),
                // Named only when there is one measure to name. Two measures
                // share the axis, and labelling it with the first would
                // misattribute the second.
                measures.size() == 1 ? measures.get(0) : "",
                false);
    }

    private static List<Series> measuresAsSeries(Models.ResultTable result, List<String> measures,
                                                 List<Band> bands) {
        List<Series> series = new ArrayList<>();
        for (String name : measures) {
            series.add(new Series(name, pointsFor(result, columnIndex(result, name), bands)));
        }
        return series;
    }

    private static List<Point> pointsFor(Models.ResultTable result, int measure, List<Band> bands) {
        List<Point> points = new ArrayList<>();
        for (int slot = 0; slot < bands.size(); slot++) {
            Band band = bands.get(slot);
            for (int row : band.rows()) {
                Double value = Values.toNumber(cell(result, row, measure));
                if (value != null) {
                    points.add(new Point(band.label(), slot, value, row));
                }
            }
        }
        return points;
    }

    /** One series per distinct value of the {@code series} column. */
    private static List<Series> pivot(Models.ResultTable result, int seriesIndex, String measure,
                                      List<Band> bands) {
        int measureIndex = columnIndex(result, measure);
        Map<String, List<Point>> byName = new LinkedHashMap<>();
        for (int slot = 0; slot < bands.size(); slot++) {
            Band band = bands.get(slot);
            for (int row : band.rows()) {
                Double value = Values.toNumber(cell(result, row, measureIndex));
                if (value == null) {
                    continue;
                }
                String name = Values.toLabel(cell(result, row, seriesIndex));
                byName.computeIfAbsent(name, unused -> new ArrayList<>())
                        .add(new Point(band.label(), slot, value, row));
            }
        }
        return named(byName);
    }

    /** x and y are both numbers. */
    private static ChartData scatter(Models.ResultTable result, Models.ChartSpec spec,
                                     List<String> measures) {
        // Taken apart rather than tested with `columnIndex`, so that "the spec
        // named no x" and "the spec named an x the result does not have" stay
        // two distinct outcomes instead of collapsing into one -1.
        String xName = spec.x();
        if (xName == null) {
            return null;
        }
        int xIndex = result.columns().indexOf(xName);
        if (xIndex < 0) {
            return null;
        }

        String measure = measures.get(0);
        int yIndex = result.columns().indexOf(measure);
        int seriesIndex = columnIndex(result, spec.series());
        Map<String, List<Point>> byName = new LinkedHashMap<>();
        for (int row = 0; row < result.rows().size(); row++) {
            Double x = Values.toNumber(cell(result, row, xIndex));
            Double y = Values.toNumber(cell(result, row, yIndex));
            if (x == null || y == null) {
                continue;
            }
            String name = seriesIndex >= 0 ? Values.toLabel(cell(result, row, seriesIndex)) : measure;
            String label = Values.formatValue(x) + ", " + Values.formatValue(y);
            byName.computeIfAbsent(name, unused -> new ArrayList<>())
                    .add(new Point(label, x, y, row));
        }

        List<Series> drawn = capped(named(byName), true);
        if (drawn.isEmpty()) {
            return null;
        }
        return new ChartData(List.copyOf(drawn), List.of(), xName, measure, true);
    }

    private static List<Series> named(Map<String, List<Point>> byName) {
        List<Series> series = new ArrayList<>();
        byName.forEach((name, points) -> series.add(new Series(name, points)));
        return series;
    }

    /**
     * Drop empty series, then fold everything past the colour cap into one.
     *
     * <p>Folding rather than truncating: a chart that silently omits the ninth
     * region is a chart that is wrong about the total, while one that shows an
     * "Other" is only less detailed.
     */
    private static List<Series> capped(List<Series> series, boolean allPairs) {
        List<Series> populated = new ArrayList<>();
        for (Series entry : series) {
            if (!entry.points().isEmpty()) {
                populated.add(entry);
            }
        }
        int cap = allPairs ? SERIES_CAP_ALL_PAIRS : SERIES_CAP;
        if (populated.size() <= cap) {
            return populated;
        }
        List<Series> kept = new ArrayList<>(populated.subList(0, cap - 1));
        List<Point> folded = new ArrayList<>();
        for (Series entry : populated.subList(cap - 1, populated.size())) {
            folded.addAll(entry.points());
        }
        kept.add(new Series(OTHER_LABEL, folded));
        return kept;
    }

    private static int columnIndex(Models.ResultTable result, String name) {
        return name == null ? -1 : result.columns().indexOf(name);
    }

    /** A cell, or null when the row is shorter than the header says. */
    public static Object cell(Models.ResultTable result, int row, int column) {
        if (row < 0 || row >= result.rows().size()) {
            return null;
        }
        List<Object> cells = result.rows().get(row);
        return column < 0 || column >= cells.size() ? null : cells.get(column);
    }
}
