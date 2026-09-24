/**
 * Watching one question happen.
 *
 * The API offers three ways to find out how a job is going -- poll it,
 * stream it, or let the server hold the connection -- and a GUI wants the
 * stream, because a minute of "working..." is the difference between an
 * application that looks broken and one that looks busy.
 *
 * Streams break, though, and they break in a way that is easy to miss:
 * `EventSource` reconnects silently forever, so a proxy that buffers
 * `text/event-stream` (several do, by default) produces a spinner that never
 * moves and no error anywhere. So this watches the stream watching the job,
 * and when the stream has failed enough times to be called broken it falls
 * back to polling and says so. Polling is slower and duller and always
 * works; that is the right thing to degrade to.
 */

import type { Client } from "./client";
import type { Job, JobStatus, ProgressEvent } from "./types";

export interface JobWatchHandlers {
  /** One pipeline node finished. Called once per `seq`, in order. */
  onProgress?: (event: ProgressEvent) => void;
  onStatus?: (status: JobStatus) => void;
  /** The job reached a terminal state. Called exactly once. */
  onDone: (job: Job) => void;
  onError?: (error: Error) => void;
  /** The stream was abandoned and polling took over. */
  onFallback?: (reason: string) => void;
}

export interface JobWatchOptions {
  /** Injectable for tests, and absent in a non-browser host. */
  eventSource?: typeof EventSource | undefined;
  /** How long to wait between polls once streaming has been given up on. */
  pollIntervalMs?: number;
  /**
   * How many stream failures before falling back. Two rather than one: a
   * single reconnect is normal -- the server closes an idle stream on
   * purpose -- and giving up on the first one would abandon a working
   * stream every `API_EVENT_STREAM_TIMEOUT_SECONDS`.
   */
  maxStreamFailures?: number;
  setTimeout?: typeof setTimeout;
  clearTimeout?: typeof clearTimeout;
}

export interface JobWatch {
  /** Stop watching. Safe to call more than once, and after `onDone`. */
  close: () => void;
}

const TERMINAL: ReadonlySet<JobStatus> = new Set<JobStatus>(["succeeded", "failed", "cancelled"]);

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL.has(status);
}

export function watchJob(
  client: Client,
  job: Job,
  handlers: JobWatchHandlers,
  options: JobWatchOptions = {},
): JobWatch {
  const EventSourceImpl = "eventSource" in options ? options.eventSource : globalThis.EventSource;
  const pollIntervalMs = options.pollIntervalMs ?? 2000;
  const maxStreamFailures = options.maxStreamFailures ?? 2;
  const later = options.setTimeout ?? setTimeout;
  const cancelLater = options.clearTimeout ?? clearTimeout;

  let closed = false;
  let finished = false;
  let lastSeq = 0;
  let failures = 0;
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const controller = new AbortController();

  function emitProgress(events: readonly ProgressEvent[]): void {
    for (const event of events) {
      if (event.seq <= lastSeq) continue;
      lastSeq = event.seq;
      handlers.onProgress?.(event);
    }
  }

  function finish(final: Job): void {
    if (closed || finished) return;
    finished = true;
    emitProgress(final.progress);
    close();
    handlers.onDone(final);
  }

  function close(): void {
    if (closed) return;
    closed = true;
    source?.close();
    source = null;
    if (timer !== null) cancelLater(timer);
    timer = null;
    controller.abort();
  }

  // --- polling, the fallback and the no-EventSource path -----------------

  function poll(): void {
    client
      .job(job.id, { signal: controller.signal })
      .then((latest) => {
        if (closed) return;
        emitProgress(latest.progress);
        handlers.onStatus?.(latest.status);
        if (isTerminal(latest.status)) finish(latest);
        else timer = later(poll, pollIntervalMs);
      })
      .catch((error: unknown) => {
        if (closed) return;
        handlers.onError?.(error instanceof Error ? error : new Error(String(error)));
        timer = later(poll, pollIntervalMs);
      });
  }

  function fallBackToPolling(reason: string): void {
    source?.close();
    source = null;
    handlers.onFallback?.(reason);
    poll();
  }

  // --- streaming ---------------------------------------------------------

  // The implementation is passed in rather than closed over so the one
  // place that knows there is one -- the branch at the bottom of this
  // function -- is the only place that has to check.
  function connect(EventSourceImpl: typeof EventSource): void {
    const url = client.eventsUrl(job);
    // `from_seq` is belt and braces: a browser sends `Last-Event-ID` on its
    // own reconnect, but this is a fresh EventSource after a deliberate
    // close, which starts with no id to send.
    const resumed = lastSeq > 0 ? `${url}${url.includes("?") ? "&" : "?"}from_seq=${lastSeq}` : url;
    const stream = new EventSourceImpl(resumed);
    source = stream;

    // Each listener re-checks `closed`. A real `EventSource` stops calling
    // back the moment it is closed, so this only matters where one does not
    // -- but "a watch that was closed delivers nothing" is this module's
    // promise to make, and a consumer that has to re-check it will eventually
    // re-check it in three places and forget the fourth.
    stream.addEventListener("progress", (message) => {
      if (closed) return;
      failures = 0;
      emitProgress([JSON.parse((message as MessageEvent<string>).data) as ProgressEvent]);
    });

    stream.addEventListener("status", (message) => {
      if (closed) return;
      const payload = JSON.parse((message as MessageEvent<string>).data) as { status: JobStatus };
      handlers.onStatus?.(payload.status);
    });

    stream.addEventListener("done", (message) => {
      finish(JSON.parse((message as MessageEvent<string>).data) as Job);
    });

    stream.addEventListener("timeout", () => {
      // The server said it was closing an idle stream and to come back. That
      // is not a failure, so the failure count is left alone.
      stream.close();
      if (!closed && !finished) connect(EventSourceImpl);
    });

    stream.onerror = () => {
      if (closed || finished) return;
      failures += 1;
      if (failures >= maxStreamFailures) {
        fallBackToPolling(
          "the progress stream keeps dropping, so this question is being polled instead",
        );
      }
    };
  }

  if (EventSourceImpl === undefined) {
    // No EventSource at all: a test host, or a browser old enough that the
    // rest of this application would not run either. Poll from the start.
    poll();
  } else {
    connect(EventSourceImpl);
  }

  return { close };
}
