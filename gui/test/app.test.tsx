import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { createFeedbackStore } from "../src/feedback/store";
import { ApiError } from "../src/api/client";
import { EXAMPLES } from "../src/components/AskBox";
import {
  FakeEventSource,
  fakeClient,
  fakeEventSource,
  makeJob,
  makeMeta,
  memoryStorage,
} from "./helpers";

afterEach(cleanup);
beforeEach(() => {
  FakeEventSource.reset();
});

const watch = { eventSource: fakeEventSource };

function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  const store = createFeedbackStore(memoryStorage());
  const view = render(<App client={client} store={store} watch={watch} />);
  return { client, store, view };
}

/** Ask the first example and let the stream answer it. */
async function askAndAnswer(job = makeJob()): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
  act(() => {
    FakeEventSource.last().emit("progress", {
      seq: 1,
      step: "screen",
      label: "screening",
      detail: "in scope",
      at: "",
    });
  });
  act(() => {
    FakeEventSource.last().emit("done", job);
  });
}

describe("App", () => {
  it("says what it is connected to once /v1/meta lands", async () => {
    mount();
    expect(await screen.findByText("nl2sql-agent")).toBeInTheDocument();
    expect(screen.getByText("qwen2.5-coder:32b")).toBeInTheDocument();
  });

  it("offers examples on an empty page and hides them once something has been asked", async () => {
    mount();
    expect(await screen.findByRole("button", { name: EXAMPLES[0] })).toBeInTheDocument();

    await askAndAnswer();
    expect(screen.queryByRole("button", { name: EXAMPLES[0] })).toBeNull();
  });

  it("shows the pipeline's own steps while it works, then the answer", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));

    const progress = await screen.findByLabelText("Pipeline progress");
    act(() => {
      FakeEventSource.last().emit("progress", {
        seq: 1,
        step: "screen",
        label: "screening",
        detail: "in scope",
        at: "",
      });
    });
    expect(within(progress).getByRole("status").textContent).toContain("screening");
    // Every node the server said it runs is listed, not only the ones so far.
    expect(within(progress).getByText("1 / 5")).toBeInTheDocument();

    act(() => {
      FakeEventSource.last().emit("done", makeJob());
    });

    expect(await screen.findByLabelText("Answer")).toBeInTheDocument();
    expect(screen.queryByLabelText("Pipeline progress")).toBeNull();
  });

  it("still answers questions when /v1/meta never landed", async () => {
    // Nothing about asking depends on meta: it only says which optional
    // stages ran, and an answer with no such guidance shows everything.
    mount({ meta: vi.fn().mockRejectedValue(new ApiError(0, "unreachable", "no route")) });
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));
    expect(await screen.findByLabelText("Pipeline progress")).toBeInTheDocument();

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => {
      FakeEventSource.last().emit("done", makeJob());
    });

    const answer = await screen.findByLabelText("Answer");
    expect(within(answer).getByText(/checked against the rows above/)).toBeInTheDocument();
  });

  it("keeps every question asked this session and shows an earlier one again", async () => {
    const first = makeJob({ id: "first", question: "first question" });
    const second = makeJob({ id: "second", question: "second question" });
    const client = fakeClient({
      ask: vi
        .fn()
        .mockResolvedValueOnce({ ...first, status: "queued", answer: null })
        .mockResolvedValueOnce({ ...second, status: "queued", answer: null }),
    });
    render(<App client={client} store={createFeedbackStore(memoryStorage())} watch={watch} />);

    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => {
      FakeEventSource.last().emit("done", first);
    });

    await userEvent.type(screen.getByLabelText(/Ask the retail database/), "another{Enter}");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    act(() => {
      FakeEventSource.last().emit("done", second);
    });

    const history = await screen.findByLabelText("Earlier questions");
    expect(within(history).getAllByRole("button")).toHaveLength(2);

    await userEvent.click(within(history).getByRole("button", { name: /first question/ }));
    expect(within(await screen.findByLabelText("Answer")).getByText("first question")).toBeInTheDocument();
  });

  it("records feedback against the answer and shows it in the session list", async () => {
    const { store } = mount();
    await askAndAnswer();

    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(store.get("job-1")?.verdict).toBe("yes");

    const history = screen.getByLabelText("Earlier questions");
    expect(within(history).getByText("✓")).toBeInTheDocument();
  });

  it("says plainly when the API cannot be reached", async () => {
    mount({ meta: vi.fn().mockRejectedValue(new ApiError(0, "unreachable", "cannot reach the API")) });
    expect((await screen.findByRole("alert")).textContent).toContain("Not connected.");
  });

  it("wraps a meta rejection that is not an Error", async () => {
    mount({ meta: vi.fn().mockRejectedValue("kaput") });
    expect((await screen.findByRole("alert")).textContent).toContain("kaput");
  });

  it("names a dependency that is not ready, rather than looking unreachable", async () => {
    mount({
      readiness: vi.fn().mockResolvedValue({
        ready: false,
        checks: { database: { ok: false, detail: "still starting" }, llm: { ok: true, detail: "" } },
        warnings: ["no API_TOKEN is set"],
      }),
    });

    expect(await screen.findByText("no API_TOKEN is set")).toBeInTheDocument();
    expect(screen.getByText("database: still starting")).toBeInTheDocument();
    // The healthy check is not reported as a problem.
    expect(screen.queryByText(/^llm:/)).toBeNull();
  });

  it("carries on when the readiness probe itself fails", async () => {
    mount({ readiness: vi.fn().mockRejectedValue(new Error("no route")) });
    expect(await screen.findByText("nl2sql-agent")).toBeInTheDocument();
    expect(screen.queryByText("no route")).toBeNull();
  });

  it("reports a question that could not be sent at all", async () => {
    mount({ ask: vi.fn().mockRejectedValue(new ApiError(503, "unavailable", "shutting down")) });
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That question could not be sent.");
    expect(alert.textContent).toContain("shutting down");
  });

  it("tells the user when it has given up on streaming", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.last().fail();
      FakeEventSource.last().fail();
    });

    expect(await screen.findByText(/polled instead/)).toBeInTheDocument();
  });

  it("goes back to the live answer when a new question is asked from the history view", async () => {
    const { client } = mount();
    await askAndAnswer(makeJob({ id: "first", question: "first question" }));

    await userEvent.click(
      within(screen.getByLabelText("Earlier questions")).getByRole("button", { name: /first question/ }),
    );
    await userEvent.type(screen.getByLabelText(/Ask the retail database/), "a new one{Enter}");

    expect(client.ask).toHaveBeenLastCalledWith("a new one");
    expect(await screen.findByLabelText("Pipeline progress")).toBeInTheDocument();
  });

  it("stops a running question when asked to", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));

    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(client.cancel).toHaveBeenCalled();
    expect(screen.queryByLabelText("Pipeline progress")).toBeNull();
  });

  it("does not show a queued job as though it were an answer", async () => {
    mount({ ask: vi.fn().mockResolvedValue(makeJob({ status: "queued", answer: null })) });
    await userEvent.click(await screen.findByRole("button", { name: EXAMPLES[0] }));
    expect(screen.queryByLabelText("Answer")).toBeNull();
  });

  it("builds its own client and store when it is given neither", async () => {
    // The real client reaches for fetch; a rejection is enough to prove the
    // default path is the one that ran.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("Failed to fetch"));
    try {
      render(<App />);
      expect((await screen.findByRole("alert")).textContent).toContain("cannot reach the API");
      expect(fetchSpy).toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("says nothing about a start-up request that was aborted by its own unmount", async () => {
    // React runs an effect twice under StrictMode and a navigation can
    // unmount mid-flight; a rejection caused by the abort is this
    // component's own doing and must not be reported as a dead server.
    let rejectMeta: (error: Error) => void = () => undefined;
    const pending = new Promise<never>((_, reject) => {
      rejectMeta = reject;
    });
    const { view } = mount({ meta: vi.fn().mockReturnValue(pending) as never });

    view.unmount();
    await act(async () => {
      rejectMeta(new DOMException("aborted", "AbortError"));
      await pending.catch(() => undefined);
    });

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps only the most recent questions", async () => {
    const meta = makeMeta();
    const client = fakeClient({ meta: vi.fn().mockResolvedValue(meta) });
    render(<App client={client} store={createFeedbackStore(memoryStorage())} watch={watch} />);

    await screen.findByText("nl2sql-agent");
    for (let index = 0; index < 22; index += 1) {
      await userEvent.type(screen.getByLabelText(/Ask the retail database/), `q${index}{Enter}`);
      await waitFor(() => expect(FakeEventSource.instances).toHaveLength(index + 1));
      act(() => {
        FakeEventSource.last().emit("done", makeJob({ id: `job-${index}`, question: `q${index}` }));
      });
    }

    const history = screen.getByLabelText("Earlier questions");
    expect(within(history).getAllByRole("button")).toHaveLength(20);
  });
});
