/**
 * What this service is, and what is wrong with it.
 *
 * The warnings are not decoration. This application writes the question set
 * the agent is measured against, and the two configurations most likely to
 * be wrong -- no token, and reloading switched off -- are both invisible
 * from the screen otherwise. One of them means anybody can edit the
 * benchmark; the other means promotions silently do not reach the agent.
 */

import type { ReviewMeta } from "../api/types";

export interface StatusBarProps {
  meta: ReviewMeta | null;
  error: Error | null;
  warnings: readonly string[];
}

export function StatusBar({ meta, error, warnings }: StatusBarProps) {
  return (
    <footer className="statusbar">
      {error && (
        <span className="status status-bad">
          Cannot reach the review service: {error.message}
        </span>
      )}

      {meta && (
        <>
          <span className="status">
            {meta.service} {meta.version}
          </span>
          <span className="status muted">
            {meta.golden_count} golden pairs · next {meta.next_pair_id || "—"} · max Q
            {meta.limits.max_pair_number}
          </span>
          <span className="status muted">
            auth {meta.authentication} · reload context {meta.reload_context ? "on" : "off"} ·
            vectors {meta.reload_vectors ? "on" : "off"}
          </span>
        </>
      )}

      {warnings.map((warning) => (
        <span className="status status-warn" key={warning}>
          {warning}
        </span>
      ))}
    </footer>
  );
}
