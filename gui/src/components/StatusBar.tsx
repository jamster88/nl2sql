/**
 * What this is connected to.
 *
 * Small, and at the bottom, because it is reference rather than news -- but
 * present, because "which model answered this" and "how many tables are in
 * scope" are the first two questions asked of any answer that looks wrong,
 * and an interface that cannot say is an interface people stop trusting.
 *
 * The failure case earns more room than the success case: a GUI that cannot
 * reach its API should say so in words, since every other part of the page
 * will simply look empty.
 */

import type { Meta } from "../api/types";

export interface StatusBarProps {
  meta: Meta | null;
  error: Error | null;
  warnings: readonly string[];
}

export function StatusBar({ meta, error, warnings }: StatusBarProps) {
  if (error) {
    return (
      <footer className="status status-error" role="alert">
        <strong>Not connected.</strong> {error.message}
        <span className="muted">
          {" "}
          Check that the API is running — <code>./launch.sh --gui</code>.
        </span>
      </footer>
    );
  }

  if (!meta) {
    return (
      <footer className="status" aria-busy="true">
        <span className="muted">Connecting…</span>
      </footer>
    );
  }

  return (
    <footer className="status">
      <span>
        <strong>{meta.service}</strong> {meta.version}
      </span>
      <span className="muted">{meta.model}</span>
      <span className="muted">
        {meta.tables.length} {meta.tables.length === 1 ? "table" : "tables"}
      </span>
      <span className="muted">
        {meta.authentication === "bearer" ? "token required" : "no token"}
      </span>
      {meta.scope && <span className="status-scope muted">{meta.scope}</span>}
      {warnings.map((warning) => (
        <span key={warning} className="status-warning">
          {warning}
        </span>
      ))}
    </footer>
  );
}
