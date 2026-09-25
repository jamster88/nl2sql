package org.nl2sql.desktop.chart;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class ValuesTest {

    @Test
    void a_numeric_arrives_as_a_string_and_is_still_a_number() {
        // Postgres `numeric` crosses JSON as a string so it does not lose
        // precision; `integer` crosses as a number. Both are the same value.
        assertEquals(719279.97, Values.toNumber("719279.97"));
        assertEquals(719279.97, Values.toNumber(719279.97));
        assertEquals(42.0, Values.toNumber(42));
        assertEquals(-3.5, Values.toNumber(" -3.5 "));
    }

    @Test
    void a_boolean_counts_as_one_or_zero() {
        assertEquals(1.0, Values.toNumber(true));
        assertEquals(0.0, Values.toNumber(false));
    }

    @Test
    void nothing_that_is_not_a_number_is_quietly_turned_into_zero() {
        // `Number("")` is 0 in more languages than it should be, and every
        // missing value would then be a real point sitting on the axis.
        assertNull(Values.toNumber(""));
        assertNull(Values.toNumber("   "));
        assertNull(Values.toNumber("Produce"));
        assertNull(Values.toNumber(null));
        assertNull(Values.toNumber(List.of()));
        assertNull(Values.toNumber(Double.NaN));
        assertNull(Values.toNumber(Double.POSITIVE_INFINITY));
        assertNull(Values.toNumber("Infinity"));
    }

    @Test
    void a_null_cell_is_labelled_rather_than_left_as_a_gap() {
        assertEquals("—", Values.toLabel(null));
        assertEquals("Produce", Values.toLabel("Produce"));
        assertEquals("42", Values.toLabel(42));
        assertEquals("true", Values.toLabel(true));
    }

    @ParameterizedTest
    @CsvSource({
            "1250000000, 1.25B",
            "-2000000000, -2B",
            "1250000, 1.25M",
            "719279.97, '719,280'",
            "1000, '1,000'",
            "999.5, 999.5",
            "42, 42",
            "0, 0",
            "0.81, 0.81",
            "0.0001234, 0.0001",
    })
    void a_number_is_short_enough_for_an_axis_and_honest_enough_for_a_total(double value,
                                                                           String shown) {
        assertEquals(shown, Values.formatValue(value));
    }

    @Test
    void a_small_magnitude_keeps_its_decimals() {
        // A 0.81 match score rounded to 1 would be a lie.
        assertEquals("0.81", Values.formatValue(0.81));
        assertEquals("-0.5", Values.formatValue(-0.5));
    }

    @Test
    void rounding_leaves_no_trailing_zeros_behind() {
        assertEquals("2", Values.trim(2.000, 2));
        assertEquals("2.5", Values.trim(2.50, 2));
        assertEquals("100", Values.trim(100.0, 2));
    }
}
