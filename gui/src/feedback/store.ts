/**
 * Was that answer right? Correct, wrong, or correct but incomplete.
 *
 * This version keeps the answer in the browser and does nothing else with
 * it. That is the whole requirement for now, but it is worth being careful
 * about *where* it is kept, because the next version sends it somewhere and
 * the difference between an easy change and an awkward one is whether the
 * components ever learned that `localStorage` was involved.
 *
 * So: one interface, one implementation behind it, and nothing above it that
 * knows which. Swapping in a version that POSTs to the API is then a new
 * `createApiFeedbackStore` and one line in `App.tsx` -- and because the
 * methods that will become asynchronous are already the only ones that
 * mutate, they can return promises without any caller changing shape.
 *
 * The records carry the question as well as the job id on purpose. A job is
 * forgotten after `API_JOB_TTL_SECONDS`, so a verdict that only held an id
 * would become an orphan pointing at nothing an hour later.
 */

/**
 * `yes` is correct and `no` is wrong, the two wire values the API started
 * with; `incomplete` is correct but incomplete. Kept in step with
 * `Verdict` in `api/types.ts`, which is what the server accepts.
 */
export type Verdict = "yes" | "no" | "incomplete";

const VERDICTS: readonly Verdict[] = ["yes", "no", "incomplete"];

/**
 * Where a verdict has got to.
 *
 * `local` is the honest answer for the browser-only store: it was recorded
 * here and was never going anywhere else. The other three belong to the
 * store that POSTs, and exist so the interface can say "your click was
 * heard but the server has not confirmed it" instead of looking identical
 * to success. A verdict that silently failed to send is worse than one that
 * was never offered -- the user believes they have reported the problem.
 */
export type SyncState = "local" | "saving" | "saved" | "failed";

export interface FeedbackRecord {
  jobId: string;
  question: string;
  verdict: Verdict;
  /** ISO 8601, so the record survives JSON without a revival step. */
  at: string;
  sync?: SyncState;
  /** Why it did not send. Present only with `sync: "failed"`. */
  error?: string;
}

export interface FeedbackStore {
  get: (jobId: string) => FeedbackRecord | undefined;
  /**
   * Record a verdict, or replace one. Returns what was stored.
   *
   * `sync` is how the API-backed store moves a record through "saving" to
   * "saved" without a second method: `set` already replaces by job id and
   * already notifies, so re-setting the same verdict with a new status is
   * the whole of it.
   */
  set: (jobId: string, question: string, verdict: Verdict, sync?: SyncState, error?: string) => FeedbackRecord;
  /** Withdraw a verdict, for the misclick. */
  clear: (jobId: string) => void;
  /** Newest first. */
  all: () => FeedbackRecord[];
  subscribe: (listener: () => void) => () => void;
}

/** One key, one JSON object, versioned so a later shape can be recognised. */
export const STORAGE_KEY = "nl2sql.feedback.v1";

/** Enough to be useful in a session, small enough to never be a problem. */
export const MAX_RECORDS = 200;

function isRecord(value: unknown): value is FeedbackRecord {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<FeedbackRecord>;
  return (
    typeof candidate.jobId === "string" &&
    typeof candidate.question === "string" &&
    typeof candidate.at === "string" &&
    VERDICTS.includes(candidate.verdict as Verdict)
  );
}

export function createFeedbackStore(
  storage: Storage | undefined = globalThis.localStorage,
  now: () => Date = () => new Date(),
): FeedbackStore {
  const listeners = new Set<() => void>();
  let records: FeedbackRecord[] = read();

  function read(): FeedbackRecord[] {
    // Every one of these can throw rather than return nothing: Safari's
    // private mode throws on read, a browser told to block site data throws
    // on access, and a half-written value throws in the parser. None of
    // them is a reason for the application not to open.
    try {
      const raw = storage?.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter(isRecord) : [];
    } catch {
      return [];
    }
  }

  function write(): void {
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify(records));
    } catch {
      // Out of quota, or not allowed to store anything. The verdict still
      // shows for this session; it just will not outlive the tab.
    }
  }

  function announce(): void {
    write();
    for (const listener of listeners) listener();
  }

  return {
    get: (jobId) => records.find((record) => record.jobId === jobId),

    set(jobId, question, verdict, sync = "local", error) {
      const record: FeedbackRecord = {
        jobId,
        question,
        verdict,
        at: now().toISOString(),
        sync,
        ...(error === undefined ? {} : { error }),
      };
      records = [record, ...records.filter((existing) => existing.jobId !== jobId)].slice(0, MAX_RECORDS);
      announce();
      return record;
    },

    clear(jobId) {
      const remaining = records.filter((record) => record.jobId !== jobId);
      if (remaining.length === records.length) return;
      records = remaining;
      announce();
    },

    all: () => records,

    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
