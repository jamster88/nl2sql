/**
 * Undoing the agent's markup escaping.
 *
 * A submission's narrative is a snapshot taken at vote time, so it is
 * whatever the agent of that day wrote. Before agent 5.1.1 the narrator was
 * shown HTML-escaped rows and copied the entities into its claims, so every
 * narrative staged by then reads `Thrift &amp; Table`; from 5.1.1 it is plain
 * text. This application renders it as React text, where an escaped
 * ampersand arrives on screen as the five characters `&amp;` -- and a
 * reviewer judging whether an answer named the right banner should see
 * `Thrift & Table`, whichever agent wrote it. On plain text this changes
 * nothing.
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
