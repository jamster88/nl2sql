/**
 * Asking a question, as a piece of state.
 *
 * A question is a job, and a job has a life: submitted, queued behind
 * whatever else the server is answering, running through fourteen pipeline
 * nodes, then finished or failed. The UI needs all of that, which is more
 * than one boolean, so it is a reducer rather than five `useState` calls
 * that can disagree with each other.
 *
 * The one rule worth stating: a stream that arrives for a question the user
 * has already moved on from is dropped, not rendered. Without the check a
 * slow first answer overwrites a fast second one, which looks exactly like
 * the agent answering the wrong question.
 */

import { useCallback, useEffect, useReducer, useRef } from "react";

import { ApiError, type Client } from "./client";
import { watchJob, type JobWatch, type JobWatchOptions } from "./events";
import type { Job, JobStatus, ProgressEvent } from "./types";

export type Phase = "idle" | "asking" | "streaming" | "finished";

export interface AskState {
  phase: Phase;
  job: Job | null;
  /** Steps so far, in order. Kept apart from `job` so it fills in live. */
  progress: ProgressEvent[];
  status: JobStatus | null;
  error: Error | null;
  /** Something the user should know that is not a failure. */
  notice: string | null;
}

export interface UseAsk extends AskState {
  busy: boolean;
  ask: (question: string) => void;
  cancel: () => void;
  reset: () => void;
}

type Action =
  | { type: "ask" }
  | { type: "accepted"; job: Job }
  | { type: "progress"; event: ProgressEvent }
  | { type: "status"; status: JobStatus }
  | { type: "done"; job: Job }
  | { type: "failed"; error: Error }
  | { type: "notice"; message: string }
  | { type: "reset" };

const INITIAL: AskState = {
  phase: "idle",
  job: null,
  progress: [],
  status: null,
  error: null,
  notice: null,
};

export function reduce(state: AskState, action: Action): AskState {
  switch (action.type) {
    case "ask":
      return { ...INITIAL, phase: "asking" };
    case "accepted":
      return { ...state, phase: "streaming", job: action.job, status: action.job.status };
    case "progress":
      // The server numbers events and a reconnect can resend one, so the
      // seq is the identity rather than the arrival order.
      return state.progress.some((event) => event.seq === action.event.seq)
        ? state
        : { ...state, progress: [...state.progress, action.event].sort((a, b) => a.seq - b.seq) };
    case "status":
      return { ...state, status: action.status };
    case "done":
      return {
        ...state,
        phase: "finished",
        job: action.job,
        status: action.job.status,
        progress: action.job.progress.length > 0 ? action.job.progress : state.progress,
      };
    case "failed":
      return { ...state, phase: "finished", error: action.error };
    case "notice":
      return { ...state, notice: action.message };
    case "reset":
      return INITIAL;
  }
}

export interface UseAskOptions {
  /** Passed through to the watcher; the tests use it to supply an EventSource. */
  watch?: JobWatchOptions;
  /** Called once per finished job, for a history list. */
  onFinished?: (job: Job) => void;
}

export function useAsk(client: Client, options: UseAskOptions = {}): UseAsk {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const watching = useRef<JobWatch | null>(null);
  // Which question is current, counted rather than named: the id is not
  // known until the POST comes back, and the race this guards against is
  // two POSTs in flight at once, where the *second* can answer first.
  // Keying off the id would then let the first one's answer win.
  const attempt = useRef(0);
  const onFinished = useRef(options.onFinished);
  onFinished.current = options.onFinished;

  const stop = useCallback(() => {
    watching.current?.close();
    watching.current = null;
  }, []);

  // A watch left open after the component goes away keeps an HTTP connection
  // and a timer alive, and in a browser that survives navigation.
  useEffect(() => stop, [stop]);

  const ask = useCallback(
    (question: string) => {
      const trimmed = question.trim();
      if (!trimmed) return;
      stop();
      const mine = (attempt.current += 1);
      dispatch({ type: "ask" });

      client
        .ask(trimmed)
        .then((job) => {
          // Superseded while the POST was in flight. `stop()` could not have
          // closed this watch, because it did not exist yet -- so the only
          // place to refuse it is here, before one is opened.
          if (mine !== attempt.current) return;
          dispatch({ type: "accepted", job });
          watching.current = watchJob(
            client,
            job,
            {
              onProgress: (event) => dispatch({ type: "progress", event }),
              onStatus: (status) => dispatch({ type: "status", status }),
              onDone: (finished) => {
                dispatch({ type: "done", job: finished });
                onFinished.current?.(finished);
              },
              onError: (error) => dispatch({ type: "notice", message: error.message }),
              onFallback: (reason) => dispatch({ type: "notice", message: reason }),
            },
            options.watch ?? {},
          );
        })
        .catch((cause: unknown) => {
          if (mine !== attempt.current) return;
          dispatch({
            type: "failed",
            error:
              cause instanceof ApiError || cause instanceof Error
                ? cause
                : new Error(String(cause)),
          });
        });
    },
    // `options.watch` is configuration, fixed for the life of the component.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, stop],
  );

  const cancel = useCallback(() => {
    const job = state.job;
    stop();
    attempt.current += 1;
    if (job && job.status !== "succeeded") void client.cancel(job.id).catch(() => undefined);
    dispatch({ type: "reset" });
  }, [client, state.job, stop]);

  const reset = useCallback(() => {
    stop();
    attempt.current += 1;
    dispatch({ type: "reset" });
  }, [stop]);

  return {
    ...state,
    busy: state.phase === "asking" || state.phase === "streaming",
    ask,
    cancel,
    reset,
  };
}
