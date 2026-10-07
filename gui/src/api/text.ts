import { plainText } from "@nl2sql/web/text";

/**
 * Undoing the agent's markup escaping: the rule every page shares
 * (`web/src/text.ts`), for the pipeline's `answer`, `narrative` and claims.
 */
export { plainText };

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
