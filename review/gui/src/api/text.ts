/**
 * Undoing the agent's markup escaping.
 *
 * The agent's prose is built to be rendered as markdown, and markdown allows
 * raw HTML -- so `present.py` escapes `&`, `<` and `>` on the way in. This
 * application renders it as React text instead, where an escaped ampersand
 * arrives on screen as the five characters `&amp;`. A reviewer judging
 * whether an answer named the right banner should see `Thrift & Table`.
 *
 * The same three entities, in the same order, as the web GUI's `plainText`
 * (`gui/src/api/text.ts`): `html.escape(quote=False)` produces exactly
 * these, and `&` is escaped first, so it is unescaped last. A general
 * decoder -- or `innerHTML` on a detached element -- would decode things the
 * pipeline never wrote and turn a text field into an injection point.
 */
export function plainText(markup: string): string {
  return markup
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/** `1 correction`, `2 corrections`: a count and its noun, agreeing. */
export function counted(
  count: number,
  singular: string,
  plural = `${singular}s`,
): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
