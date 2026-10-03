/** A sample of rows, as a validation sends them back. */

import { cell } from "../api/text";

export interface RowsProps {
  columns: readonly string[];
  rows: readonly (readonly unknown[])[];
  /** How many there were in all, when that is more than are shown. */
  total?: number;
}

export function Rows({ columns, rows, total }: RowsProps) {
  if (columns.length === 0) return null;
  return (
    <div className="result-table">
      <table>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={`${column}-${index}`}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((value, column) => (
                <td key={column}>{cell(value)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total !== undefined && total > rows.length && (
        <p className="muted">
          First {rows.length} of {total} rows.
        </p>
      )}
    </div>
  );
}
