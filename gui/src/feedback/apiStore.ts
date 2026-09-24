/**
 * The store that sends the verdict somewhere.
 *
 * `store.ts` said the next version would be "a new `createApiFeedbackStore`
 * and one line in `App.tsx`". This is that, and the shape it predicted held:
 * nothing above here learns that a network is involved, because `set` and
 * `clear` were already the only methods that mutate and they still return
 * the moment the click is recorded.
 *
 * That last part is the design. The POST is *not* awaited before the button
 * changes state. A verdict is a courtesy the user is doing us, and making
 * them watch a spinner for it is how a feedback system stops collecting
 * feedback. So the local store is written first and is what the interface
 * reads, the request goes out behind it, and the record's `sync` field
 * carries what happened -- which is why that field exists rather than the
 * failure being swallowed or thrown at a user who has already moved on.
 *
 * The local store underneath is not a cache. It is the same `localStorage`
 * store as before, so a verdict given while the server was unreachable is
 * still on screen after a reload, still marked as unsent, and still the
 * user's own record of what they thought.
 */

import { createFeedbackStore, type FeedbackRecord, type FeedbackStore, type Verdict } from "./store";

/** Just the two calls this needs, so a test can pass an object literal. */
export interface FeedbackClient {
  submitFeedback: (
    id: string,
    verdict: Verdict,
    comment?: string,
    signal?: AbortSignal,
  ) => Promise<unknown>;
  withdrawFeedback: (id: string, signal?: AbortSignal) => Promise<unknown>;
}

export interface ApiFeedbackOptions {
  client: FeedbackClient;
  /** The mirror. Defaults to the browser-backed store. */
  local?: FeedbackStore;
  /** Called with every send failure, for a status bar that wants to say so. */
  onError?: (error: Error, jobId: string) => void;
  /**
   * Whether this server accepts feedback, read fresh on every vote.
   *
   * A function rather than a boolean because `/v1/meta` answers after the
   * store is built, and a store that captured the value at construction
   * would decide "no" before it had been told. When it says no, a verdict
   * is recorded locally and marked `local` -- not `failed`, which would put
   * "not sent" under every answer on a server that was never going to take
   * it and is not a failure of anything.
   */
  enabled?: () => boolean;
}

export interface ApiFeedbackStore extends FeedbackStore {
  /**
   * Record a verdict and a comment together.
   *
   * Separate from `set` because the comment arrives later than the verdict:
   * the user clicks Yes or No first and may then explain. Both write the
   * same row, so the second call is an amendment rather than a second
   * opinion.
   */
  setWithComment: (jobId: string, question: string, verdict: Verdict, comment: string) => FeedbackRecord;
  /** Send a verdict that previously failed to send. */
  retry: (jobId: string) => void;
}

export function createApiFeedbackStore(options: ApiFeedbackOptions): ApiFeedbackStore {
  const local = options.local ?? createFeedbackStore();
  const { client, onError } = options;
  const enabled = options.enabled ?? (() => true);

  /** The comment last sent for a job, so a retry resends it too. */
  const comments = new Map<string, string>();

  function send(jobId: string, question: string, verdict: Verdict, comment: string): void {
    if (!enabled()) {
      local.set(jobId, question, verdict, "local");
      return;
    }
    local.set(jobId, question, verdict, "saving");
    client
      .submitFeedback(jobId, verdict, comment)
      .then(() => {
        // Only if the record is still the one that was sent. A user who
        // changed their mind while the request was in flight has a newer
        // verdict on screen, and overwriting it with the older one's
        // success would show them an answer they had already replaced.
        const current = local.get(jobId);
        if (current?.verdict === verdict) local.set(jobId, question, verdict, "saved");
      })
      .catch((cause: unknown) => {
        const error = cause instanceof Error ? cause : new Error(String(cause));
        const current = local.get(jobId);
        if (current?.verdict === verdict) {
          local.set(jobId, question, verdict, "failed", error.message);
        }
        onError?.(error, jobId);
      });
  }

  return {
    get: local.get,
    all: local.all,
    subscribe: local.subscribe,

    set(jobId, question, verdict) {
      const record = local.set(jobId, question, verdict, "saving");
      comments.set(jobId, "");
      send(jobId, question, verdict, "");
      return record;
    },

    setWithComment(jobId, question, verdict, comment) {
      const record = local.set(jobId, question, verdict, "saving");
      comments.set(jobId, comment);
      send(jobId, question, verdict, comment);
      return record;
    },

    clear(jobId) {
      const had = local.get(jobId);
      local.clear(jobId);
      comments.delete(jobId);
      if (!had || !enabled()) return;
      client.withdrawFeedback(jobId).catch((cause: unknown) => {
        // A withdrawal that does not reach the server leaves a row staged
        // that the user believes they took back. It is reported rather than
        // ignored -- but the local record stays gone, because re-showing a
        // verdict someone just withdrew would be the more confusing lie.
        onError?.(cause instanceof Error ? cause : new Error(String(cause)), jobId);
      });
    },

    retry(jobId) {
      const record = local.get(jobId);
      if (record === undefined || record.sync !== "failed") return;
      send(jobId, record.question, record.verdict, comments.get(jobId) ?? "");
    },
  };
}
