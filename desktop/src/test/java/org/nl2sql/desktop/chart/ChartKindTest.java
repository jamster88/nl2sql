package org.nl2sql.desktop.chart;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;

class ChartKindTest {

    @ParameterizedTest
    @EnumSource(ChartKind.class)
    void every_kind_is_named_as_the_pipeline_writes_it(ChartKind kind) {
        assertSame(kind, ChartKind.of(kind.name().toLowerCase(Locale.ROOT)));
    }

    @Test
    void a_kind_this_client_has_not_heard_of_falls_back_to_the_table() {
        // The kinds are the pipeline's to choose. A client that threw on a
        // new one would fail on the release that added it, and the table is
        // always correct.
        assertEquals(ChartKind.TABLE, ChartKind.of("sunburst"));
        assertEquals(ChartKind.TABLE, ChartKind.of(""));
        assertEquals(ChartKind.TABLE, ChartKind.of("BAR"));
    }
}
