/**
 * Undoing the agent's markup escaping.
 *
 * The pipeline's prose is built to be rendered as markdown, and markdown
 * allows raw HTML -- so `present.py` escapes `&`, `<` and `>` on the way in,
 * which is right for its own output and for anything that renders it as
 * markdown. This application is neither: React renders a string as text, so
 * an escaped ampersand arrives on screen as the five characters `&amp;`.
 *
 * Three entities, because `html.escape(quote=False)` produces exactly three.
 * A general entity decoder is the wrong tool here -- it would also decode
 * things the pipeline never wrote, turning a literal `&copy;` in a product
 * name into a symbol -- and the usual trick of setting `innerHTML` on a
 * detached element to let the browser decode is how a text field becomes an
 * injection point.
 *
 * The order is the inverse of the escaping: `&` is escaped first, so it is
 * unescaped last. Otherwise a product genuinely called `&lt;` -- escaped to
 * `&amp;lt;` -- would come back as `<`.
 */
export function plainText(markup: string): string {
  return markup.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

/**
 * The prose at the top of a markdown answer.
 *
 * `answer` is a whole document -- a paragraph, then the result as a markdown
 * table, then any caveats -- because that is what a terminal wants. A GUI
 * draws the table itself and would otherwise print the pipe characters. The
 * first block is the sentence; everything after the first blank line is a
 * rendering this application replaces.
 */
export function leadParagraph(markup: string): string {
  // `search` rather than `split`, which would need a fallback for an index
  // that is always there -- a branch no input can take and no test can reach.
  const table = markup.search(/\n\s*\n/);
  return plainText(table === -1 ? markup : markup.slice(0, table)).trim();
}
