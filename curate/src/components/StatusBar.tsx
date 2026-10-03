/**
 * What the service is, and what is wrong with it.
 *
 * The warnings matter here as much as in the review interface: this page
 * writes what the agent is shown and what it is measured against, and the
 * configurations most likely to be wrong -- no token, a loader switched off
 * -- are invisible from the screen otherwise.
 */

import { counted } from "../api/text";
import type { ReviewMeta } from "../api/types";

export interface StatusBarProps {
  meta: ReviewMeta | null;
  error: string | null;
}

export function StatusBar({ meta, error }: StatusBarProps) {
  return (
    <footer className="statusbar">
      {error && <span className="status status-bad">Cannot reach the review service: {error}</span>}
      {meta && (
        <>
          <span className="status">
            {meta.service} {meta.version}
          </span>
          <span className="status muted">{counted(meta.golden_count, "golden pair")}</span>
          <span className="status muted">
            {counted(meta.fixes.corrections ?? 0, "correction")} · {counted(meta.fixes.completions ?? 0, "completion")}
          </span>
          <span className="status muted">auth {meta.authentication}</span>
          {meta.warnings.map((warning) => (
            <span className="status status-warn" key={warning}>
              {warning}
            </span>
          ))}
        </>
      )}
    </footer>
  );
}
