/**
 * The store, as a hook.
 *
 * `useSyncExternalStore` rather than `useState` because the store is shared:
 * the answer panel and the history list both show the same verdict, and a
 * click in one has to move the other. It also gets the tearing case right
 * for free, which matters the moment React renders the two in one pass.
 */

import { useCallback, useMemo, useSyncExternalStore } from "react";

import type { ApiFeedbackStore } from "./apiStore";
import type { FeedbackRecord, FeedbackStore, SyncState, Verdict } from "./store";

export interface UseFeedback {
  verdict: Verdict | undefined;
  record: FeedbackRecord | undefined;
  sync: SyncState | undefined;
  error: string | undefined;
  vote: (verdict: Verdict) => void;
  withdraw: () => void;
  /** Undefined when the store does not send anywhere, so no box is drawn. */
  comment: ((text: string) => void) | undefined;
  /** Undefined for the same reason: nothing local can fail to send. */
  retry: (() => void) | undefined;
}

/** Whether this store is the one that POSTs, without asking the caller. */
function sends(store: FeedbackStore): store is ApiFeedbackStore {
  return typeof (store as Partial<ApiFeedbackStore>).setWithComment === "function";
}

export function useFeedbackRecords(store: FeedbackStore): FeedbackRecord[] {
  return useSyncExternalStore(store.subscribe, store.all, store.all);
}

export function useFeedback(
  store: FeedbackStore,
  jobId: string | undefined,
  question: string,
): UseFeedback {
  const records = useFeedbackRecords(store);
  const record = useMemo(
    () => (jobId === undefined ? undefined : records.find((entry) => entry.jobId === jobId)),
    [records, jobId],
  );

  const vote = useCallback(
    (verdict: Verdict) => {
      if (jobId === undefined) return;
      // Clicking the verdict already recorded withdraws it, which is how
      // every other pair of toggle buttons behaves and saves the user
      // hunting for an undo.
      if (store.get(jobId)?.verdict === verdict) store.clear(jobId);
      else store.set(jobId, question, verdict);
    },
    [store, jobId, question],
  );

  const withdraw = useCallback(() => {
    if (jobId !== undefined) store.clear(jobId);
  }, [store, jobId]);

  const comment = useCallback(
    (text: string) => {
      if (jobId === undefined) return;
      const verdict = store.get(jobId)?.verdict;
      // A comment without a verdict has nothing to attach to: the staging
      // row is keyed on the job and its verdict is NOT NULL, so there is
      // no row to amend until one has been voted on.
      if (verdict === undefined || !sends(store)) return;
      store.setWithComment(jobId, question, verdict, text);
    },
    [store, jobId, question],
  );

  const retry = useCallback(() => {
    if (jobId !== undefined && sends(store)) store.retry(jobId);
  }, [store, jobId]);

  return {
    verdict: record?.verdict,
    record,
    sync: record?.sync,
    error: record?.error,
    vote,
    withdraw,
    comment: sends(store) ? comment : undefined,
    retry: sends(store) ? retry : undefined,
  };
}
