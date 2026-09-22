import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { reduce, useAsk } from "../src/api/useAsk";
import { ApiError, type Client } from "../src/api/client";
import type { ProgressEvent } from "../src/api/types";
import { FakeEventSource, fakeClient, fakeEventSource, makeJob } from "./helpers";

afterEach(cleanup);
beforeEach(() => {
  FakeEventSource.reset();
});

const STEP = (seq: number, label = `step ${seq}`): ProgressEvent => ({
  seq,
  step: `node_${seq}`,
  label,
  detail: "",
  at: "2026-09-22T10:00:00Z",
});

/** The hook, rendered as the little it needs to be observable. */
function Harness({ client, mounted = true }: { client: Client; mounted?: boolean }) {
  return mounted ? <Inner client={client} /> : null;
}

function Inner({ client }: { client: Client }) {
  const ask = useAsk(client, { watch: { eventSource: fakeEventSource } });
  return (
    <div>
      <button type="button" onClick={() => ask.ask("how many stores?")}>
        ask
      </button>
      <button type="button" onClick={() => ask.ask("   ")}>
        ask blank
      </button>
      <button type="button" onClick={ask.cancel}>
        cancel
      </button>
      <button type="button" onClick={ask.reset}>
        reset
      </button>
      <output data-testid="phase">{ask.phase}</output>
      <output data-testid="busy">{String(ask.busy)}</output>
      <output data-testid="status">{ask.status ?? ""}</output>
      <output data-testid="error">{ask.error?.message ?? ""}</output>
      <output data-testid="notice">{ask.notice ?? ""}</output>
      <output data-testid="steps">{ask.progress.map((event) => event.seq).join(",")}</output>
      <output data-testid="answer">{ask.job?.answer?.answer ?? ""}</output>
    </div>
  );
}

const value = (id: string): string => screen.getByTestId(id).textContent ?? "";

describe("reduce", () => {
  it("keeps progress in order however it arrives", () => {
    const state = [STEP(3), STEP(1), STEP(2)].reduce(
      (acc, event) => reduce(acc, { type: "progress", event }),
      reduce({ phase: "idle", job: null, progress: [], status: null, error: null, notice: null }, { type: "ask" }),
    );
    expect(state.progress.map((event) => event.seq)).toEqual([1, 2, 3]);
  });

  it("ignores an event number it already holds, so a resume does not duplicate", () => {
    const asked = reduce(
      { phase: "idle", job: null, progress: [], status: null, error: null, notice: null },
      { type: "ask" },
    );
    const once = reduce(asked, { type: "progress", event: STEP(1) });
    const twice = reduce(once, { type: "progress", event: STEP(1) });

    expect(twice).toBe(once);
    expect(twice.progress).toHaveLength(1);
  });

  it("keeps the streamed progress when the finished job carries none", () => {
    const start = reduce(
      { phase: "streaming", job: null, progress: [STEP(1)], status: "running", error: null, notice: null },
      { type: "done", job: makeJob({ progress: [] }) },
    );
    expect(start.progress.map((event) => event.seq)).toEqual([1]);
  });
});

describe("useAsk", () => {
  it("submits, streams and finishes", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));
    expect(value("busy")).toBe("true");

    act(() => {
      FakeEventSource.last().emit("status", { status: "running" });
      FakeEventSource.last().emit("progress", STEP(1));
      FakeEventSource.last().emit("progress", STEP(2));
    });
    expect(value("steps")).toBe("1,2");
    expect(value("status")).toBe("running");

    act(() => {
      FakeEventSource.last().emit("done", makeJob());
    });
    expect(value("phase")).toBe("finished");
    expect(value("busy")).toBe("false");
    expect(value("answer")).toContain("Produce led");
    expect(client.ask).toHaveBeenCalledWith("how many stores?");
  });

  it("will not send a blank question", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask blank"));
    expect(client.ask).not.toHaveBeenCalled();
    expect(value("phase")).toBe("idle");
  });

  it("reports a rejected submission without pretending to be running", async () => {
    const client = fakeClient({
      ask: vi.fn().mockRejectedValue(new ApiError(503, "unavailable", "shutting down")),
    });
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("error")).toBe("shutting down"));
    expect(value("busy")).toBe("false");
  });

  it("wraps a rejection that is not an Error", async () => {
    render(<Harness client={fakeClient({ ask: vi.fn().mockRejectedValue("just a string") })} />);
    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("error")).toBe("just a string"));
  });

  it("surfaces a fallback to polling as a notice, not as an error", async () => {
    render(<Harness client={fakeClient()} />);
    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.last().fail();
      FakeEventSource.last().fail();
    });

    await waitFor(() => expect(value("notice")).toMatch(/polled instead/));
    expect(value("error")).toBe("");
  });

  it("reports a polling error as a notice too", async () => {
    const client = fakeClient({ job: vi.fn().mockRejectedValue(new Error("gateway timeout")) });
    render(<Harness client={client} />);
    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.last().fail();
      FakeEventSource.last().fail();
    });

    await waitFor(() => expect(value("notice")).toBe("gateway timeout"));
  });

  it("cancels a running question and forgets it", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));

    await userEvent.click(screen.getByText("cancel"));
    expect(client.cancel).toHaveBeenCalledWith("job-1");
    expect(value("phase")).toBe("idle");
    expect(FakeEventSource.last().closed).toBe(true);
  });

  it("does not tell the server to cancel a question that already succeeded", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));
    act(() => {
      FakeEventSource.last().emit("done", makeJob({ status: "succeeded" }));
    });

    await userEvent.click(screen.getByText("cancel"));
    expect(client.cancel).not.toHaveBeenCalled();
  });

  it("swallows a cancel the server refuses, since the user has already moved on", async () => {
    const client = fakeClient({
      cancel: vi.fn().mockRejectedValue(new ApiError(409, "job_running", "cannot be interrupted")),
    });
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));

    await userEvent.click(screen.getByText("cancel"));
    await waitFor(() => expect(value("phase")).toBe("idle"));
    expect(value("error")).toBe("");
  });

  it("cancels nothing when nothing is running", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);
    await userEvent.click(screen.getByText("cancel"));
    expect(client.cancel).not.toHaveBeenCalled();
  });

  it("resets without telling the server anything", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));

    await userEvent.click(screen.getByText("reset"));
    expect(value("phase")).toBe("idle");
    expect(client.cancel).not.toHaveBeenCalled();
  });

  it("never opens a stream for a question that was superseded while it was being sent", async () => {
    // The race: the first POST is still in flight when the second is sent,
    // so `stop()` has nothing to close -- the first question's watch does
    // not exist yet. If it were opened when the POST finally came back it
    // would overwrite the second question's, and the first answer would win.
    let resolveFirst: (job: ReturnType<typeof makeJob>) => void = () => undefined;
    const first = new Promise<ReturnType<typeof makeJob>>((resolve) => {
      resolveFirst = resolve;
    });
    const client = fakeClient({
      ask: vi
        .fn()
        .mockReturnValueOnce(first)
        .mockResolvedValueOnce(makeJob({ id: "second", status: "queued", answer: null, progress: [] })),
    });
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.last().url).toContain("second");

    await act(async () => {
      resolveFirst(makeJob({ id: "first", status: "queued", answer: null, progress: [] }));
      await first;
    });

    expect(FakeEventSource.instances).toHaveLength(1);

    // And the live stream still answers the question that is on screen.
    act(() => {
      FakeEventSource.last().emit("done", makeJob({ id: "second" }));
    });
    expect(value("phase")).toBe("finished");
  });

  it("does not report a failure for a question that was superseded", async () => {
    let rejectFirst: (error: Error) => void = () => undefined;
    const first = new Promise<ReturnType<typeof makeJob>>((_, reject) => {
      rejectFirst = reject;
    });
    const client = fakeClient({
      ask: vi
        .fn()
        .mockReturnValueOnce(first)
        .mockResolvedValueOnce(makeJob({ id: "second", status: "queued", answer: null, progress: [] })),
    });
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(value("phase")).toBe("streaming"));

    await act(async () => {
      rejectFirst(new ApiError(503, "unavailable", "the first one failed"));
      await first.catch(() => undefined);
    });

    expect(value("error")).toBe("");
    expect(value("phase")).toBe("streaming");
  });

  it("delivers nothing once the watch has been closed", async () => {
    const client = fakeClient();
    render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.last();

    await userEvent.click(screen.getByText("reset"));
    act(() => {
      stream.emit("progress", STEP(9, "stale"));
      stream.emit("status", { status: "running" });
      stream.emit("done", makeJob({ error: "stale answer" }));
    });

    expect(value("steps")).toBe("");
    expect(value("status")).toBe("");
    expect(value("phase")).toBe("idle");
  });

  it("closes the stream when the component goes away", async () => {
    const client = fakeClient();
    const view = render(<Harness client={client} />);

    await userEvent.click(screen.getByText("ask"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    view.rerender(<Harness client={client} mounted={false} />);
    expect(FakeEventSource.last().closed).toBe(true);
  });

  it("works without the watch option, falling back to the global EventSource", async () => {
    const original = globalThis.EventSource;
    globalThis.EventSource = fakeEventSource;
    try {
      function Plain({ client }: { client: Client }) {
        const ask = useAsk(client);
        return (
          <button type="button" onClick={() => ask.ask("q")}>
            ask
          </button>
        );
      }
      render(<Plain client={fakeClient()} />);
      await userEvent.click(screen.getByText("ask"));
      await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    } finally {
      globalThis.EventSource = original;
    }
  });
});
