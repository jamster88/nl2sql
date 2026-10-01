/**
 * Taking a judgement back.
 *
 * Two actions, for any submission. *Back to pending* returns it to the
 * queue, unjudged; *Delete* removes it for good. When it had been promoted
 * or fixed, either one takes the pair back out of the golden set, or the
 * fix out of its store, first -- the queue and what it produced are never
 * allowed to disagree, or a reopened pair would be promoted a second time.
 *
 * Anything with consequences beyond the staging table asks first, and says
 * what those consequences are in the words of the files and stores they
 * land in. Putting an accepted or rejected submission back changes nothing
 * else, so it does not.
 */

import { useState } from "react";

import type { SubmissionModel } from "../api/types";

export interface RecordEditorProps {
  submission: SubmissionModel;
  busy: boolean;
  onReopen: () => void;
  onDelete: () => void;
}

type Asking = "reopen" | "delete" | null;

function storeOf(submission: SubmissionModel): string {
  return submission.verdict === "incomplete" ? "completions" : "corrections";
}

/** What reopening will do, said before it is done. */
function reopening(submission: SubmissionModel): string {
  const id = submission.promoted_pair_id;
  if (submission.state === "promoted") {
    return (
      `Takes ${id} back out of the golden set — out of the question document, with the ` +
      "previous version kept beside it, and out of both stores — and puts this back in the " +
      "queue with the pair as its draft."
    );
  }
  if (submission.state === "corrected") {
    return (
      `Deletes ${id} and its vector from the ${storeOf(submission)} store, and puts this back ` +
      "in the queue with its corrected SQL ready to edit."
    );
  }
  return "Puts this back in the queue, unjudged. The draft and the note are kept.";
}

/** What deleting will do. */
function deleting(submission: SubmissionModel): string {
  const id = submission.promoted_pair_id;
  const forGood = "Deletes this submission from the staging database for good";
  if (submission.state === "promoted") return `${forGood}, and takes ${id} out of the golden set first.`;
  if (submission.state === "corrected") {
    return `${forGood}, and ${id} out of the ${storeOf(submission)} store first.`;
  }
  return `${forGood}. It cannot be brought back.`;
}

export function RecordEditor({ submission, busy, onReopen, onDelete }: RecordEditorProps) {
  const [asking, setAsking] = useState<Asking>(null);
  const finished = submission.state === "promoted" || submission.state === "corrected";
  const reopenable = submission.state !== "pending";

  function confirm(): void {
    setAsking(null);
    if (asking === "reopen") onReopen();
    else onDelete();
  }

  return (
    <section className="record" aria-label="Change this review">
      <h3 className="record-title">Change this review</h3>

      {asking === null ? (
        <>
          {reopenable && <p className="muted">{reopening(submission)}</p>}
          <div className="actions">
            {reopenable && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={finished ? () => setAsking("reopen") : onReopen}
              >
                Back to pending
              </button>
            )}
            <button type="button" className="button button-danger" disabled={busy} onClick={() => setAsking("delete")}>
              Delete…
            </button>
          </div>
        </>
      ) : (
        <div className="confirm" role="alertdialog" aria-label="Are you sure?">
          <p>
            <strong>{asking === "reopen" ? reopening(submission) : deleting(submission)}</strong>
          </p>
          <div className="actions">
            <button
              type="button"
              className={asking === "delete" ? "button button-danger" : "button button-primary"}
              disabled={busy}
              onClick={confirm}
            >
              {asking === "delete"
                ? "Delete for good"
                : submission.state === "promoted"
                  ? `Take ${submission.promoted_pair_id} out and reopen`
                  : `Delete ${submission.promoted_pair_id} and reopen`}
            </button>
            <button type="button" className="button button-quiet" onClick={() => setAsking(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
