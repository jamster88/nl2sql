/**
 * What saving a fix actually did.
 *
 * A panel rather than a toast for the same reason as `Promoted`: saving has
 * two outcomes that both count as success and must not look the same. The
 * record is always in the store -- that is the fact. The embedding that
 * makes it retrievable is a separate step that can fail on its own, and
 * then the honest report is "stored, not yet retrievable; the next save
 * that reaches the embedding host will catch it up".
 */

import type { FixResultModel } from "../api/types";

export interface FixedProps {
  result: FixResultModel;
  onDismiss: () => void;
}

export function Fixed({ result, onDismiss }: FixedProps) {
  return (
    <section
      className={`promoted ${result.embedded ? "promoted-complete" : "promoted-partial"}`}
      aria-label="Fix result"
      role="status"
    >
      <header className="promoted-head">
        <h3>
          {result.fix.fix_id} added to the {result.kind} store
          <span className="muted">
            {" "}
            — {result.fix.corrected_row_count}
            {result.fix.corrected_truncated ? "+" : ""} rows validated
          </span>
        </h3>
        <button type="button" className="button button-quiet" onClick={onDismiss}>
          Close
        </button>
      </header>
      <p className="muted">
        Its RAG entry: {result.embedded ? "embedded" : "not embedded yet"} — {result.embed_detail}
      </p>
      {!result.embedded && (
        <p className="notice notice-quiet">
          The fix is stored, so nothing has been lost. Its question has no vector yet, so nothing
          can retrieve it; the next fix saved while the embedding host is reachable embeds it too.
        </p>
      )}
    </section>
  );
}
