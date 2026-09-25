package org.nl2sql.desktop.ui;

/**
 * Undoing the agent's markup escaping.
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
public final class Text {

    private Text() {
    }

    public static String plain(String markup) {
        return markup.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&");
    }

    /**
     * The prose at the top of a markdown answer.
     *
     * <p>{@code answer} is a whole document -- a paragraph, then the result as
     * a markdown table, then any caveats -- because that is what a terminal
     * wants. This application draws the table itself and would otherwise print
     * the pipe characters. The first block is the sentence; everything after
     * the first blank line is a rendering this application replaces.
     */
    public static String lead(String markup) {
        java.util.regex.Matcher blank =
                java.util.regex.Pattern.compile("\\n\\s*\\n").matcher(markup);
        return plain(blank.find() ? markup.substring(0, blank.start()) : markup).trim();
    }
}
