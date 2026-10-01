/**
 * What reopening or deleting a submission actually did.
 *
 * A panel like `Promoted` and `Fixed`, and for their reason: taking a pair
 * out of the golden set has the same two halves as putting one in. The pair
 * leaving the question document is the fact; the stores catching up is a
 * reload that can fail on its own, and then the honest report is "out of the
 * golden set, still retrievable until the loaders run".
 */

import type { UndoModel, WithdrawalModel } from "../api/types";

export interface WithdrawnProps {
  result: UndoModel;
  onDismiss: () => void;
}

function whatCameOut(withdrawn: WithdrawalModel): string {
  const from = withdrawn.kind === "golden" ? "the golden set" : `the ${withdrawn.kind} store`;
  if (!withdrawn.found) return `${withdrawn.id} was no longer in ${from}; there was nothing to take out.`;
  if (withdrawn.kind === "golden") {
    return `${withdrawn.id} taken out of the golden set — ${withdrawn.pairs_before} → ${withdrawn.pairs_after} pairs.`;
  }
  return `${withdrawn.id} deleted from the ${withdrawn.kind} store, with its vector.`;
}

export function Withdrawn({ result, onDismiss }: WithdrawnProps) {
  const { withdrawn } = result;
  const golden = withdrawn?.kind === "golden";
  const caughtUp = !golden || withdrawn.reloaded;

  return (
    <section
      className={`promoted ${caughtUp ? "promoted-complete" : "promoted-partial"}`}
      aria-label="Undo result"
      role="status"
    >
      <header className="promoted-head">
        <h3>{result.action === "deleted" ? "Submission deleted" : "Back in the queue as pending"}</h3>
        <button type="button" className="button button-quiet" onClick={onDismiss}>
          Close
        </button>
      </header>

      {withdrawn && <p>{whatCameOut(withdrawn)}</p>}

      {golden && withdrawn.backup && (
        <p className="muted">
          Written to <code>{withdrawn.document}</code> · previous version kept at <code>{withdrawn.backup}</code>
        </p>
      )}

      {golden && (
        <ul className="steps">
          {withdrawn.steps.map((step) => (
            <li key={step.name} className={step.ran ? (step.ok ? "step-ok" : "step-failed") : "step-skipped"}>
              <strong>{step.name}</strong> {!step.ran ? "skipped" : step.ok ? "reloaded" : "failed"}
              {step.detail ? <span className="muted"> — {step.detail}</span> : null}
            </li>
          ))}
        </ul>
      )}

      {!caughtUp && (
        <p className="notice notice-quiet">
          The question document no longer holds the pair, which is what the golden set <em>is</em>.
          The retrieval stores have not caught up, so the agent can still retrieve it until{" "}
          <code>rag/05_load_golden_pairs.py</code> and <code>rag/06_embed_golden_pairs.py</code> run
          — or until the next promotion, which runs them.
        </p>
      )}
    </section>
  );
}
