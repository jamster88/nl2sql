/**
 * The rows, as the database returned them.
 *
 * Each column is headed by its name and its type, because "the agent summed
 * a text column" and "the agent compared a date to a string" are both
 * answered by the type. NULL is shown as NULL and styled apart from the
 * empty string. When more rows came back than the console reads, it says
 * so: a result that looks complete and is not is the mistake this whole page
 * exists to catch in the agent.
 */

import { useState } from "react";

import { cellText, counted, isNumericType, toCsv } from "../api/format";
import type { QueryResult } from "../api/types";

export interface ResultTableProps {
  result: QueryResult;
  /** Where Copy CSV writes. The browser's clipboard unless a test says otherwise. */
  copy?: (text: string) => Promise<void>;
}

function clipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text);
}

export function ResultTable({ result, copy = clipboard }: ResultTableProps) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");

  function copyCsv() {
    const csv = toCsv(
      result.columns.map((column) => column.name),
      result.rows,
    );
    Promise.resolve()
      .then(() => copy(csv))
      .then(
        () => setCopied("done"),
        () => setCopied("failed"),
      );
  }

  if (result.columns.length === 0) {
    return <p className="muted">No rows: the query was not run to completion.</p>;
  }
  const numeric = result.columns.map((column) => isNumericType(column.data_type));

  return (
    <section className="results" aria-label="Rows">
      <div className="results-head">
        <span>
          {counted(result.row_count, "row")}
          {result.truncated && (
            <span className="warn">
              {" "}
              -- the first {result.max_rows}; more came back and were not read
            </span>
          )}
        </span>
        <button type="button" className="button button-small" onClick={copyCsv}>
          {copied === "done" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy CSV"}
        </button>
      </div>
      <div className="table-scroll">
        <table className="grid">
          <thead>
            <tr>
              <th scope="col" className="rownum">
                #
              </th>
              {result.columns.map((column, index) => (
                <th scope="col" key={`${column.name}-${index}`} className={numeric[index] ? "num" : undefined}>
                  <span className="mono">{column.name}</span>
                  <span className="coltype">{column.data_type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <td className="rownum muted">{rowIndex + 1}</td>
                {row.map((cell, index) => (
                  <td
                    key={index}
                    className={[numeric[index] ? "num" : "", cell === null ? "null" : ""]
                      .filter(Boolean)
                      .join(" ") || undefined}
                  >
                    {cellText(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
