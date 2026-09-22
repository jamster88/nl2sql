/**
 * The store, as a hook.
 *
 * `useSyncExternalStore` rather than `useState` because the store is shared:
 * the answer panel and the history list both show the same verdict, and a
 * click in one has to move the other. It also gets the tearing case right
 * for free, which matters the moment React renders the two in one pass.
 */

import { useCallback, useMemo, useSyncExternalStore } from "react";

import type { FeedbackRecord, FeedbackStore, Verdict } from "./store";

export interface UseFeedback {
  verdict: Verdict | undefined;
  record: FeedbackRecord | undefined;
  vote: (verdict: Verdict) => void;
  withdraw: () => void;
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

  return { verdict: record?.verdict, record, vote, withdraw };
}
