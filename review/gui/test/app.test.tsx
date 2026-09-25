/**
 * The application, against a fake service.
 *
 * The behaviours worth pinning down are the ones that protect the golden
 * question set: that Promote is unreachable until the server says the draft
 * would parse, that a promoted submission stops being editable, and that a
 * partial promotion is reported as partial rather than as either success or
 * failure.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import {
  fakeClient,
  makeDraft,
  makeMeta,
  makePreview,
  makePromotion,
  makeSubmission,
} from "./helpers";

afterEach(cleanup);
beforeEach(() => vi.useRealTimers());

/** No debounce in tests: the delay is the thing being avoided, not tested. */
function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  const view = render(<App client={client} previewDelayMs={0} />);
  return { client, view };
}

async function openFirst(): Promise<void> {
  await userEvent.click(await screen.findByText(/total net sales for Produce/i));
  await screen.findByLabelText(/^Title/);
}

describe("the queue", () => {
  it("asks for pending work first", async () => {
    const { client } = mount();
    await waitFor(() => expect(client.submissions).toHaveBeenCalledWith({ state: "pending" }));
  });

  it("asks for everything when the filter is cleared", async () => {
    const { client } = mount();
    await screen.findByRole("button", { name: /all/ });
    await userEvent.click(screen.getByRole("button", { name: /all/ }));
    await waitFor(() => expect(client.submissions).toHaveBeenLastCalledWith({}));
  });

  it("filters by a named state", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByRole("button", { name: /accepted/ }));
    await waitFor(() => expect(client.submissions).toHaveBeenLastCalledWith({ state: "accepted" }));
  });

  it("reports a queue it cannot load", async () => {
    mount({ submissions: vi.fn().mockRejectedValue(new Error("staging database is down")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("staging database is down");
  });
});

describe("opening a submission", () => {
  it("re-fetches it, because the list carries no seeded draft", async () => {
    const { client } = mount();
    await openFirst();
    expect(client.submission).toHaveBeenCalledWith("sub-1");
    expect(screen.getByLabelText(/^Title/)).toHaveValue(makeDraft().title);
  });

  it("shows the evidence beside the form", async () => {
    mount({ submission: vi.fn().mockResolvedValue({ ...makeSubmission({ comment: "off by one" }), draft: makeDraft() }) });
    await openFirst();
    expect(screen.getByLabelText("What was submitted")).toBeInTheDocument();
    expect(screen.getByText(/off by one/)).toBeInTheDocument();
  });

  it("says so when one cannot be opened", async () => {
    mount({ submission: vi.fn().mockRejectedValue(new Error("no submission sub-1")) });
    await userEvent.click(await screen.findByText(/total net sales for Produce/i));
    expect(await screen.findByRole("alert")).toHaveTextContent("no submission sub-1");
  });

  it("prompts before anything is chosen", async () => {
    mount();
    expect(await screen.findByText(/pick something from the queue/i)).toBeInTheDocument();
  });
});

describe("previewing", () => {
  it("does not ask the server while a required field is blank", async () => {
    const { client } = mount({
      submission: vi.fn().mockResolvedValue({
        ...makeSubmission(),
        draft: makeDraft({ keywords: "" }),
      }),
    });
    await openFirst();
    await waitFor(() => expect(screen.getByLabelText(/^Keywords/)).toHaveValue(""));
    expect(client.preview).not.toHaveBeenCalled();
  });

  it("asks once the draft is complete, and shows the block", async () => {
    const { client } = mount();
    await openFirst();
    await waitFor(() => expect(client.preview).toHaveBeenCalled());
    expect(await screen.findByText(/Q46, as it will be written/)).toBeInTheDocument();
  });

  it("keeps quiet when the preview call itself fails", async () => {
    mount({ preview: vi.fn().mockRejectedValue(new Error("boom")) });
    await openFirst();
    // A failed preview disables Promote; it does not shout at the curator,
    // who has not done anything wrong.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeDisabled(),
    );
  });

  it("stops asking once a required field is emptied again", async () => {
    const { client } = mount();
    await openFirst();
    await waitFor(() => expect(client.preview).toHaveBeenCalled());
    const calls = (client.preview as ReturnType<typeof vi.fn>).mock.calls.length;

    await userEvent.clear(screen.getByLabelText(/^Keywords/));
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeDisabled());
    expect((client.preview as ReturnType<typeof vi.fn>).mock.calls.length).toBe(calls);
  });
});

describe("judging", () => {
  it("accepts, saving the draft alongside", async () => {
    const { client } = mount();
    await openFirst();
    await userEvent.type(screen.getByLabelText(/review note/i), "good one");
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(client.review).toHaveBeenCalledWith(
        "sub-1",
        expect.objectContaining({ state: "accepted", review_note: "good one" }),
      ),
    );
    // The draft goes with it, so work in progress survives clicking away.
    expect((client.review as ReturnType<typeof vi.fn>).mock.calls[0]![1].draft).toBeTruthy();
  });

  it("rejects", async () => {
    const { client } = mount();
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(client.review).toHaveBeenCalledWith("sub-1", expect.objectContaining({ state: "rejected" })),
    );
  });

  it("names the reviewer", async () => {
    const { client } = mount();
    await openFirst();
    await userEvent.type(screen.getByLabelText("Reviewer"), "sam");
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(client.review).toHaveBeenCalledWith("sub-1", expect.objectContaining({ reviewer: "sam" })),
    );
  });

  it("reports a refusal", async () => {
    mount({ review: vi.fn().mockRejectedValue(new Error("already promoted")) });
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already promoted");
  });

  it("reports a refusal that was not thrown as an Error", async () => {
    mount({ review: vi.fn().mockRejectedValue("judge died") });
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("judge died");
  });
});

describe("promoting", () => {
  it("is refused until the server says the draft would parse", async () => {
    mount({ preview: vi.fn().mockResolvedValue(makePreview({ valid: false, problems: ["keywords is empty"] })) });
    await openFirst();
    await waitFor(() => expect(screen.getByText("keywords is empty")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeDisabled();
  });

  it("writes the pair and reports what happened", async () => {
    const { client } = mount();
    await openFirst();
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /promote to golden set/i }));

    await waitFor(() => expect(client.promote).toHaveBeenCalledWith("sub-1", expect.any(Object), ""));
    const result = await screen.findByLabelText("Promotion result");
    expect(within(result).getByText(/Q46 added to the golden set/)).toBeInTheDocument();
  });

  it("reports a partial promotion as partial", async () => {
    mount({
      promote: vi.fn().mockResolvedValue(
        makePromotion({
          reloaded: false,
          steps: [{ name: "embed_golden_pairs", ran: true, ok: false, detail: "no embedding host" }],
        }),
      ),
    });
    await openFirst();
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /promote to golden set/i }));

    expect(await screen.findByText(/have not caught up/i)).toBeInTheDocument();
  });

  it("surfaces a refusal from the server", async () => {
    mount({ promote: vi.fn().mockRejectedValue(new Error("the rendered pair does not parse")) });
    await openFirst();
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /promote to golden set/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not parse");
  });

  it("surfaces a refusal that was not thrown as an Error", async () => {
    mount({ promote: vi.fn().mockRejectedValue("promote died") });
    await openFirst();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: /promote to golden set/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("promote died");
  });

  it("can dismiss the result", async () => {
    mount();
    await openFirst();
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /promote to golden set/i }));
    await screen.findByLabelText("Promotion result");

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("Promotion result")).not.toBeInTheDocument();
  });

  it("refuses a rejected submission and says why", async () => {
    mount({
      submission: vi
        .fn()
        .mockResolvedValue({ ...makeSubmission({ state: "rejected" }), draft: makeDraft() }),
    });
    await openFirst();
    await waitFor(() => expect(screen.getByRole("button", { name: /promote to golden set/i })).toBeDisabled());
    expect(screen.getByText(/accept it before promoting it/i)).toBeInTheDocument();
  });

  it("makes an already-promoted submission read-only", async () => {
    mount({
      submission: vi.fn().mockResolvedValue({
        ...makeSubmission({ state: "promoted", promoted_pair_id: "Q46" }),
        draft: makeDraft(),
      }),
    });
    await userEvent.click(await screen.findByText(/total net sales for Produce/i));

    expect(await screen.findByText(/already promoted as/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Title/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /promote to golden set/i })).not.toBeInTheDocument();
  });
});

describe("the status bar", () => {
  it("shows the service and its warnings", async () => {
    mount({ meta: vi.fn().mockResolvedValue(makeMeta({ warnings: ["No REVIEW_TOKEN is set"] })) });
    expect(await screen.findByText("No REVIEW_TOKEN is set")).toBeInTheDocument();
  });

  it("says when the service cannot be reached at all", async () => {
    mount({ meta: vi.fn().mockRejectedValue(new Error("connection refused")) });
    expect(await screen.findByText(/connection refused/)).toBeInTheDocument();
  });

  it("wraps a rejection that is not an Error", async () => {
    mount({ meta: vi.fn().mockRejectedValue("nope") });
    expect(await screen.findByText(/nope/)).toBeInTheDocument();
  });

  it("wraps a non-Error from the queue too", async () => {
    mount({ submissions: vi.fn().mockRejectedValue("queue died") });
    expect(await screen.findByRole("alert")).toHaveTextContent("queue died");
  });

  it("wraps a non-Error from opening, judging and promoting", async () => {
    const { client } = mount({ submission: vi.fn().mockRejectedValue("open died") });
    await userEvent.click(await screen.findByText(/total net sales for Produce/i));
    expect(await screen.findByRole("alert")).toHaveTextContent("open died");
    expect(client.submission).toHaveBeenCalled();
  });
});

describe("the default client", () => {
  it("is built when none is injected", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("offline")) as unknown as typeof fetch;
    try {
      render(<App />);
      // Both the queue and the status bar report it, which is the point: a
      // service that cannot be reached at all fails every call, not one.
      expect(await screen.findAllByText(/TypeError: offline/)).toHaveLength(2);
    } finally {
      globalThis.fetch = original;
    }
  });
});
