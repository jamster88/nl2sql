package org.nl2sql.desktop.chart;

import java.util.Locale;

/**
 * The forms the pipeline can ask for.
 *
 * <p>The agent's Visual Formatter decides the form from the shape of the
 * result -- one number is a headline, a category against a measure is a bar, a
 * date against a measure is a line, two measures are a scatter -- so this
 * client does not decide again. It draws what was asked for.
 *
 * <p>An unrecognised kind becomes {@link #TABLE}, which draws no chart and
 * leaves the rows to speak for themselves. That is the one safe answer: the
 * kinds are the pipeline's to choose, a release that adds one would otherwise
 * take this client down with it, and the table is always correct.
 * {@code tests/java/test_desktop_contract.py} checks that every kind
 * {@code present.py} can produce is named here, so the fallback stays a
 * safeguard rather than a habit.
 */
public enum ChartKind {
    SCALAR,
    BAR,
    GROUPED_BAR,
    LINE,
    SCATTER,
    TABLE;

    public static ChartKind of(String wire) {
        for (ChartKind kind : values()) {
            if (kind.name().toLowerCase(Locale.ROOT).equals(wire)) {
                return kind;
            }
        }
        return TABLE;
    }
}
