/**
 * The store that sends, and the interface on top of it.
 *
 * The behaviour worth pinning down here is not "it calls the endpoint" -- it
 * is what happens when the endpoint does not answer. A feedback widget whose
 * failures are invisible collects nothing and tells the user it collected
 * something, which is the one outcome worse than having no widget.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiFeedbackStore, type FeedbackClient } from "../src/feedback/apiStore";
import { STORAGE_KEY, createFeedbackStore } from "../src/feedback/store";
import { useFeedback } from "../src/feedback/useFeedback";
import { FeedbackBar } from "../src/components/FeedbackBar";
import { memoryStorage } from "./helpers";

afterEach(cleanup);

const AT = () => new Date("2026-09-22T12:00:00Z");

function makeClient(overrides: Partial<FeedbackClient> = {}): FeedbackClient {
  return {
    submitFeedback: vi.fn().mockResolvedValue({ id: "s1" }),
    withdrawFeedback: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function makeStore(client: FeedbackClient, onError?: (e: Error, id: string) => void) {
  const local = createFeedbackStore(memoryStorage(), AT);
  return createApiFeedbackStore(onError ? { client, local, onError } : { client, local });
}

/** A store told the server does not take feedback. */
function makeOfflineStore(client: FeedbackClient) {
  return createApiFeedbackStore({
    client,
    local: createFeedbackStore(memoryStorage(), AT),
    enabled: () => false,
  });
}

describe("createApiFeedbackStore", () => {
  it("records the verdict before the request resolves", () => {
    // The whole point: the button moves on the click, not on the round trip.
    let release: (value: unknown) => void = () => {};
    const client = makeClient({
      submitFeedback: vi.fn(() => new Promise((resolve) => (release = resolve))),
    });
    const store = makeStore(client);

    const record = store.set("job-1", "q", "yes");
    expect(record.verdict).toBe("yes");
    expect(store.get("job-1")?.sync).toBe("saving");
    release(undefined);
  });

  it("marks a verdict sent once the server confirms it", async () => {
    const client = makeClient();
    const store = makeStore(client);

    store.set("job-1", "q", "yes");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("saved"));
    expect(client.submitFeedback).toHaveBeenCalledWith("job-1", "yes", "");
  });

  it("marks a verdict unsent, with the reason, when the server refuses", async () => {
    const onError = vi.fn();
    const client = makeClient({
      submitFeedback: vi.fn().mockRejectedValue(new Error("cannot reach the API")),
    });
    const store = makeStore(client, onError);

    store.set("job-1", "q", "no");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("failed"));
    expect(store.get("job-1")?.error).toBe("cannot reach the API");
    expect(onError).toHaveBeenCalledWith(expect.any(Error), "job-1");
    // The verdict itself survives: it is still the user's own record.
    expect(store.get("job-1")?.verdict).toBe("no");
  });

  it("wraps a rejection that is not an Error", async () => {
    const client = makeClient({ submitFeedback: vi.fn().mockRejectedValue("nope") });
    const store = makeStore(client);
    store.set("job-1", "q", "yes");
    await waitFor(() => expect(store.get("job-1")?.error).toBe("nope"));
  });

  it("does not let a slow success overwrite a newer verdict", async () => {
    // The user votes yes, changes to no before the first request lands. The
    // late confirmation must not put "yes" back on screen.
    let release: (value: unknown) => void = () => {};
    const submitFeedback = vi
      .fn()
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
      .mockResolvedValue({ id: "s2" });
    const store = makeStore(makeClient({ submitFeedback }));

    store.set("job-1", "q", "yes");
    store.set("job-1", "q", "no");
    await act(async () => {
      release(undefined);
    });

    expect(store.get("job-1")?.verdict).toBe("no");
  });

  it("does not let a slow failure overwrite a newer verdict either", async () => {
    let reject: (reason: unknown) => void = () => {};
    const submitFeedback = vi
      .fn()
      .mockImplementationOnce(() => new Promise((_resolve, r) => (reject = r)))
      .mockResolvedValue({ id: "s2" });
    const onError = vi.fn();
    const store = makeStore(makeClient({ submitFeedback }), onError);

    store.set("job-1", "q", "yes");
    store.set("job-1", "q", "no");
    await act(async () => {
      reject(new Error("too late"));
    });

    expect(store.get("job-1")?.error).toBeUndefined();
    // Still reported, because it still failed -- just not attributed to the
    // verdict that has replaced it.
    expect(onError).toHaveBeenCalled();
  });

  it("sends a comment with the verdict", async () => {
    const client = makeClient();
    const store = makeStore(client);

    store.setWithComment("job-1", "q", "no", "fiscal month is off by one");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("saved"));
    expect(client.submitFeedback).toHaveBeenCalledWith("job-1", "no", "fiscal month is off by one");
  });

  it("withdraws locally and at the server", async () => {
    const client = makeClient();
    const store = makeStore(client);

    store.set("job-1", "q", "yes");
    store.clear("job-1");

    expect(store.get("job-1")).toBeUndefined();
    await waitFor(() => expect(client.withdrawFeedback).toHaveBeenCalledWith("job-1"));
  });

  it("does not call the server to withdraw something that was never recorded", () => {
    const client = makeClient();
    const store = makeStore(client);
    store.clear("never-voted");
    expect(client.withdrawFeedback).not.toHaveBeenCalled();
  });

  it("keeps the withdrawal local even when the server refuses it", async () => {
    const onError = vi.fn();
    const client = makeClient({
      withdrawFeedback: vi.fn().mockRejectedValue(new Error("gone")),
    });
    const store = makeStore(client, onError);

    store.set("job-1", "q", "yes");
    store.clear("job-1");

    await waitFor(() => expect(onError).toHaveBeenCalled());
    // Re-showing a verdict the user just withdrew would be the worse lie.
    expect(store.get("job-1")).toBeUndefined();
  });

  it("wraps a non-Error withdrawal rejection", async () => {
    const onError = vi.fn();
    const store = makeStore(
      makeClient({ withdrawFeedback: vi.fn().mockRejectedValue("boom") }),
      onError,
    );
    store.set("job-1", "q", "yes");
    store.clear("job-1");
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.any(Error), "job-1"));
  });

  it("resends a failed verdict, with its comment", async () => {
    const submitFeedback = vi
      .fn()
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValue({ id: "s1" });
    const store = makeStore(makeClient({ submitFeedback }));

    store.setWithComment("job-1", "q", "no", "wrong month");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("failed"));

    store.retry("job-1");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("saved"));
    expect(submitFeedback).toHaveBeenLastCalledWith("job-1", "no", "wrong month");
  });

  it("resends a verdict left failed by an earlier session", async () => {
    // The realistic way `comments` has no entry for a failed record: the
    // record came back from localStorage after a reload, and the map that
    // remembers what was typed did not survive the page.
    const storage = memoryStorage();
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        { jobId: "job-1", question: "q", verdict: "no", at: AT().toISOString(), sync: "failed" },
      ]),
    );
    const client = makeClient();
    const store = createApiFeedbackStore({
      client,
      local: createFeedbackStore(storage, AT),
    });

    store.retry("job-1");
    await waitFor(() => expect(client.submitFeedback).toHaveBeenCalledWith("job-1", "no", ""));
  });

  it("ignores a retry for something that did not fail", async () => {
    const client = makeClient();
    const store = makeStore(client);

    store.retry("never-voted");
    store.set("job-1", "q", "yes");
    await waitFor(() => expect(store.get("job-1")?.sync).toBe("saved"));

    store.retry("job-1");
    expect(client.submitFeedback).toHaveBeenCalledTimes(1);
  });

  it("records locally, and sends nothing, when the server takes no feedback", () => {
    // Not a failure: a server with no staging database was never going to
    // take it. Marking these "not sent" would put a red warning under every
    // answer for something nobody did wrong.
    const client = makeClient();
    const store = makeOfflineStore(client);

    store.set("job-1", "q", "yes");
    expect(store.get("job-1")?.sync).toBe("local");
    expect(client.submitFeedback).not.toHaveBeenCalled();
  });

  it("does not try to withdraw from a server that takes no feedback", () => {
    const client = makeClient();
    const store = makeOfflineStore(client);
    store.set("job-1", "q", "yes");

    store.clear("job-1");
    expect(store.get("job-1")).toBeUndefined();
    expect(client.withdrawFeedback).not.toHaveBeenCalled();
  });

  it("uses the browser store when no mirror is given", () => {
    const store = createApiFeedbackStore({ client: makeClient() });
    expect(store.all()).toEqual([]);
  });

  it("exposes the mirror's subscription", () => {
    const store = makeStore(makeClient());
    const listener = vi.fn();
    store.subscribe(listener);
    store.set("job-1", "q", "yes");
    expect(listener).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The hook, and the bar it feeds
// ---------------------------------------------------------------------------

function Harness({
  store,
  jobId,
}: {
  store: ReturnType<typeof makeStore>;
  // No default: the point of one of these tests is to pass undefined, and a
  // destructuring default would quietly turn it back into a job id.
  jobId?: string | undefined;
}) {
  const feedback = useFeedback(store, jobId, "how many stores?");
  return (
    <FeedbackBar
      verdict={feedback.verdict}
      onVote={feedback.vote}
      onComment={feedback.comment}
      onRetry={feedback.retry}
      recordedAt={feedback.record?.at}
      sync={feedback.sync}
      error={feedback.error}
    />
  );
}

describe("FeedbackBar with a sending store", () => {
  it("offers no comment box until a verdict is given", async () => {
    render(<Harness store={makeStore(makeClient())} jobId="job-1" />);
    expect(screen.queryByLabelText(/what was wrong/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    expect(await screen.findByLabelText(/what was wrong/i)).toBeInTheDocument();
  });

  it("asks a different question after a positive verdict", async () => {
    render(<Harness store={makeStore(makeClient())} jobId="job-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect(await screen.findByLabelText(/anything worth noting/i)).toBeInTheDocument();
  });

  it("asks what was missing after a correct-but-incomplete verdict, and sends it as one", async () => {
    const client = makeClient();
    render(<Harness store={makeStore(client)} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct but incomplete" }));
    const box = await screen.findByLabelText(/what was missing/i);
    expect(box).toHaveAttribute("placeholder", "e.g. the product names beside the SKUs");
    await userEvent.type(box, "no store names");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(client.submitFeedback).toHaveBeenCalledWith("job-1", "incomplete", "no store names"),
    );
  });

  it("sends the comment and thanks the user", async () => {
    const client = makeClient();
    render(<Harness store={makeStore(client)} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    await userEvent.type(await screen.findByLabelText(/what was wrong/i), "off by one");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(client.submitFeedback).toHaveBeenCalledWith("job-1", "no", "off by one"),
    );
    expect(screen.getByText(/comment sent/i)).toBeInTheDocument();
  });

  it("will not send an empty comment", async () => {
    const client = makeClient();
    render(<Harness store={makeStore(client)} jobId="job-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));

    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    // Whitespace is not a comment either, and the form must refuse it even
    // if something re-enables the button.
    await userEvent.type(await screen.findByLabelText(/anything worth noting/i), "   ");
    expect(send).toBeDisabled();
  });

  it("shows the failure and offers a retry", async () => {
    const submitFeedback = vi
      .fn()
      .mockRejectedValueOnce(new Error("cannot reach the API"))
      .mockResolvedValue({ id: "s1" });
    render(<Harness store={makeStore(makeClient({ submitFeedback }))} jobId="job-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect(await screen.findByText(/not sent/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("cannot reach the API");

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText(/sent for review/)).toBeInTheDocument());
  });

  it("clears the comment box when the verdict changes", async () => {
    render(<Harness store={makeStore(makeClient())} jobId="job-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    await userEvent.type(await screen.findByLabelText(/anything worth noting/i), "keep this");

    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    expect(await screen.findByLabelText(/what was wrong/i)).toHaveValue("");
  });

  it("refuses a whitespace comment even when the form is submitted directly", async () => {
    // The Send button is disabled for this, so the guard inside onSubmit is
    // only reachable by submitting the form another way -- which a browser
    // does on Enter in a text input.
    const client = makeClient();
    render(<Harness store={makeStore(client)} jobId="job-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));

    const input = await screen.findByLabelText(/anything worth noting/i);
    await userEvent.type(input, "   ");
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    expect(client.submitFeedback).toHaveBeenCalledTimes(1); // the verdict only
    expect(screen.queryByText(/comment sent/i)).not.toBeInTheDocument();
  });

  it("does nothing when there is no job to attach an opinion to", async () => {
    const client = makeClient();
    const store = makeStore(client);
    render(<Harness store={store} />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect(client.submitFeedback).not.toHaveBeenCalled();
  });
});

describe("FeedbackBar with a browser-only store", () => {
  it("draws no comment box, because there is nowhere to send one", async () => {
    const local = createFeedbackStore(memoryStorage(), AT);
    function Local() {
      const feedback = useFeedback(local, "job-1", "q");
      return (
        <FeedbackBar
          verdict={feedback.verdict}
          onVote={feedback.vote}
          onComment={feedback.comment}
          onRetry={feedback.retry}
          sync={feedback.sync}
        />
      );
    }
    render(<Local />);
    await userEvent.click(screen.getByRole("button", { name: "Correct" }));

    expect(screen.getByText(/click again to withdraw/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });
});

// The hook's own guards. These paths are unreachable through the bar -- it
// draws no comment box before a verdict, and no retry before a failure --
// so they are exercised against the hook itself rather than left uncovered
// on the argument that the UI would never do it. The UI is not the only
// caller this hook will ever have.
describe("useFeedback guards", () => {
  function Raw({
    store,
    jobId,
    onReady,
  }: {
    store: ReturnType<typeof makeStore>;
    jobId?: string | undefined;
    onReady: (api: ReturnType<typeof useFeedback>) => void;
  }) {
    onReady(useFeedback(store, jobId, "q"));
    return null;
  }

  it("will not attach a comment to a job with no verdict", () => {
    const client = makeClient();
    const store = makeStore(client);
    let api!: ReturnType<typeof useFeedback>;
    render(<Raw store={store} jobId="job-1" onReady={(value) => (api = value)} />);

    act(() => api.comment?.("a note with no opinion"));
    // The staging row is keyed on the job and its verdict is NOT NULL, so
    // there is nothing for this to amend.
    expect(client.submitFeedback).not.toHaveBeenCalled();
  });

  it("will not comment or retry when there is no job at all", () => {
    const client = makeClient();
    const store = makeStore(client);
    store.set("job-9", "q", "yes");
    let api!: ReturnType<typeof useFeedback>;
    render(<Raw store={store} onReady={(value) => (api = value)} />);

    act(() => api.comment?.("orphan"));
    act(() => api.retry?.());
    // Only the verdict recorded above, never anything for the absent job.
    expect(client.submitFeedback).toHaveBeenCalledTimes(1);
    expect(client.submitFeedback).toHaveBeenCalledWith("job-9", "yes", "");
  });
});
