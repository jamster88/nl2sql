/**
 * The rows.
 *
 * Always available, whatever else is drawn, for three reasons: it is the
 * only representation that is never a simplification, it is what a reader
 * with a colour-vision deficiency falls back to when a chart's series sit
 * close together, and it is where the numbers behind a claim can be pointed
 * at individually.
 *
 * That last one is `highlight`: the audit ties each sentence of the
 * narrative to the cells it was read from, so hovering a claim lights up its
 * cells here. It is the clearest thing this API makes possible and it costs
 * one `Set`.
 */

import { toNumber } from "../charts/values";
import type { ResultTable as Rows } from "../api/types";

export interface ResultTableProps {
  result: Rows;
  /** `row:column` keys to mark as the source of the hovered claim. */
  highlight?: ReadonlySet<string>;
  /** Rows to show before the table scrolls internally. */
  maxHeight?: number;
}

export function cellKey(row: number, column: string): string {
  return `${row}:${column}`;
}

export function ResultTable({ result, highlight, maxHeight = 420 }: ResultTableProps) {
  if (result.columns.length === 0) {
    return <p className="muted">The query returned no columns.</p>;
  }

  // Alignment is decided per column from the first row that has a value in
  // it, not per cell: a column where one number happens to be null should
  // not have one cell jump to the left.
  const numeric = result.columns.map((_, index) =>
    result.rows.some((row) => toNumber(row[index]) !== null),
  );

  return (
    <div className="table-wrap" style={{ maxHeight }}>
      <table className="table">
        <caption className="table-caption">
          {result.row_count} {result.row_count === 1 ? "row" : "rows"}
          {result.truncated && " — truncated by the server's row limit"}
        </caption>
        <thead>
          <tr>
            {result.columns.map((column, index) => (
              <th key={column} scope="col" data-numeric={numeric[index] === true}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {result.columns.map((column, index) => (
                <td
                  key={column}
                  data-numeric={numeric[index] === true}
                  data-highlight={highlight?.has(cellKey(rowIndex, column)) ?? false}
                >
                  {format(row[index])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.rows.length === 0 && <p className="muted">The query ran and matched nothing.</p>}
    </div>
  );
}

/**
 * A cell, as it came.
 *
 * Deliberately not reformatted: a `numeric` arrives as a string so it does
 * not lose precision, and re-rounding it here to look tidy would throw away
 * exactly the precision that was preserved. The charts format; the table
 * reports.
 */
function format(cell: unknown): string {
  if (cell === null || cell === undefined) return "—";
  if (typeof cell === "object") return JSON.stringify(cell);
  return String(cell);
}
