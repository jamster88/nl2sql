package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class MarkupTest {

    @Test
    void the_three_entities_the_pipeline_escapes_are_the_three_undone() {
        // `html.escape(quote=False)` produces exactly these three, and a
        // general decoder would also turn a literal `&copy;` in a product
        // name into a symbol.
        assertEquals("Ben & Jerry's <Vanilla>", Markup.plain("Ben &amp; Jerry's &lt;Vanilla&gt;"));
        assertEquals("&copy; 2026", Markup.plain("&copy; 2026"));
    }

    @Test
    void the_order_is_the_inverse_of_the_escaping() {
        // A product genuinely called `&lt;` is escaped to `&amp;lt;`, and
        // undoing the ampersand first would bring it back as `<`.
        assertEquals("&lt;", Markup.plain("&amp;lt;"));
    }

    @Test
    void the_lead_is_the_prose_above_the_markdown_table() {
        // `answer` is a whole document; this window draws the table itself
        // and would otherwise print the pipe characters.
        assertEquals("Produce net sales were $719,279.97 in FY2025.",
                Markup.lead("Produce net sales were $719,279.97 in FY2025.\n\n"
                        + "| department | net_sales |\n| --- | --- |\n"));
    }

    @Test
    void an_answer_that_is_only_prose_is_all_lead() {
        assertEquals("There is nothing to report.", Markup.lead("  There is nothing to report.  "));
    }

    @Test
    void the_lead_is_unescaped_too() {
        assertEquals("Ben & Jerry's", Markup.lead("Ben &amp; Jerry's\n\n| a |"));
    }


    @Test
    void the_prose_stops_at_the_table_as_well_as_at_a_blank_line() {
        // The pipeline does not always put a blank line before the table, and
        // an answer that is nothing but a table starts with one on line one --
        // so stopping only at a blank line printed the whole thing, pipes and
        // dashes, where the sentence should have been.
        assertEquals("Produce net sales were $719,279.97.", Markup.lead("""
                Produce net sales were $719,279.97.
                | department | net_sales |
                | --- | --- |
                | Produce | 719279.97 |
                """));
    }

    @Test
    void an_answer_that_is_only_a_table_has_no_prose_in_it() {
        assertEquals("", Markup.lead("""
                | sku_id | net_sales |
                | --- | --- |
                | SKU100189 | 138685.01 |
                """));
    }

    @Test
    void an_indented_table_row_ends_the_prose_too() {
        assertEquals("Here it is.", Markup.lead("Here it is.\n  | a | b |\n"));
    }
}
