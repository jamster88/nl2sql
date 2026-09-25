/**
 * The panels, each against the states it actually meets.
 *
 * The cases worth writing down are the ones where two different situations
 * could render the same and must not: a promotion that reloaded versus one
 * that did not, a submission with no SQL versus one with SQL, and an empty
 * queue versus one that is still loading.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DraftEditor } from "../src/components/DraftEditor";
import { Original } from "../src/components/Original";
import { Promoted } from "../src/components/Promoted";
import { Queue } from "../src/components/Queue";
import { StatusBar } from "../src/components/StatusBar";
import { makeDraft, makeMeta, makePreview, makePromotion, makeSubmission } from "./helpers";

afterEach(cleanup);

const STATES = ["pending", "accepted", "rejected", "promoted"];

describe("Queue", () => {
  const base = {
    submissions: [makeSubmission()],
    counts: { pending: 1, accepted: 2, rejected: 0, promoted: 0 },
    states: STATES,
    state: "pending" as const,
    currentId: undefined,
    onState: vi.fn(),
    onSelect: vi.fn(),
  };

  it("shows a count for every state, including the empty ones", () => {
    render(<Queue {...base} />);
    // A badge that vanishes at zero reads as "no such state".
    expect(screen.getByRole("button", { name: /rejected 0/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /accepted 2/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /all 3/ })).toBeInTheDocument();
  });

  it("marks the filter in force", () => {
    render(<Queue {...base} />);
    expect(screen.getByRole("button", { name: /pending 1/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /all 3/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("changes the filter", async () => {
    const onState = vi.fn();
    render(<Queue {...base} onState={onState} />);
    await userEvent.click(screen.getByRole("button", { name: /accepted 2/ }));
    expect(onState).toHaveBeenCalledWith("accepted");
    await userEvent.click(screen.getByRole("button", { name: /all 3/ }));
    expect(onState).toHaveBeenCalledWith("all");
  });

  it("selects a submission", async () => {
    const onSelect = vi.fn();
    render(<Queue {...base} onSelect={onSelect} />);
    await userEvent.click(screen.getByText(base.submissions[0]!.question));
    expect(onSelect).toHaveBeenCalledWith(base.submissions[0]);
  });

  it("marks the open one", () => {
    render(<Queue {...base} currentId="sub-1" />);
    expect(screen.getByText(base.submissions[0]!.question).closest("button")).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("says the queue is empty rather than showing nothing", () => {
    render(<Queue {...base} submissions={[]} />);
    expect(screen.getByText(/nothing here/i)).toBeInTheDocument();
  });

  it("distinguishes loading from empty", () => {
    render(<Queue {...base} submissions={[]} busy />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing here/i)).not.toBeInTheDocument();
  });

  it("shows both verdicts in the list", () => {
    render(
      <Queue
        {...base}
        submissions={[makeSubmission({ id: "a" }), makeSubmission({ id: "b", verdict: "no" })]}
      />,
    );
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
  });

  it("reads a missing count as zero rather than blank", () => {
    render(<Queue {...base} counts={{}} />);
    expect(screen.getByRole("button", { name: /pending 0/ })).toBeInTheDocument();
  });

  it("flags a comment and a promoted pair id in the row", () => {
    render(
      <Queue
        {...base}
        submissions={[
          makeSubmission({ comment: "off by one", state: "promoted", promoted_pair_id: "Q46" }),
        ]}
      />,
    );
    expect(screen.getByText(/promoted · Q46 · commented/)).toBeInTheDocument();
  });
});

describe("Original", () => {
  it("shows the verdict, the question and the SQL", () => {
    render(<Original submission={makeSubmission()} />);
    expect(screen.getByText("Marked correct")).toBeInTheDocument();
    expect(screen.getByText(/total net sales for Produce/i)).toBeInTheDocument();
    expect(screen.getByText(/SELECT sum\(net_sales_amt\)/)).toBeInTheDocument();
  });

  it("says so when the verdict was negative", () => {
    render(<Original submission={makeSubmission({ verdict: "no" })} />);
    expect(screen.getByText("Marked wrong")).toBeInTheDocument();
  });

  it("explains an absent SQL rather than rendering an empty block", () => {
    // A refusal is a successful run with no query, and an empty <pre> here
    // reads as a rendering bug rather than as the fact it is.
    render(<Original submission={makeSubmission({ sql_code: "" })} />);
    expect(screen.getByText(/no SQL was captured/i)).toBeInTheDocument();
  });

  it("shows a comment when there is one, and nothing when there is not", () => {
    const { unmount } = render(<Original submission={makeSubmission({ comment: "off by one" })} />);
    expect(screen.getByText(/off by one/)).toBeInTheDocument();
    unmount();

    render(<Original submission={makeSubmission()} />);
    expect(screen.queryByText(/they said/i)).not.toBeInTheDocument();
  });

  it("handles a submission with no timestamp and no narrative", () => {
    render(<Original submission={makeSubmission({ submitted_at: null, narrative: "", agent_version: "" })} />);
    expect(screen.getByText(/no timestamp/)).toBeInTheDocument();
  });

  it("shows the review note and who left it", () => {
    render(<Original submission={makeSubmission({ review_note: "needs keywords", reviewer: "sam" })} />);
    expect(screen.getByText(/needs keywords/)).toBeInTheDocument();
    expect(screen.getByText(/— sam/)).toBeInTheDocument();
  });

  it("shows a note with no named reviewer", () => {
    render(<Original submission={makeSubmission({ review_note: "later" })} />);
    expect(screen.getByText(/later/)).toBeInTheDocument();
  });

  it("falls back to a dash for the empty facts", () => {
    render(<Original submission={makeSubmission({ tables: "", intent: "", columns: [] })} />);
    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});

describe("DraftEditor", () => {
  const base = {
    draft: makeDraft(),
    onChange: vi.fn(),
    preview: null,
    suiteInForce: "Suite 25 - Brand loyalty",
  };

  it("marks the three fields no thumbs-up can carry", () => {
    render(<DraftEditor {...base} />);
    expect(screen.getAllByText("yours to write")).toHaveLength(3);
  });

  it("edits a field", async () => {
    const onChange = vi.fn();
    render(<DraftEditor {...base} draft={makeDraft({ title: "" })} onChange={onChange} />);
    await userEvent.type(screen.getByLabelText(/^Title/), "T");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ title: "T" }));
  });

  it("edits a multi-line field", async () => {
    const onChange = vi.fn();
    render(<DraftEditor {...base} draft={makeDraft({ sql_code: "" })} onChange={onChange} />);
    await userEvent.type(screen.getByLabelText(/^SQL/), "S");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ sql_code: "S" }));
  });

  it("edits the suite", async () => {
    const onChange = vi.fn();
    render(<DraftEditor {...base} onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Suite"), "X");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ suite: "X" }));
  });

  it("names the suite a blank field would inherit", () => {
    render(<DraftEditor {...base} />);
    expect(screen.getByText(/leave blank to join "Suite 25 - Brand loyalty"/i)).toBeInTheDocument();
  });

  it("says something useful when no suite is in force yet", () => {
    render(<DraftEditor {...base} suiteInForce="" />);
    expect(screen.getByText(/whatever suite the document ends in/i)).toBeInTheDocument();
  });

  it("lists every problem rather than the first", () => {
    render(
      <DraftEditor
        {...base}
        preview={makePreview({ valid: false, problems: ["keywords is empty", "result spans two lines"] })}
      />,
    );
    expect(screen.getByText("keywords is empty")).toBeInTheDocument();
    expect(screen.getByText("result spans two lines")).toBeInTheDocument();
  });

  it("shows the block that will be written once it parses", () => {
    render(<DraftEditor {...base} preview={makePreview()} />);
    expect(screen.getByText(/Q46, as it will be written/)).toBeInTheDocument();
    expect(screen.getByText(/## Q46 - Total net sales/)).toBeInTheDocument();
  });

  it("binds an input to a string even when the draft is missing the field", () => {
    // The draft column is JSONB written by a browser; a field that is not
    // there must not make React switch the input from uncontrolled to
    // controlled the first time someone types in it.
    const partial = { ...makeDraft(), title: undefined } as unknown as ReturnType<typeof makeDraft>;
    render(<DraftEditor {...base} draft={partial} />);
    expect(screen.getByLabelText(/^Title/)).toHaveValue("");
  });

  it("disables every input while something is in flight", () => {
    render(<DraftEditor {...base} disabled />);
    expect(screen.getByLabelText(/^Title/)).toBeDisabled();
    expect(screen.getByLabelText("Suite")).toBeDisabled();
  });
});

describe("Promoted", () => {
  it("reports a complete promotion", () => {
    render(<Promoted promotion={makePromotion()} onDismiss={vi.fn()} />);
    expect(screen.getByText(/Q46 added to the golden set/)).toBeInTheDocument();
    expect(screen.getByText(/45 → 46 pairs/)).toBeInTheDocument();
    expect(screen.queryByText(/have not caught up/i)).not.toBeInTheDocument();
  });

  it("says plainly when the stores did not catch up", () => {
    // The pair is in the source of truth either way; a tick would be a lie
    // and a cross would be a bigger one.
    render(
      <Promoted
        promotion={makePromotion({
          reloaded: false,
          steps: [
            { name: "load_golden_pairs", ran: true, ok: true, detail: "" },
            { name: "embed_golden_pairs", ran: true, ok: false, detail: "no embedding host" },
          ],
        })}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText(/have not caught up/i)).toBeInTheDocument();
    expect(screen.getByText(/no embedding host/)).toBeInTheDocument();
  });

  it("distinguishes a skipped step from a failed one", () => {
    render(
      <Promoted
        promotion={makePromotion({
          reloaded: false,
          backup: "",
          suite: "",
          steps: [{ name: "embed_golden_pairs", ran: false, ok: true, detail: "" }],
        })}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText(/skipped/)).toBeInTheDocument();
  });

  it("can be dismissed", async () => {
    const onDismiss = vi.fn();
    render(<Promoted promotion={makePromotion()} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("StatusBar", () => {
  it("shows what the service is", () => {
    render(<StatusBar meta={makeMeta()} error={null} warnings={[]} />);
    expect(screen.getByText("nl2sql-review 4.3.0")).toBeInTheDocument();
    expect(screen.getByText(/45 golden pairs · next Q46 · max Q99/)).toBeInTheDocument();
  });

  it("shows a dash when there is no next id to offer", () => {
    render(<StatusBar meta={makeMeta({ next_pair_id: "" })} error={null} warnings={[]} />);
    expect(screen.getByText(/next — · max Q99/)).toBeInTheDocument();
  });

  it("reports the reload switches, which are otherwise invisible", () => {
    const { unmount } = render(
      <StatusBar meta={makeMeta({ reload_vectors: false })} error={null} warnings={[]} />,
    );
    expect(screen.getByText(/reload context on · vectors off/)).toBeInTheDocument();
    unmount();

    // Context off is the more serious of the two -- a promotion then never
    // reaches the agent at all -- so both spellings are pinned down.
    render(<StatusBar meta={makeMeta({ reload_context: false })} error={null} warnings={[]} />);
    expect(screen.getByText(/reload context off · vectors on/)).toBeInTheDocument();
  });

  it("shows warnings and an unreachable service", () => {
    render(
      <StatusBar meta={null} error={new Error("connection refused")} warnings={["No REVIEW_TOKEN is set"]} />,
    );
    expect(screen.getByText(/connection refused/)).toBeInTheDocument();
    expect(screen.getByText("No REVIEW_TOKEN is set")).toBeInTheDocument();
  });
});
