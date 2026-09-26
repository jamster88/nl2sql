import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MAX_RECORDS, STORAGE_KEY, createFeedbackStore } from "../src/feedback/store";
import { useFeedback } from "../src/feedback/useFeedback";
import { FeedbackBar } from "../src/components/FeedbackBar";
import { hostileStorage, memoryStorage } from "./helpers";

afterEach(cleanup);

const AT = () => new Date("2026-09-22T12:00:00Z");

describe("createFeedbackStore", () => {
  it("starts empty and records a verdict", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    expect(store.all()).toEqual([]);

    const record = store.set("job-1", "how many stores?", "yes");
    expect(record).toEqual({
      jobId: "job-1",
      question: "how many stores?",
      verdict: "yes",
      at: "2026-09-22T12:00:00.000Z",
      // The browser-only store says so about every record it holds: it was
      // recorded here and was never going anywhere else.
      sync: "local",
    });
    expect(store.get("job-1")).toEqual(record);
  });

  it("replaces a verdict rather than recording it twice", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    store.set("job-1", "q", "yes");
    store.set("job-1", "q", "no");

    expect(store.all()).toHaveLength(1);
    expect(store.get("job-1")?.verdict).toBe("no");
  });

  it("keeps the newest first", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    store.set("a", "first", "yes");
    store.set("b", "second", "no");
    expect(store.all().map((record) => record.jobId)).toEqual(["b", "a"]);
  });

  it("withdraws a verdict", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    store.set("job-1", "q", "yes");
    store.clear("job-1");
    expect(store.get("job-1")).toBeUndefined();
  });

  it("does nothing, and tells nobody, when clearing something that is not there", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    const listener = vi.fn();
    store.subscribe(listener);

    store.clear("never-existed");
    expect(listener).not.toHaveBeenCalled();
  });

  it("caps how much it remembers", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    for (let index = 0; index < MAX_RECORDS + 10; index += 1) {
      store.set(`job-${index}`, "q", "yes");
    }
    expect(store.all()).toHaveLength(MAX_RECORDS);
  });

  it("notifies subscribers and stops when they unsubscribe", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);

    store.set("job-1", "q", "yes");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    store.set("job-2", "q", "no");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  describe("persistence", () => {
    it("survives a reload", () => {
      const storage = memoryStorage();
      createFeedbackStore(storage, AT).set("job-1", "q", "yes");

      const reopened = createFeedbackStore(storage, AT);
      expect(reopened.get("job-1")?.verdict).toBe("yes");
    });

    it("reads a stored no back as a no", () => {
      const storage = memoryStorage();
      createFeedbackStore(storage, AT).set("job-1", "q", "no");
      expect(createFeedbackStore(storage, AT).get("job-1")?.verdict).toBe("no");
    });

    it("reads a stored correct-but-incomplete back as one", () => {
      const storage = memoryStorage();
      createFeedbackStore(storage, AT).set("job-1", "q", "incomplete");
      expect(createFeedbackStore(storage, AT).get("job-1")?.verdict).toBe("incomplete");
    });

    it("drops a stored verdict it has no button for", () => {
      const storage = memoryStorage();
      storage.setItem(
        STORAGE_KEY,
        JSON.stringify([{ jobId: "job-1", question: "q", verdict: "maybe", at: AT().toISOString() }]),
      );
      expect(createFeedbackStore(storage, AT).get("job-1")).toBeUndefined();
    });

    it("ignores a stored value that is not the shape it expects", () => {
      const storage = memoryStorage();
      storage.setItem(STORAGE_KEY, JSON.stringify([{ jobId: "x" }, "nonsense", null]));
      expect(createFeedbackStore(storage, AT).all()).toEqual([]);
    });

    it("ignores a stored value that is not a list", () => {
      const storage = memoryStorage();
      storage.setItem(STORAGE_KEY, JSON.stringify({ jobId: "x" }));
      expect(createFeedbackStore(storage, AT).all()).toEqual([]);
    });

    it("ignores half-written JSON", () => {
      const storage = memoryStorage();
      storage.setItem(STORAGE_KEY, "{not json");
      expect(createFeedbackStore(storage, AT).all()).toEqual([]);
    });

    it("works in a browser that refuses to store anything", () => {
      const store = createFeedbackStore(hostileStorage(), AT);
      expect(store.all()).toEqual([]);

      // The verdict still holds for this session; it just will not outlive it.
      expect(() => store.set("job-1", "q", "yes")).not.toThrow();
      expect(store.get("job-1")?.verdict).toBe("yes");
    });

    it("works with no storage at all", () => {
      const store = createFeedbackStore(undefined, AT);
      store.set("job-1", "q", "no");
      expect(store.get("job-1")?.verdict).toBe("no");
    });

    it("defaults to the real clock", () => {
      const store = createFeedbackStore(memoryStorage());
      const before = Date.now();
      const record = store.set("job-1", "q", "yes");
      expect(new Date(record.at).getTime()).toBeGreaterThanOrEqual(before);
    });
  });
});

/** The hook and the bar, together, because that is how they are used. */
function Harness({ store, jobId }: { store: ReturnType<typeof createFeedbackStore>; jobId?: string }) {
  const feedback = useFeedback(store, jobId, "how many stores?");
  return (
    <FeedbackBar verdict={feedback.verdict} onVote={feedback.vote} recordedAt={feedback.record?.at} />
  );
}

describe("useFeedback", () => {
  it("records a yes and shows it back", async () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));

    expect(screen.getByRole("button", { name: "Correct" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/recorded as/)).toBeInTheDocument();
    expect(store.get("job-1")?.verdict).toBe("yes");
  });

  it("offers correct, wrong and correct but incomplete, in that order", () => {
    render(<Harness store={createFeedbackStore(memoryStorage(), AT)} jobId="job-1" />);
    const group = screen.getByRole("group", { name: /was this answer correct/i });
    const labels = Array.from(group.querySelectorAll("button")).map((b) => b.textContent);
    expect(labels).toEqual(["Correct", "Wrong", "Correct but incomplete"]);
  });

  it("records a correct-but-incomplete verdict as its own kind", async () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct but incomplete" }));

    expect(store.get("job-1")?.verdict).toBe("incomplete");
    expect(screen.getByRole("button", { name: "Correct but incomplete" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Correct" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switches from yes to no", async () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));

    expect(store.get("job-1")?.verdict).toBe("no");
    expect(screen.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "true");
  });

  it("withdraws when the recorded verdict is clicked again", async () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));

    expect(store.get("job-1")).toBeUndefined();
    expect(screen.getByText("Was this answer right?")).toBeInTheDocument();
  });

  it("does nothing without a job to attach the verdict to", async () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect(store.all()).toEqual([]);
  });

  it("re-renders when the store changes underneath it", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    render(<Harness store={store} jobId="job-1" />);

    act(() => {
      store.set("job-1", "q", "no");
    });
    expect(screen.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "true");
  });

  it("exposes withdraw for a caller that wants it explicitly", () => {
    const store = createFeedbackStore(memoryStorage(), AT);
    store.set("job-1", "q", "yes");

    let withdraw = (): void => undefined;
    function Probe({ jobId }: { jobId?: string }) {
      ({ withdraw } = useFeedback(store, jobId, "q"));
      return null;
    }

    const view = render(<Probe jobId="job-1" />);
    act(() => withdraw());
    expect(store.get("job-1")).toBeUndefined();

    // And is a no-op with nothing selected, rather than throwing.
    view.rerender(<Probe />);
    expect(() => act(() => withdraw())).not.toThrow();
  });
});

describe("FeedbackBar", () => {
  it("can be disabled", () => {
    render(<FeedbackBar verdict={undefined} onVote={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: "Correct" })).toBeDisabled();
  });

  it("shows when the verdict was recorded", () => {
    render(<FeedbackBar verdict="yes" onVote={vi.fn()} recordedAt="2026-09-22T12:00:00Z" />);
    expect(screen.getByText(/click again to withdraw/)).toBeInTheDocument();
  });

  it("omits the time when there is none", () => {
    render(<FeedbackBar verdict="yes" onVote={vi.fn()} />);
    expect(screen.getByText("click again to withdraw")).toBeInTheDocument();
  });
});
