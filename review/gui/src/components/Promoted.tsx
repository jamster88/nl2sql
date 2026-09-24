/**
 * What a promotion actually did.
 *
 * The reason this is a panel and not a toast: a promotion has two outcomes
 * that both count as success and must not look the same. The pair is always
 * written to the question document -- that is the source of truth and the
 * part that cannot be half-done. Reloading the context store and the vectors
 * is a separate thing that can fail on its own, and when it does the honest
 * report is "it is in the golden set, the agent cannot retrieve it yet, here
 * is what to run". A green tick would be a lie and a red cross would be a
 * bigger one.
 */

import type { PromotionModel } from "../api/types";

export interface PromotedProps {
  promotion: PromotionModel;
  onDismiss: () => void;
}

export function Promoted({ promotion, onDismiss }: PromotedProps) {
  return (
    <section
      className={`promoted ${promotion.reloaded ? "promoted-complete" : "promoted-partial"}`}
      aria-label="Promotion result"
      role="status"
    >
      <header className="promoted-head">
        <h3>
          {promotion.pair_id} added to the golden set
          <span className="muted"> — {promotion.pairs_before} → {promotion.pairs_after} pairs</span>
        </h3>
        <button type="button" className="button button-quiet" onClick={onDismiss}>
          Close
        </button>
      </header>

      <p className="muted">
        Written to <code>{promotion.document}</code>
        {promotion.backup ? (
          <>
            {" "}
            · previous version kept at <code>{promotion.backup}</code>
          </>
        ) : null}
        {promotion.suite ? ` · suite "${promotion.suite}"` : ""}
      </p>

      <ul className="steps">
        {promotion.steps.map((step) => (
          <li key={step.name} className={step.ran ? (step.ok ? "step-ok" : "step-failed") : "step-skipped"}>
            <strong>{step.name}</strong>{" "}
            {!step.ran ? "skipped" : step.ok ? "reloaded" : "failed"}
            {step.detail ? <span className="muted"> — {step.detail}</span> : null}
          </li>
        ))}
      </ul>

      {!promotion.reloaded && (
        <p className="notice notice-quiet">
          The pair is in the question document, which is what the golden set <em>is</em>, so
          nothing has been lost. The retrieval stores have not caught up, so the agent will not
          retrieve it yet. Re-run <code>rag/05_load_golden_pairs.py</code> and then{" "}
          <code>rag/06_embed_golden_pairs.py</code>, or promote again once the cause is fixed.
        </p>
      )}
    </section>
  );
}
