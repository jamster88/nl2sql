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
  makeFixResult,
  makeMeta,
  makePreview,
  makePromotion,
  makeSubmission,
  makeUndo,
  makeValidation,
  makeWithdrawal,
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
    await waitFor(() => expect(client.submissions).toHaveBeenCalledWith({ state: "pending", verdict: "yes" }));
  });

  it("asks for everything when the filter is cleared", async () => {
    const { client } = mount();
    await screen.findByRole("button", { name: /all/ });
    await userEvent.click(screen.getByRole("button", { name: /all/ }));
    await waitFor(() => expect(client.submissions).toHaveBeenLastCalledWith({ verdict: "yes" }));
  });

  it("filters by a named state", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByRole("button", { name: /accepted/ }));
    await waitFor(() => expect(client.submissions).toHaveBeenLastCalledWith({ state: "accepted", verdict: "yes" }));
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

    expect(await screen.findByText(/already promoted as/i)).toHaveTextContent(/put it back to pending below/);
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


// ---------------------------------------------------------------------------
// One pane per verdict, and fixing the wrong and the incomplete
// ---------------------------------------------------------------------------

function wrong(overrides: Parameters<typeof makeSubmission>[0] = {}) {
  return makeSubmission({
    id: "sub-w",
    verdict: "no",
    question: "top 10 SKUs",
    sql_code: "SELECT sku_id FROM dim_product",
    ...overrides,
  });
}

/** A client serving one wrong submission in the Wrong pane. */
function mountWrong(overrides: Parameters<typeof fakeClient>[0] = {}, submission = wrong()) {
  return mount({
    submissions: vi.fn().mockResolvedValue({
      submissions: [submission],
      count: 1,
      counts: {},
      counts_by_verdict: { no: { pending: 1 } },
    }),
    // Seeded as the service seeds it: a wrong answer's draft carries the
    // agent's own SQL, which is where a fix starts.
    submission: vi.fn().mockResolvedValue({ ...submission, draft: makeDraft({ sql_code: submission.sql_code }) }),
    ...overrides,
  });
}

async function openWrong(pane: RegExp = /^Wrong/): Promise<void> {
  await userEvent.click(await screen.findByRole("tab", { name: pane }));
  await userEvent.click(await screen.findByText("top 10 SKUs"));
  await screen.findByLabelText("Corrected SQL");
}

describe("the panes", () => {
  it("offers one pane per verdict, each saying where its answers go", async () => {
    mount();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Correct → golden set1",
      "Wrong → corrections0",
      "Correct but incomplete → completions0",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  });

  it("counts each pane's pending work from the queue's per-verdict counts", async () => {
    mount({
      submissions: vi.fn().mockResolvedValue({
        submissions: [],
        count: 0,
        counts: {},
        counts_by_verdict: { yes: { pending: 1 }, no: { pending: 2 }, incomplete: { pending: 3 } },
      }),
    });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Wrong/ })).toHaveTextContent("Wrong → corrections2"),
    );
    expect(screen.getByRole("tab", { name: /^Correct but/ })).toHaveTextContent("3");
  });

  it("asks for the chosen pane's verdict, and closes what was open", async () => {
    const { client } = mount();
    await openFirst();
    await userEvent.click(screen.getByRole("tab", { name: /^Correct but/ }));
    await waitFor(() =>
      expect(client.submissions).toHaveBeenLastCalledWith({ state: "pending", verdict: "incomplete" }),
    );
    expect(screen.queryByLabelText(/^Title/)).not.toBeInTheDocument();
    expect(screen.getByText(/Pick something from the queue/)).toBeInTheDocument();
  });

  it("asks for every state of a pane when the filter is cleared", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByRole("tab", { name: /^Wrong/ }));
    await userEvent.click(screen.getByRole("button", { name: /all/ }));
    await waitFor(() => expect(client.submissions).toHaveBeenLastCalledWith({ verdict: "no" }));
  });
});

describe("fixing a wrong answer", () => {
  it("starts from the agent's SQL, with nothing to save until it validates", async () => {
    mountWrong();
    await openWrong();
    expect(screen.getByLabelText("Corrected SQL")).toHaveValue("SELECT sku_id FROM dim_product");
    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Promote/ })).not.toBeInTheDocument();
  });

  it("validates the text in the box and shows what it returned", async () => {
    const { client } = mountWrong();
    await openWrong();
    const box = screen.getByLabelText("Corrected SQL");
    await userEvent.clear(box);
    await userEvent.type(box, "SELECT sku_id, product_name FROM dim_product");
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));

    expect(client.validate).toHaveBeenCalledWith("sub-w", "SELECT sku_id, product_name FROM dim_product");
    const report = await screen.findByLabelText("Validation result");
    expect(report).toHaveTextContent("It runs. 2 rows · plan cost 4.5 · 12.5 ms");
    expect(within(report).getByRole("columnheader", { name: "product_name" })).toBeInTheDocument();
    expect(within(report).getByText("NULL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeEnabled();
  });

  it("disables saving again the moment the validated text is edited", async () => {
    mountWrong();
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    await userEvent.type(screen.getByLabelText("Corrected SQL"), " ");

    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeDisabled();
    expect(screen.getByText(/Edited since it was validated/)).toBeInTheDocument();
  });

  it("shows why a query cannot be stored, and keeps saving off", async () => {
    mountWrong({
      validate: vi.fn().mockResolvedValue(
        makeValidation({
          valid: false,
          problems: ['the database refused it: column "nope" does not exist'],
          columns: [],
          rows: [],
          row_count: 0,
          plan_cost: null,
        }),
      ),
    });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));

    const report = await screen.findByRole("alert");
    expect(report).toHaveTextContent("It cannot be stored.");
    expect(report).toHaveTextContent('column "nope" does not exist');
    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeDisabled();
  });

  it("shows warnings, and says when it is showing a sample of the rows", async () => {
    mountWrong({
      validate: vi.fn().mockResolvedValue(
        makeValidation({
          warnings: ["it returned more than 200 rows; only the first 200 were read"],
          rows: [["SKU1", "Whole Milk"]],
          row_count: 200,
          truncated: true,
          plan_cost: null,
        }),
      ),
    });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));

    const report = await screen.findByLabelText("Validation result");
    expect(report).toHaveTextContent("It runs. 200+ rows · 12.5 ms");
    expect(report).toHaveTextContent("more than 200 rows");
    expect(report).toHaveTextContent("First 1 of 200+ rows.");
  });

  it("says it is a sample when it shows fewer rows than came back", async () => {
    mountWrong({
      validate: vi.fn().mockResolvedValue(makeValidation({ rows: [["SKU1", "Milk"]], row_count: 30 })),
    });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    expect(await screen.findByLabelText("Validation result")).toHaveTextContent("First 1 of 30 rows.");
  });

  it("says one row in the singular", async () => {
    mountWrong({ validate: vi.fn().mockResolvedValue(makeValidation({ rows: [["SKU1", "Milk"]], row_count: 1 })) });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    expect(await screen.findByLabelText("Validation result")).toHaveTextContent("It runs. 1 row ·");
  });

  it("adds it to the corrections store and reports what that did", async () => {
    const { client } = mountWrong();
    await openWrong();
    await userEvent.type(screen.getByLabelText("Reviewer"), "ada");
    await userEvent.type(screen.getByLabelText("Review note"), "missing the product name");
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    await userEvent.click(screen.getByRole("button", { name: "Add to corrections" }));

    expect(client.fix).toHaveBeenCalledWith(
      "sub-w",
      "SELECT sku_id FROM dim_product",
      "ada",
      "missing the product name",
    );
    const result = await screen.findByLabelText("Fix result");
    expect(result).toHaveTextContent("W0001 added to the corrections store — 1 rows validated");
    expect(result).toHaveTextContent("embedded — embedded 1; 0 still to embed");
    expect(screen.getByText(/Already fixed as/)).toHaveTextContent("W0001");
    await userEvent.click(within(result).getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("Fix result")).not.toBeInTheDocument();
  });

  it("reports a fix whose vector could not be written as stored, not failed", async () => {
    mountWrong({
      fix: vi.fn().mockResolvedValue(
        makeFixResult({
          embedded: false,
          embed_detail: "FAILED: ConnectionError: no route; 1 still to embed",
          fix: { ...makeFixResult().fix, corrected_truncated: true, embedded: false },
        }),
      ),
    });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    await userEvent.click(screen.getByRole("button", { name: "Add to corrections" }));

    const result = await screen.findByLabelText("Fix result");
    expect(result).toHaveTextContent("1+ rows validated");
    expect(result).toHaveTextContent("not embedded yet — FAILED");
    expect(result).toHaveTextContent("nothing has been lost");
  });

  it("surfaces a refusal from validating or saving", async () => {
    mountWrong({
      validate: vi.fn().mockRejectedValue(new Error("the retail database is down")),
    });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the retail database is down");
  });

  it("surfaces a refusal from saving", async () => {
    mountWrong({ fix: vi.fn().mockRejectedValue("the server re-ran it and it failed") });
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    await userEvent.click(screen.getByRole("button", { name: "Add to corrections" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the server re-ran it and it failed");
  });

  it("cannot validate an empty box", async () => {
    mountWrong({}, wrong({ sql_code: "" }));
    await openWrong();
    expect(screen.getByRole("button", { name: /Validate/ })).toBeDisabled();
  });

  it("judges without sending a golden-pair draft", async () => {
    const { client } = mountWrong();
    await openWrong();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(client.review).toHaveBeenCalledWith("sub-w", { state: "rejected", reviewer: "", review_note: "" }),
    );
  });

  it("says a rejected one has to be accepted before it is fixed", async () => {
    mountWrong({}, wrong({ state: "rejected" }));
    await openWrong();
    expect(screen.getByText(/accept it before fixing it/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeDisabled();
  });

  it("makes an already-fixed submission read-only", async () => {
    mountWrong({}, wrong({ state: "corrected", promoted_pair_id: "W0003" }));
    await userEvent.click(await screen.findByRole("tab", { name: /^Wrong/ }));
    await userEvent.click(await screen.findByText("top 10 SKUs"));
    expect(await screen.findByText(/Already fixed as/)).toHaveTextContent("W0003");
    expect(screen.getByText(/Already fixed as/)).toHaveTextContent(/deletes the fix and gives its SQL back/);
    expect(screen.queryByLabelText("Corrected SQL")).not.toBeInTheDocument();
  });
});

describe("fixing an incomplete answer", () => {
  it("is its own pane, and adds to the completions store", async () => {
    const incomplete = wrong({ id: "sub-i", verdict: "incomplete" });
    const { client } = mountWrong(
      {
        fix: vi.fn().mockResolvedValue(
          makeFixResult({
            kind: "completions",
            fix: { ...makeFixResult().fix, fix_id: "I0001" },
          }),
        ),
      },
      incomplete,
    );
    await openWrong(/^Correct but incomplete/);
    expect(screen.getByRole("heading", { name: "The query that would have been complete" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Validate/ }));
    await screen.findByLabelText("Validation result");
    await userEvent.click(screen.getByRole("button", { name: "Add to completions" }));

    expect(client.fix).toHaveBeenCalledWith("sub-i", "SELECT sku_id FROM dim_product", "", "");
    expect(await screen.findByLabelText("Fix result")).toHaveTextContent(
      "I0001 added to the completions store",
    );
  });
});

describe("the reviewer's name", () => {
  it("is kept across submissions that were never reviewed", async () => {
    mountWrong();
    await userEvent.type(await screen.findByLabelText("Reviewer"), "ada");
    await openWrong();
    expect(screen.getByLabelText("Reviewer")).toHaveValue("ada");
  });
});

describe("taking a judgement back", () => {
  it("puts an accepted submission back at once, and says so", async () => {
    const accepted = { ...makeSubmission({ state: "accepted" }), draft: makeDraft() };
    const { client } = mount({
      submission: vi.fn().mockResolvedValueOnce(accepted).mockResolvedValue({ ...accepted, state: "pending" }),
      reopen: vi.fn().mockResolvedValue(makeUndo({ withdrawn: null })),
    });
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Back to pending" }));

    expect(client.reopen).toHaveBeenCalledWith("sub-1");
    expect(await screen.findByRole("status", { name: "Undo result" })).toHaveTextContent(
      "Back in the queue as pending",
    );
    // The queue and the counts move, and the submission is shown as it now is.
    await waitFor(() => expect(client.submissions).toHaveBeenCalledTimes(2));
    expect(client.meta).toHaveBeenCalledTimes(2);
    expect(client.submission).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Back to pending" })).not.toBeInTheDocument());
  });

  it("takes a promoted pair out of the golden set and gives it back as the draft", async () => {
    const promoted = { ...makeSubmission({ state: "promoted", promoted_pair_id: "Q46" }), draft: makeDraft() };
    const reopened = { ...makeSubmission(), draft: makeDraft({ keywords: "from the golden set" }) };
    const { client } = mount({
      submission: vi.fn().mockResolvedValueOnce(promoted).mockResolvedValue(reopened),
    });
    await userEvent.click(await screen.findByText(/total net sales for Produce/i));
    expect(await screen.findByText(/put it back to pending below/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Title/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Back to pending" }));
    await userEvent.click(screen.getByRole("button", { name: "Take Q46 out and reopen" }));

    expect(client.reopen).toHaveBeenCalledWith("sub-1");
    expect(await screen.findByText("Q46 taken out of the golden set — 46 → 45 pairs.")).toBeInTheDocument();
    expect(await screen.findByLabelText(/^Keywords/)).toHaveValue("from the golden set");
  });

  it("gives a reopened fix's SQL back to edit rather than the agent's", async () => {
    const corrected = wrong({ state: "corrected", promoted_pair_id: "W0003" });
    mountWrong(
      {
        submission: vi
          .fn()
          .mockResolvedValueOnce({ ...corrected, draft: makeDraft() })
          .mockResolvedValue({ ...wrong(), draft: { sql_code: "SELECT sku_id, product_name FROM dim_product" } }),
        reopen: vi.fn().mockResolvedValue(
          makeUndo({ withdrawn: makeWithdrawal({ kind: "corrections", id: "W0003", backup: "", steps: [] }) }),
        ),
      },
      corrected,
    );
    await userEvent.click(await screen.findByRole("tab", { name: /^Wrong/ }));
    await userEvent.click(await screen.findByText("top 10 SKUs"));
    await userEvent.click(await screen.findByRole("button", { name: "Back to pending" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete W0003 and reopen" }));

    expect(await screen.findByLabelText("Corrected SQL")).toHaveValue("SELECT sku_id, product_name FROM dim_product");
    expect(screen.getByText("W0003 deleted from the corrections store, with its vector.")).toBeInTheDocument();
  });

  it("starts a fix from the agent's SQL when the draft carries none", async () => {
    mountWrong({ submission: vi.fn().mockResolvedValue({ ...wrong(), draft: {} }) });
    await openWrong();
    expect(screen.getByLabelText("Corrected SQL")).toHaveValue("SELECT sku_id FROM dim_product");
  });

  it("deletes for good once asked twice, and closes the submission", async () => {
    const { client } = mount();
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));

    expect(client.remove).toHaveBeenCalledWith("sub-1");
    expect(await screen.findByText("Submission deleted")).toBeInTheDocument();
    expect(screen.getByText(/Pick something from the queue/)).toBeInTheDocument();
    await waitFor(() => expect(client.submissions).toHaveBeenCalledTimes(2));
  });

  it.each(["reopen", "remove"] as const)("reports a %s the service refused, and changes nothing on screen", async (call) => {
    const accepted = { ...makeSubmission({ state: "accepted" }), draft: makeDraft() };
    mount({
      submission: vi.fn().mockResolvedValue(accepted),
      [call]: vi.fn().mockRejectedValue(new Error("the corrections store could not be written")),
    });
    await openFirst();
    if (call === "reopen") {
      await userEvent.click(screen.getByRole("button", { name: "Back to pending" }));
    } else {
      await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
      await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    }

    expect(await screen.findByRole("alert")).toHaveTextContent("the corrections store could not be written");
    expect(screen.queryByRole("status", { name: "Undo result" })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Title/)).toBeInTheDocument();
  });

  it("closes the report when it is dismissed, or another submission is opened", async () => {
    const accepted = { ...makeSubmission({ state: "accepted" }), draft: makeDraft() };
    mount({ submission: vi.fn().mockResolvedValue(accepted) });
    await openFirst();

    await userEvent.click(screen.getByRole("button", { name: "Back to pending" }));
    await screen.findByRole("status", { name: "Undo result" });
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("status", { name: "Undo result" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Back to pending" }));
    await screen.findByRole("status", { name: "Undo result" });
    // The queue's entry, which comes before the open submission's own copy.
    await userEvent.click(screen.getAllByText(/total net sales for Produce/i)[0]!);
    await waitFor(() => expect(screen.queryByRole("status", { name: "Undo result" })).not.toBeInTheDocument());
  });

  it("closes the report when the pane changes", async () => {
    mount();
    await openFirst();
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await screen.findByText("Submission deleted");
    await userEvent.click(screen.getByRole("tab", { name: /^Wrong/ }));
    expect(screen.queryByText("Submission deleted")).not.toBeInTheDocument();
  });
});
