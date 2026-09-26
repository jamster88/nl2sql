package org.nl2sql.desktop.chart;

import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.api.Models;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The shaping, which is a port of {@code gui/src/charts/values.ts}.
 *
 * <p>These are the web interface's own cases, answered the same way. Two
 * clients that shape one result differently are two clients that disagree
 * about what the agent said, and the one somebody happens to be looking at
 * decides who is right.
 */
class ChartDataTest {

    private static Models.ResultTable table(List<String> columns, List<List<Object>> rows) {
        return new Models.ResultTable(columns, rows, rows.size(), false);
    }

    private static Models.ChartSpec spec(String kind, String x, List<String> y, String series) {
        return new Models.ChartSpec(kind, x, y, series);
    }

    @Test
    void a_category_against_a_measure_is_one_series_of_bands() {
        ChartData data = ChartData.of(
                table(List.of("department", "net_sales"),
                        List.of(List.of("Produce", "719279.97"), List.of("Bakery", 412000.5))),
                spec("bar", "department", List.of("net_sales"), null));

        assertEquals(List.of("Produce", "Bakery"), data.categories());
        assertEquals(1, data.series().size());
        assertEquals("net_sales", data.series().get(0).name());
        assertEquals(719279.97, data.series().get(0).points().get(0).y());
        assertEquals("department", data.xLabel());
        assertEquals("net_sales", data.yLabel());
        assertFalse(data.numericX());
    }

    @Test
    void without_an_x_column_every_row_is_its_own_slot_numbered_from_one() {
        // What the pipeline produces for a bare list of measures. "1, 2, 3"
        // is a more honest axis than inventing labels for it.
        ChartData data = ChartData.of(
                table(List.of("total"), List.of(List.of(1), List.of(2))),
                spec("bar", null, List.of("total"), null));

        assertEquals(List.of("1", "2"), data.categories());
        assertEquals("", data.xLabel());
    }

    @Test
    void two_measures_share_the_axis_and_it_is_left_unnamed() {
        // Labelling it with the first would misattribute the second.
        ChartData data = ChartData.of(
                table(List.of("month", "sales", "units"), List.of(List.of("Jan", 10, 3))),
                spec("grouped_bar", "month", List.of("sales", "units"), null));

        assertEquals(List.of("sales", "units"), data.series().stream().map(ChartData.Series::name).toList());
        assertEquals("", data.yLabel());
    }

    @Test
    void a_series_column_pivots_one_measure_into_several_series() {
        ChartData data = ChartData.of(
                table(List.of("month", "region", "sales"),
                        List.of(List.of("Jan", "East", 1), List.of("Jan", "West", 2),
                                List.of("Feb", "East", 3))),
                spec("line", "month", List.of("sales"), "region"));

        assertEquals(List.of("East", "West"),
                data.series().stream().map(ChartData.Series::name).toList());
        assertEquals(2, data.series().get(0).points().size());
        assertEquals(List.of("Jan", "Feb"), data.categories());
    }

    @Test
    void a_scatter_puts_numbers_on_both_axes() {
        ChartData data = ChartData.of(
                table(List.of("price", "units"), List.of(List.of(2.5, 10), List.of(3.0, 8))),
                spec("scatter", "price", List.of("units"), null));

        assertTrue(data.numericX());
        assertEquals(List.of(), data.categories());
        assertEquals(2.5, data.series().get(0).points().get(0).x());
        assertEquals("2.5, 10", data.series().get(0).points().get(0).label());
    }

    @Test
    void a_scatter_with_a_series_column_splits_by_it() {
        ChartData data = ChartData.of(
                table(List.of("price", "units", "region"),
                        List.of(List.of(1, 2, "East"), List.of(3, 4, "West"))),
                spec("scatter", "price", List.of("units"), "region"));

        assertEquals(List.of("East", "West"),
                data.series().stream().map(ChartData.Series::name).toList());
    }

    @Test
    void a_scatter_the_spec_named_no_x_for_cannot_be_drawn() {
        assertNull(ChartData.of(
                table(List.of("units"), List.of(List.of(1))),
                spec("scatter", null, List.of("units"), null)));
    }

    @Test
    void a_scatter_whose_x_is_not_in_the_result_cannot_be_drawn_either() {
        // Two distinct outcomes rather than one -1: the spec named no x, or
        // it named one the result has since lost.
        assertNull(ChartData.of(
                table(List.of("units"), List.of(List.of(1))),
                spec("scatter", "price", List.of("units"), null)));
    }

    @Test
    void a_spec_naming_no_measure_the_result_has_cannot_be_drawn() {
        assertNull(ChartData.of(
                table(List.of("department"), List.of(List.of("Produce"))),
                spec("bar", "department", List.of("net_sales"), null)));
    }

    @Test
    void an_empty_result_cannot_be_drawn() {
        assertNull(ChartData.of(table(List.of("a"), List.of()), spec("bar", null, List.of("a"), null)));
    }

    @Test
    void a_series_with_nothing_in_it_is_dropped_rather_than_drawn_flat_at_zero() {
        // A flat line at zero is a claim the data does not make.
        ChartData data = ChartData.of(
                table(List.of("month", "sales", "units"),
                        List.of(Arrays.asList("Jan", 10, null), Arrays.asList("Feb", 20, null))),
                spec("grouped_bar", "month", List.of("sales", "units"), null));

        assertEquals(List.of("sales"), data.series().stream().map(ChartData.Series::name).toList());
    }

    @Test
    void when_every_series_is_empty_there_is_nothing_to_draw() {
        assertNull(ChartData.of(
                table(List.of("month", "sales"),
                        List.of(Arrays.asList("Jan", null))),
                spec("bar", "month", List.of("sales"), null)));
        assertNull(ChartData.of(
                table(List.of("price", "units"), List.of(Arrays.asList(null, 1))),
                spec("scatter", "price", List.of("units"), null)));
    }

    @Test
    void the_tail_past_the_colour_cap_is_folded_into_one_rather_than_dropped() {
        // A chart that silently omits the ninth region is wrong about the
        // total; one that shows an "Other" is only less detailed.
        List<List<Object>> rows = new ArrayList<>();
        for (int region = 0; region < 12; region++) {
            rows.add(List.of("Jan", "region-" + region, region + 1));
        }
        ChartData data = ChartData.of(
                table(List.of("month", "region", "sales"), rows),
                spec("bar", "month", List.of("sales"), "region"));

        assertEquals(ChartData.SERIES_CAP, data.series().size());
        ChartData.Series last = data.series().get(ChartData.SERIES_CAP - 1);
        assertEquals(ChartData.OTHER_LABEL, last.name());
        assertEquals(12 - (ChartData.SERIES_CAP - 1), last.points().size());
    }

    @Test
    void a_scatter_folds_sooner_because_every_pair_is_on_screen_at_once() {
        List<List<Object>> rows = new ArrayList<>();
        for (int region = 0; region < 6; region++) {
            rows.add(List.of(region, region * 2, "region-" + region));
        }
        ChartData data = ChartData.of(
                table(List.of("price", "units", "region"), rows),
                spec("scatter", "price", List.of("units"), "region"));

        assertEquals(ChartData.SERIES_CAP_ALL_PAIRS, data.series().size());
        assertEquals(ChartData.OTHER_LABEL,
                data.series().get(ChartData.SERIES_CAP_ALL_PAIRS - 1).name());
    }

    @Test
    void a_row_shorter_than_the_header_says_is_a_missing_cell_rather_than_a_crash() {
        assertNull(ChartData.cell(
                table(List.of("a", "b"), List.of(List.of(1))), 0, 1));
        assertNull(ChartData.cell(table(List.of("a"), List.of(List.of(1))), 5, 0));
        assertNull(ChartData.cell(table(List.of("a"), List.of(List.of(1))), -1, 0));
        assertNull(ChartData.cell(table(List.of("a"), List.of(List.of(1))), 0, -1));
    }

    @Test
    void several_rows_in_one_band_all_get_a_mark() {
        ChartData data = ChartData.of(
                table(List.of("month", "sales"),
                        List.of(List.of("Jan", 1), List.of("Jan", 2), List.of("Feb", 3))),
                spec("bar", "month", List.of("sales"), null));

        assertEquals(List.of("Jan", "Feb"), data.categories());
        assertEquals(3, data.series().get(0).points().size());
    }


    @Test
    void a_row_with_no_measure_in_it_is_skipped_rather_than_charted_as_zero() {
        ChartData data = ChartData.of(
                table(List.of("month", "region", "sales"),
                        List.of(List.of("Jan", "East", 1), Arrays.asList("Jan", "West", null))),
                spec("bar", "month", List.of("sales"), "region"));

        assertEquals(List.of("East"), data.series().stream().map(ChartData.Series::name).toList());
    }

    @Test
    void a_scatter_point_missing_either_coordinate_is_skipped() {
        ChartData data = ChartData.of(
                table(List.of("price", "units"),
                        List.of(List.of(1, 2), Arrays.asList(null, 3), Arrays.asList(4, null))),
                spec("scatter", "price", List.of("units"), null));

        assertEquals(1, data.series().get(0).points().size());
    }
}
