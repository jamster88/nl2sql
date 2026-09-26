package org.nl2sql.desktop.chart;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Locale;

/**
 * Cells, as numbers and as words.
 *
 * <p>Two facts about the data drive everything here. First, a cell is not
 * necessarily a number even when the column is: a Postgres {@code numeric}
 * crosses JSON as a string so it does not lose precision, so {@code "719279.97"}
 * and {@code 719279.97} are the same value arriving from two different column
 * types. Second, the chart spec names columns but promises nothing about them
 * -- it was chosen from the shape of the result, and a client that trusts it
 * blindly draws a broken axis the first time a row is null.
 *
 * <p>So every value is coerced and every coercion can fail, and the caller is
 * given a null it has to deal with rather than a zero it will not notice.
 */
public final class Values {

    private Values() {
    }

    /**
     * A cell as a number, or null when it is not one.
     *
     * <p>An empty string is rejected before the conversion rather than after
     * it: parsing it would not throw in every language, and in the ones where
     * it yields zero, every missing value becomes a real data point sitting on
     * the axis.
     */
    public static Double toNumber(Object cell) {
        if (cell instanceof Number number) {
            double value = number.doubleValue();
            return Double.isFinite(value) ? value : null;
        }
        if (cell instanceof Boolean flag) {
            return flag ? 1.0 : 0.0;
        }
        if (cell instanceof String text) {
            String trimmed = text.trim();
            if (trimmed.isEmpty()) {
                return null;
            }
            try {
                double value = Double.parseDouble(trimmed);
                return Double.isFinite(value) ? value : null;
            } catch (NumberFormatException notANumber) {
                return null;
            }
        }
        return null;
    }

    /** A cell as a label. Null is shown as such rather than as an empty gap. */
    public static String toLabel(Object cell) {
        if (cell == null) {
            return "—";
        }
        if (cell instanceof String text) {
            return text;
        }
        return String.valueOf(cell);
    }

    /**
     * A number, short enough for an axis and honest enough for a total.
     *
     * <p>Thousands separators below a million, then a suffix, because an axis
     * reading 1,250,000,000 is an axis nobody reads. Small magnitudes keep
     * their decimals: a 0.81 match score rounded to 1 would be a lie.
     */
    public static String formatValue(double value) {
        double magnitude = Math.abs(value);
        if (magnitude >= 1e9) {
            return trim(value / 1e9, 2) + "B";
        }
        if (magnitude >= 1e6) {
            return trim(value / 1e6, 2) + "M";
        }
        if (magnitude >= 1000) {
            return String.format(Locale.US, "%,.0f", value);
        }
        return trim(value, magnitude >= 1 || magnitude == 0 ? 2 : 4);
    }

    /** Rounded to {@code digits}, then with the zeros that adds taken back off. */
    static String trim(double value, int digits) {
        BigDecimal rounded = BigDecimal.valueOf(value)
                .setScale(digits, RoundingMode.HALF_UP)
                .stripTrailingZeros();
        // `stripTrailingZeros` turns 0 into 0E-2 and 100 into 1E+2, and
        // `toPlainString` is what puts both back into the form a person reads.
        return rounded.toPlainString();
    }
}
