/**
 * Was that right?
 *
 * Two buttons and nothing else. This version records the verdict in the
 * browser and shows it back; the next one sends it somewhere, which is why
 * nothing in here knows where it goes -- it calls `vote` on a store it was
 * handed (see `feedback/store.ts`).
 *
 * Clicking the verdict already recorded withdraws it. A user who misclicks
 * "No" on a good answer should not have to hunt for an undo, and a verdict
 * that cannot be taken back is a verdict people stop giving.
 */

import type { Verdict } from "../feedback/store";

export interface FeedbackBarProps {
  verdict: Verdict | undefined;
  onVote: (verdict: Verdict) => void;
  /** Shown instead of the prompt once a verdict is recorded. */
  recordedAt?: string | undefined;
  disabled?: boolean;
}

export function FeedbackBar({ verdict, onVote, recordedAt, disabled = false }: FeedbackBarProps) {
  return (
    <div className="feedback" role="group" aria-label="Was this answer correct?">
      <span className="feedback-prompt">
        {verdict === undefined ? "Was this answer right?" : "Thanks — recorded as"}
      </span>
      <button
        type="button"
        className="button button-vote"
        aria-pressed={verdict === "yes"}
        data-verdict="yes"
        disabled={disabled}
        onClick={() => onVote("yes")}
      >
        Yes
      </button>
      <button
        type="button"
        className="button button-vote"
        aria-pressed={verdict === "no"}
        data-verdict="no"
        disabled={disabled}
        onClick={() => onVote("no")}
      >
        No
      </button>
      {verdict !== undefined && (
        <span className="feedback-note muted">
          {recordedAt ? `${new Date(recordedAt).toLocaleTimeString()} · ` : ""}
          click again to withdraw
        </span>
      )}
    </div>
  );
}
