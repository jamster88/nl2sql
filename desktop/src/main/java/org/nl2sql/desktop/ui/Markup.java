package org.nl2sql.desktop.ui;

/**
 * Undoing the agent's markup escaping.
 *
 * <p>Named for what it undoes rather than for what it produces, because the
 * obvious name is taken: {@code javafx.scene.text.Text} is the node the
 * answer's sentences are drawn as, and two classes called {@code Text} in one
 * file is a trap rather than a coincidence.
 *
 * <p>The pipeline's prose is built to be rendered as markdown, and markdown
 * allows raw HTML -- so {@code present.py} escapes {@code &}, {@code <} and
 * {@code >} on the way in, which is right for its own output and for anything
 * that renders it as markdown. This application is neither: a JavaFX label
 * draws a string as text, so an escaped ampersand arrives on screen as the
 * five characters {@code &amp;}.
 *
 * <p>Three entities, because {@code html.escape(quote=False)} produces exactly
 * three. A general entity decoder is the wrong tool here -- it would also
 * decode things the pipeline never wrote, turning a literal {@code &copy;} in
 * a product name into a symbol.
 *
 * <p>The order is the inverse of the escaping: {@code &} is escaped first, so
 * it is unescaped last. Otherwise a product genuinely called {@code &lt;} --
 * escaped to {@code &amp;lt;} -- would come back as {@code <}.
 */
public final class Markup {

    private Markup() {
    }

    public static String plain(String markup) {
        return markup.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&");
    }

    /**
     * The prose at the top of a markdown answer.
     *
     * <p>{@code answer} is a whole document -- a paragraph, then the result
     * as a markdown table, then any caveats -- because that is what a
     * terminal wants. This application draws the table itself and would
     * otherwise print the pipe characters.
     *
     * <p>Two things end the prose: a blank line, and the first row of the
     * table. The blank line alone is not enough, because the pipeline does
     * not always put one there -- an answer that is nothing but a table
     * starts with {@code | sku_id | net_sales |} on line one, and taking
     * everything up to the first blank line then takes the whole table.
     */
    public static String lead(String markup) {
        StringBuilder prose = new StringBuilder();
        for (String line : markup.split("\n", -1)) {
            if (line.isBlank() || line.stripLeading().startsWith("|")) {
                break;
            }
            prose.append(line).append('\n');
        }
        return plain(prose.toString()).trim();
    }
}
