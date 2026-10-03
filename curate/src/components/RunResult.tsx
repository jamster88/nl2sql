/**
 * What running a golden pair's or a fix's SQL against the live database showed.
 *
 * `stale` is the whole reason this is more than a table: the save buttons
 * are bound to the exact text that passed, and one keystroke after it a
 * result is a result about some other query.
 */

import type { ValidationModel } from "../api/types";
import { Rows } from "./Rows";

export interface RunResultProps {
  validation: ValidationModel;
  stale: boolean;
}

export function RunResult({ validation, stale }: RunResultProps) {
  return (
    <div
      className={`validation ${validation.valid ? "validation-ok" : "validation-failed"}`}
      role={validation.valid ? "status" : "alert"}
      aria-label="Validation result"
    >
      {stale && <p className="notice notice-quiet">Edited since it was validated. Validate again before saving.</p>}
      {validation.valid ? (
        <p>
          <strong>It runs.</strong> {validation.row_count}
          {validation.truncated ? "+" : ""} row{validation.row_count === 1 ? "" : "s"}
          {validation.plan_cost !== null ? ` · plan cost ${validation.plan_cost}` : ""} · {validation.elapsed_ms} ms
        </p>
      ) : (
        <>
          <p>
            <strong>It cannot be saved.</strong>
          </p>
          <ul>
            {validation.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </>
      )}
      {validation.warnings.length > 0 && (
        <ul className="validation-warnings">
          {validation.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <Rows columns={validation.columns} rows={validation.rows} total={validation.row_count} />
    </div>
  );
}
