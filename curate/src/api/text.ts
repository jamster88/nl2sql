/** Small wording helpers, shared by every pane. */

/** `1 snippet`, `2 snippets`: a count and its noun, agreeing. */
export function counted(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

/** A cell as a table shows it: NULL for nothing, the text for anything else. */
export function cell(value: unknown): string {
  return value === null || value === undefined ? "NULL" : String(value);
}

/** The message of whatever was thrown. */
export function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Comma-separated names, without the blanks. */
export function names(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

/** True when a row's text contains every word of the search, in any case. */
export function matches(search: string, ...fields: string[]): boolean {
  const haystack = fields.join(" ").toLowerCase();
  return search
    .toLowerCase()
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .every((word) => haystack.includes(word));
}
