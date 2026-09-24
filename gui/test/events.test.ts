import { beforeEach, describe, expect, it, vi } from "vitest";

import { isTerminal, watchJob, type JobWatchHandlers } from "../src/api/events";
import type { Job, ProgressEvent } from "../src/api/types";
import { FakeEventSource, fakeClient, fakeEventSource, makeJob } from "./helpers";

/**
 * A hand-driven clock.
 *
 * The polling path is a `setTimeout` that reschedules itself, so a real
 * timer would either make the test slow or make it racy. Injecting the
 * scheduler makes "one more poll happened" an assertion rather than a wait.
 */
function scheduler() {
  let queued: (() => void)[] = [];
  return {
    setTimeout: ((fn: () => void) => {
      queued.push(fn);
      return queued.length;
    }) as unknown as typeof setTimeout,
    clearTimeout: (() => undefined) as unknown as typeof clearTimeout,
    pending: () => queued.length,
    async tick(): Promise<void> {
      const due = queued;
      queued = [];
      for (const fn of due) fn();
      // Two turns: the poll's own promise, then the handler it resolves into.
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    },
  };
}

function handlers(): JobWatchHandlers & {
  progress: ProgressEvent[];
  statuses: string[];
  finished: Job[];
  errors: Error[];
  fallbacks: string[];
} {
  const progress: ProgressEvent[] = [];
  const statuses: string[] = [];
  const finished: Job[] = [];
  const errors: Error[] = [];
  const fallbacks: string[] = [];
  return {
    progress,
    statuses,
    finished,
    errors,
    fallbacks,
    onProgress: (event) => progress.push(event),
    onStatus: (status) => statuses.push(status),
    onDone: (job) => finished.push(job),
    onError: (error) => errors.push(error),
    onFallback: (reason) => fallbacks.push(reason),
  };
}

const STEP = (seq: number): ProgressEvent => ({
  seq,
  step: `node_${seq}`,
  label: `step ${seq}`,
  detail: "",
  at: "2026-09-22T10:00:00Z",
});

beforeEach(() => {
  FakeEventSource.reset();
});

describe("isTerminal", () => {
  it("is true only for the three states that stop", () => {
    expect(["succeeded", "failed", "cancelled"].every((status) => isTerminal(status as Job["status"]))).toBe(true);
    expect(["queued", "running"].some((status) => isTerminal(status as Job["status"]))).toBe(false);
  });
});

describe("watchJob over the event stream", () => {
  it("delivers progress, status and the finished job", () => {
    const client = fakeClient();
    const job = makeJob({ status: "queued", answer: null, progress: [] });
    const sink = handlers();

    watchJob(client, job, sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();

    stream.emit("status", { status: "running" });
    stream.emit("progress", STEP(1));
    stream.emit("progress", STEP(2));
    stream.emit("done", makeJob({ progress: [STEP(1), STEP(2)] }));

    expect(sink.statuses).toEqual(["running"]);
    expect(sink.progress.map((event) => event.seq)).toEqual([1, 2]);
    expect(sink.finished).toHaveLength(1);
    expect(stream.closed).toBe(true);
  });

  it("opens the URL the client supplied", () => {
    const client = fakeClient();
    watchJob(client, makeJob({ id: "abc" }), handlers(), { eventSource: fakeEventSource });
    expect(FakeEventSource.last().url).toBe("/v1/questions/abc/events");
  });

  it("ignores an event it has already seen, so a resume does not redraw", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();

    stream.emit("progress", STEP(1));
    stream.emit("progress", STEP(1));
    stream.emit("progress", STEP(2));

    expect(sink.progress.map((event) => event.seq)).toEqual([1, 2]);
  });

  it("reports the finished job's own progress, not only what was streamed", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    FakeEventSource.last().emit("done", makeJob({ progress: [STEP(1), STEP(2), STEP(3)] }));
    expect(sink.progress.map((event) => event.seq)).toEqual([1, 2, 3]);
  });

  it("calls onDone exactly once even if the server sends it twice", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();
    stream.emit("done", makeJob());
    stream.emit("done", makeJob());
    expect(sink.finished).toHaveLength(1);
  });

  it("reconnects after a deliberate idle timeout, resuming from the last seq", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob({ id: "abc" }), sink, { eventSource: fakeEventSource });

    const first = FakeEventSource.last();
    first.emit("progress", STEP(4));
    first.emit("timeout", { message: "reconnect" });

    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.last().url).toBe("/v1/questions/abc/events?from_seq=4");
  });

  it("appends from_seq with & when the URL already carries a token", () => {
    const client = fakeClient({ eventsUrl: () => "/v1/questions/abc/events?access_token=t" });
    watchJob(client, makeJob(), handlers(), { eventSource: fakeEventSource });

    FakeEventSource.last().emit("progress", STEP(9));
    FakeEventSource.last().emit("timeout", {});

    expect(FakeEventSource.last().url).toBe("/v1/questions/abc/events?access_token=t&from_seq=9");
  });

  it("does not reconnect on a timeout that arrives after the job finished", () => {
    watchJob(fakeClient(), makeJob(), handlers(), { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();
    stream.emit("done", makeJob());
    stream.emit("timeout", {});
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("tolerates a single transport failure without giving up", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    FakeEventSource.last().fail();
    expect(sink.fallbacks).toEqual([]);
  });

  it("a delivered event forgives an earlier failure", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();

    stream.fail();
    stream.emit("progress", STEP(1));
    stream.fail();

    expect(sink.fallbacks).toEqual([]);
  });

  it("falls back to polling once the stream has failed twice", async () => {
    const clock = scheduler();
    const client = fakeClient({
      job: vi.fn().mockResolvedValue(makeJob({ status: "running", answer: null })),
    });
    const sink = handlers();

    watchJob(client, makeJob({ status: "running" }), sink, {
      eventSource: fakeEventSource,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    });

    const stream = FakeEventSource.last();
    stream.fail();
    stream.fail();
    await Promise.resolve();
    await Promise.resolve();

    expect(sink.fallbacks).toHaveLength(1);
    expect(sink.fallbacks[0]).toMatch(/polled instead/);
    expect(stream.closed).toBe(true);
    expect(client.job).toHaveBeenCalled();
  });

  it("ignores failures once the job has finished", () => {
    const sink = handlers();
    watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();
    stream.emit("done", makeJob());
    stream.fail();
    stream.fail();
    expect(sink.fallbacks).toEqual([]);
  });

  it("stops on close and delivers nothing that arrives afterwards", () => {
    const sink = handlers();
    const watch = watchJob(fakeClient(), makeJob(), sink, { eventSource: fakeEventSource });
    const stream = FakeEventSource.last();

    watch.close();
    watch.close(); // idempotent
    stream.emit("progress", STEP(1));
    stream.emit("status", { status: "running" });
    stream.emit("done", makeJob());
    stream.fail();

    expect(stream.closed).toBe(true);
    expect(sink.progress).toEqual([]);
    expect(sink.statuses).toEqual([]);
    expect(sink.finished).toEqual([]);
    expect(sink.fallbacks).toEqual([]);
  });
});

describe("watchJob without an event stream", () => {
  it("polls from the start when there is no EventSource", async () => {
    const clock = scheduler();
    const running = makeJob({ status: "running", answer: null, progress: [STEP(1)] });
    const finished = makeJob({ status: "succeeded", progress: [STEP(1), STEP(2)] });
    const job = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(finished);
    const sink = handlers();

    watchJob(fakeClient({ job }), makeJob({ status: "queued" }), sink, {
      eventSource: undefined,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(sink.statuses).toEqual(["running"]);
    expect(clock.pending()).toBe(1);

    await clock.tick();
    expect(sink.statuses).toEqual(["running", "succeeded"]);
    expect(sink.finished).toHaveLength(1);
    expect(sink.progress.map((event) => event.seq)).toEqual([1, 2]);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("keeps polling through an error and reports it", async () => {
    const clock = scheduler();
    const job = vi
      .fn()
      .mockRejectedValueOnce(new Error("gateway timeout"))
      .mockResolvedValueOnce(makeJob());
    const sink = handlers();

    watchJob(fakeClient({ job }), makeJob(), sink, {
      eventSource: undefined,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(sink.errors.map((error) => error.message)).toEqual(["gateway timeout"]);

    await clock.tick();
    expect(sink.finished).toHaveLength(1);
  });

  it("wraps a rejection that is not an Error", async () => {
    const clock = scheduler();
    const sink = handlers();
    watchJob(fakeClient({ job: vi.fn().mockRejectedValue("just a string") }), makeJob(), sink, {
      eventSource: undefined,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    });

    await Promise.resolve();
    await Promise.resolve();
    expect(sink.errors[0]?.message).toBe("just a string");
  });

  it("does not poll again after being closed mid-request", async () => {
    const clock = scheduler();
    const sink = handlers();
    const watch = watchJob(
      fakeClient({ job: vi.fn().mockResolvedValue(makeJob({ status: "running", answer: null })) }),
      makeJob(),
      sink,
      { eventSource: undefined, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout },
    );

    watch.close();
    await Promise.resolve();
    await Promise.resolve();

    expect(sink.statuses).toEqual([]);
    expect(clock.pending()).toBe(0);
  });

  it("stops polling after a close that lands between an error and its retry", async () => {
    const clock = scheduler();
    const sink = handlers();
    const watch = watchJob(
      fakeClient({ job: vi.fn().mockRejectedValue(new Error("nope")) }),
      makeJob(),
      sink,
      { eventSource: undefined, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout },
    );

    watch.close();
    await Promise.resolve();
    await Promise.resolve();

    expect(sink.errors).toEqual([]);
  });

  it("uses the global EventSource when the option is omitted entirely", () => {
    const original = globalThis.EventSource;
    globalThis.EventSource = fakeEventSource;
    try {
      watchJob(fakeClient(), makeJob(), handlers());
      expect(FakeEventSource.instances).toHaveLength(1);
    } finally {
      globalThis.EventSource = original;
    }
  });
});
