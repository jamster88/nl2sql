/**
 * What running a snippet inside its probe query showed.
 *
 * The probe is shown, not only its verdict: a snippet is a fragment, and the
 * curator should see the query it was checked inside -- the join joined, the
 * filter filtering -- rather than take "it runs" on trust. The counts are the
 * part a join or a filter gets wrong without failing: a fan-out, a literal
 * that matches nothing.
 */

import type { SnippetValidationModel } from "../api/types";
import { Rows } from "./Rows";

export interface SnippetCheckProps {
  validation: SnippetValidationModel;
  stale: boolean;
  /** Offered when the SQL uses tables the form does not list. */
  onUseTables?: (tables: string[]) => void;
  listed: readonly string[];
}

function counts(validation: SnippetValidationModel): string | null {
  const { rows_before: before, rows_after: after, kind } = validation;
  if (before === null || after === null) return null;
  return kind === "join"
    ? `${before.toLocaleString()} rows before the join, ${after.toLocaleString()} after`
    : `${after.toLocaleString()} of ${before.toLocaleString()} rows match`;
}

export function SnippetCheck({ validation, stale, onUseTables, listed }: SnippetCheckProps) {
  const summary = counts(validation);
  const unlisted = validation.tables.some((table) => !listed.includes(table));
  return (
    <div
      className={`validation ${validation.valid ? "validation-ok" : "validation-failed"}`}
      role={validation.valid ? "status" : "alert"}
      aria-label="Validation result"
    >
      {stale && <p className="notice notice-quiet">Edited since it was validated. Validate again before saving.</p>}
      {validation.valid ? (
        <p>
          <strong>It runs.</strong>
          {summary ? ` ${summary}` : ""}
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
      {validation.tables.length > 0 && (
        <p className="muted">
          Uses {validation.tables.join(", ")}.
          {unlisted && onUseTables && (
            <>
              {" "}
              <button type="button" className="button button-quiet" onClick={() => onUseTables(validation.tables)}>
                List these tables
              </button>
            </>
          )}
        </p>
      )}
      {validation.probe_sql && (
        <details className="probe">
          <summary>The query it was checked inside</summary>
          <pre className="mono">{validation.probe_sql}</pre>
        </details>
      )}
      <Rows columns={validation.columns} rows={validation.rows} />
    </div>
  );
}
