/** Wording helpers every page shares (V6-25). */

/**
 * Undoing the agent's markup escaping.
 *
 * The pipeline's text is built to be rendered as markdown, and markdown
 * allows raw HTML -- so `present.py` escapes `&`, `<` and `>` on the way
 * out, which is right for its own output and for anything that renders it
 * as markdown. A page is neither: React renders a string as text, so an
 * escaped ampersand arrives on screen as the five characters `&amp;`. And a
 * narrative staged before agent 5.1.1 reads `Thrift &amp; Table` whatever
 * renders it.
 *
 * Exactly the three entities `html.escape(quote=False)` produces, `&` last
 * because it was escaped first -- the desktop client's `Markup.plain` is
 * held to the same three (`tests/web/test_web_package.py`). A general
 * decoder, or `innerHTML` on a detached element, would decode things the
 * pipeline never wrote and turn a text field into an injection point.
 */
export function plainText(markup: string): string {
  return markup.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

/** `1 correction`, `1,204 corrections`: a count and its noun, agreeing. */
export function counted(count: number, singular: string, plural = `${singular}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? singular : plural}`;
}
