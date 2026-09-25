/**
 * Was that right?
 *
 * Two buttons, and -- once one is pressed -- somewhere to say why. The
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
 * "No" on a good answer should not have to hunt for an undo, and a verdict
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
            {verdict === "no" ? "What was wrong?" : "Anything worth noting?"}
            <span className="muted"> (optional)</span>
          </label>
          <div className="feedback-row">
            <input
              id="feedback-comment"
              className="input"
              type="text"
              value={comment}
              placeholder={
                verdict === "no"
                  ? "e.g. the fiscal month is off by one"
                  : "e.g. worth keeping as an example"
              }
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
