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

/**
 * A markdown answer without its first blocks: one, unless told how many.
 *
 * Under the ensemble (7.0) the document opens with the agreement line --
 * how many runs there were and how they agreed -- before anything the runs
 * say. This page shows that line as a badge, from `Answer.ensemble.line`,
 * so the prose it falls back to is the block after it. A follow-up whose
 * restatement held (8.0) opens with one more, "Read as: ...", which the
 * page shows under the question, from `Answer.conversation`.
 */
export function afterLead(markup: string, blocks = 1): string {
  let rest = markup;
  for (let skipped = 0; skipped < blocks; skipped += 1) {
    const gap = /\n\s*\n/.exec(rest);
    if (gap === null) return "";
    rest = rest.slice(gap.index + gap[0].length);
  }
  return rest;
}
