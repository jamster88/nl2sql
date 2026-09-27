/**
 * Was that right?
 *
 * Three buttons -- correct, wrong, and correct but incomplete -- and, once
 * one is pressed, somewhere to say why. The third exists because "right"
 * was two answers: a query that was wrong needs correcting, and one that
 * was right but left out what a reader needed (a name beside an id, the
 * figure a ranking was ranked by) needs fleshing out, and a reviewer
 * cannot tell those apart from a "no". The
 * ordering is deliberate: the verdict is one click and is recorded on that
 * click, and the comment box only appears afterwards. Asking for prose up
 * front turns a one-click courtesy into a form, and a feedback widget that
 * looks like a form is one people scroll past.
 *
 * Nothing here knows where a verdict goes. It calls `onVote` on props it was
 * handed, which is how the same component serves the browser-only store and
 * the one that POSTs (see `feedback/apiStore.ts`).
 *
 * Clicking the verdict already recorded withdraws it. A user who misclicks
 * "Wrong" on a good answer should not have to hunt for an undo, and a verdict
 * that cannot be taken back is a verdict people stop giving.
 *
 * The send state is shown rather than hidden. A verdict that failed to reach
 * the server is the one case where saying nothing actively misleads: the
 * user believes they have reported the problem, and nobody has heard it.
 */

import { useEffect, useState } from "react";

import type { SyncState, Verdict } from "../feedback/store";

export interface FeedbackBarProps {
  verdict: Verdict | undefined;
  onVote: (verdict: Verdict) => void;
  /**
   * Called when the user submits a comment alongside the recorded verdict.
   * Undefined when the store does not send anywhere, and the box is then
   * not drawn at all -- a comment with nowhere to go is a lie to the user.
   */
  onComment?: ((comment: string) => void) | undefined;
  /** Called to resend a verdict whose last send failed. */
  onRetry?: (() => void) | undefined;
  /** Shown instead of the prompt once a verdict is recorded. */
  recordedAt?: string | undefined;
  sync?: SyncState | undefined;
  error?: string | undefined;
  disabled?: boolean;
}

/**
 * The three options, in the order a reader weighs them. The wire values are
 * the API's: `yes` and `no` predate the third and are kept, so every verdict
 * already recorded still means what it did.
 */
export const OPTIONS: readonly { verdict: Verdict; label: string }[] = [
  { verdict: "yes", label: "Correct" },
  { verdict: "no", label: "Wrong" },
  { verdict: "incomplete", label: "Correct but incomplete" },
];

/** What the comment box asks, which depends on what was wrong, if anything. */
const ASK: Record<Verdict, { label: string; placeholder: string }> = {
  yes: { label: "Anything worth noting?", placeholder: "e.g. worth keeping as an example" },
  no: { label: "What was wrong?", placeholder: "e.g. the fiscal month is off by one" },
  incomplete: { label: "What was missing?", placeholder: "e.g. the product names beside the SKUs" },
};

const NOTE: Record<SyncState, string> = {
  local: "click again to withdraw",
  saving: "sending...",
  saved: "sent for review",
  failed: "not sent",
};

export function FeedbackBar({
  verdict,
  onVote,
  onComment,
  onRetry,
  recordedAt,
  sync,
  error,
  disabled = false,
}: FeedbackBarProps) {
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  // A new verdict is a new opinion, so the comment box starts empty and
  // open again rather than still showing the last answer's note.
  useEffect(() => {
    setComment("");
    setSent(false);
  }, [verdict]);

  const state: SyncState = sync ?? "local";

  return (
    <div className="feedback" role="group" aria-label="Was this answer correct?">
      <div className="feedback-row">
        <span className="feedback-prompt">
          {verdict === undefined ? "Was this answer right?" : "Thanks — recorded as"}
        </span>
        {OPTIONS.map((option) => (
          <button
            key={option.verdict}
            type="button"
            className="button button-vote"
            aria-pressed={verdict === option.verdict}
            data-verdict={option.verdict}
            disabled={disabled}
            onClick={() => onVote(option.verdict)}
          >
            {option.label}
          </button>
        ))}
        {verdict !== undefined && (
          <span className="feedback-note muted" data-sync={state}>
            {recordedAt ? `${new Date(recordedAt).toLocaleTimeString()} · ` : ""}
            {NOTE[state]}
          </span>
        )}
        {state === "failed" && onRetry && (
          <button type="button" className="button button-quiet" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>

      {state === "failed" && error && (
        <p className="feedback-error" role="alert">
          {error}
        </p>
      )}

      {verdict !== undefined && onComment && !sent && (
        <form
          className="feedback-comment"
          onSubmit={(event) => {
            event.preventDefault();
            if (!comment.trim()) return;
            onComment(comment.trim());
            setSent(true);
          }}
        >
          <label className="feedback-comment-label" htmlFor="feedback-comment">
            {ASK[verdict].label}
            <span className="muted"> (optional)</span>
          </label>
          <div className="feedback-row">
            <input
              id="feedback-comment"
              className="input"
              type="text"
              value={comment}
              placeholder={ASK[verdict].placeholder}
              onChange={(event) => setComment(event.target.value)}
              disabled={disabled}
            />
            <button type="submit" className="button" disabled={disabled || !comment.trim()}>
              Send
            </button>
          </div>
        </form>
      )}

      {sent && <p className="feedback-note muted">Comment sent. Thank you.</p>}
    </div>
  );
}
