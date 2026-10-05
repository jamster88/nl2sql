/**
 * Turning what the console returns into text a person reads.
 *
 * A cell arrives as JSON can carry it: numerics as strings (so `719279.97`
 * is not shown as `719279.9699999999`), dates as ISO strings, arrays and
 * JSON columns as themselves. NULL is kept apart from the empty string --
 * "no value" and "a value that is empty" are different answers, and telling
 * them apart is often the whole point of looking.
 */

/** The text of one cell. `null` is the caller's to style; this says NULL. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** A numeric scalar, with its precision if it has one -- not an array of one. */
const NUMERIC = /^(smallint|integer|bigint|numeric|decimal|real|double precision|money)(\(\d+(,\s*\d+)?\))?$/;

/** Right-aligned in a table, so the digits line up. */
export function isNumericType(dataType: string): boolean {
  return NUMERIC.test(dataType);
}

/** `1 row`, `1,204 rows`: a count and its noun, agreeing -- every page's (`web/src/text.ts`). */
export { counted } from "@nl2sql/web/text";

/** A cost or a time, grouped and rounded the way EXPLAIN reads. */
export function formatNumber(value: number, digits = 2): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/**
 * `part` as a percentage of `whole`, for a cost against its ceiling. A real
 * share too small to show is "<0.1%", never "0%": zero would say the query
 * costs nothing, which no query does.
 */
export function formatShare(part: number, whole: number): string {
  const share = (part / whole) * 100;
  return share > 0 && share < 0.1 ? "<0.1%" : `${formatNumber(share, 1)}%`;
}

/**
 * An identifier as the SQL a person would write it: bare when Postgres
 * would read it back unchanged, quoted otherwise.
 */
export function identifier(name: string): string {
  return /^[a-z_][a-z0-9_$]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`;
}

function csvField(text: string): string {
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The rows as CSV, for pasting into something that is not this page. */
export function toCsv(columns: readonly string[], rows: readonly (readonly unknown[])[]): string {
  const lines = [columns.map(csvField).join(",")];
  for (const row of rows) {
    lines.push(row.map((cell) => (cell === null ? "" : csvField(cellText(cell)))).join(","));
  }
  return lines.join("\n");
}
