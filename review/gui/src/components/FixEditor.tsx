/**
 * Fixing a wrong or incomplete answer.
 *
 * The reviewer writes the query the agent should have generated. The box
 * starts with the agent's own SQL, because most fixes are an edit -- a
 * missing join, a label beside an id -- rather than a rewrite, and a
 * reviewer retyping the parts that were right is a reviewer introducing
 * new mistakes.
 *
 * Nothing is stored until the query has been run against the live retail
 * database and passed. The save button is bound to the *exact text* that
 * passed: one keystroke after validating and it is disabled again, because
 * "it validated a moment ago" is not the same claim as "this validated".
 * The server runs it once more on save regardless -- this button is a
 * courtesy, and the rule is enforced where it cannot be talked around.
 */

import type { FixKind, ValidationModel } from "../api/types";

export interface FixEditorProps {
  kind: FixKind;
  sql: string;
  onChange: (sql: string) => void;
  validation: ValidationModel | null;
  /** True when the SQL has changed since `validation` was run. */
  stale: boolean;
  onValidate: () => void;
  onSave: () => void;
  canSave: boolean;
  busy?: boolean;
}

const STORE: Record<FixKind, { title: string; action: string; explain: string }> = {
  corrections: {
    title: "The query that should have been generated",
    action: "Add to corrections",
    explain:
      "The answer was marked wrong. Write the SQL that answers the question; it is run against " +
      "the live retail database, and only a query that runs can be added to the corrections store.",
  },
  completions: {
    title: "The query that would have been complete",
    action: "Add to completions",
    explain:
      "The answer was marked correct but incomplete. Write the SQL that also carries what was " +
      "missing; it is run against the live retail database, and only a query that runs can be " +
      "added to the completions store.",
  },
};

function cell(value: unknown): string {
  return value === null || value === undefined ? "NULL" : String(value);
}

export function FixEditor({
  kind,
  sql,
  onChange,
  validation,
  stale,
  onValidate,
  onSave,
  canSave,
  busy = false,
}: FixEditorProps) {
  const words = STORE[kind];
  return (
    <section className="fix" aria-label={words.title}>
      <h3>{words.title}</h3>
      <p className="muted">{words.explain}</p>

      <div className="field">
        <label htmlFor="fix-sql">Corrected SQL</label>
        <textarea
          id="fix-sql"
          className="input mono"
          rows={10}
          value={sql}
          disabled={busy}
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>

      <div className="actions">
        <button type="button" className="button" disabled={busy || !sql.trim()} onClick={onValidate}>
          Validate against the live database
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !canSave}
          title={canSave ? `Store this fix in the ${kind} store` : "Validate the SQL first; it has to pass"}
          onClick={onSave}
        >
          {words.action}
        </button>
      </div>

      {validation && (
        <div
          className={`validation ${validation.valid ? "validation-ok" : "validation-failed"}`}
          role={validation.valid ? "status" : "alert"}
          aria-label="Validation result"
        >
          {stale && (
            <p className="notice notice-quiet">
              Edited since it was validated. Validate again before adding it.
            </p>
          )}
          {validation.valid ? (
            <p>
              <strong>It runs.</strong> {validation.row_count}
              {validation.truncated ? "+" : ""} row{validation.row_count === 1 ? "" : "s"}
              {validation.plan_cost !== null ? ` · plan cost ${validation.plan_cost}` : ""} ·{" "}
              {validation.elapsed_ms} ms
            </p>
          ) : (
            <>
              <p>
                <strong>It cannot be stored.</strong>
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
          {validation.columns.length > 0 && (
            <div className="result-table">
              <table>
                <thead>
                  <tr>
                    {validation.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {validation.rows.map((row, index) => (
                    <tr key={index}>
                      {row.map((value, column) => (
                        <td key={column}>{cell(value)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {validation.row_count > validation.rows.length && (
                <p className="muted">
                  First {validation.rows.length} of {validation.row_count}
                  {validation.truncated ? "+" : ""} rows.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
