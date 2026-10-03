/**
 * The golden-pair pane, against a fake service.
 *
 * A pair is the benchmark, so: nothing is added until its SQL has passed,
 * for the exact SQL on screen; and taking one out says, before it happens,
 * whether a submission goes back to the review queue with it.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GoldenPane, consequence } from "../src/components/GoldenPane";
import {
  fakeClient,
  makeGolden,
  makeGoldenRemoval,
  makeGoldenResult,
  makePair,
  makePreview,
  makeSubmission,
  makeValidation,
} from "./helpers";

afterEach(cleanup);

function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  const onChanged = vi.fn();
  render(<GoldenPane client={client} onChanged={onChanged} />);
  return { client, onChanged };
}

async function writePair() {
  await userEvent.click(await screen.findByRole("button", { name: "New pair" }));
  await userEvent.type(screen.getByLabelText("Title"), "Stores");
  await userEvent.type(screen.getByLabelText("Question"), "How many stores are there?");
  await userEvent.type(screen.getByLabelText("SQL"), "SELECT count(*) FROM dim_store");
}

describe("the set", () => {
  it("lists the pairs, how many, the next id, and searches them", async () => {
    mount();
    expect(await screen.findByText("Daily net sales for one store")).toBeInTheDocument();
    expect(screen.getByText("2 pairs · next Q49")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search golden pairs"), "how many");
    expect(screen.queryByText("Daily net sales for one store")).not.toBeInTheDocument();
    expect(screen.getByText("Promoted")).toBeInTheDocument();
  });

  it("reports a document that cannot be read, and a service that is down", async () => {
    mount({ golden: vi.fn().mockResolvedValue(makeGolden({ pairs: [], count: 0, next_pair_id: "", error: "cannot parse: x" })) });
    expect(await screen.findByText("cannot parse: x")).toBeInTheDocument();
    expect(screen.getByText("0 pairs · next —")).toBeInTheDocument();
    cleanup();
    mount({ golden: vi.fn().mockRejectedValue(new Error("down")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });

  it("shows a pair whole, saying where it came from", async () => {
    mount();
    await userEvent.click(await screen.findByText("Daily net sales for one store"));
    const record = screen.getByRole("region", { name: "Pair Q01" });
    expect(record).toHaveTextContent("The date column is sales_date_key.");
    expect(record).toHaveTextContent("written by hand");
    await userEvent.click(screen.getByText("Promoted"));
    expect(screen.getByRole("region", { name: "Pair Q47" })).toHaveTextContent("from submission sub-1");
  });

  it("explains itself before anything is open", async () => {
    mount();
    expect(await screen.findByText(/what the agent's worked examples come from/)).toBeInTheDocument();
  });
});

describe("adding one", () => {
  it("needs the exact SQL on screen to have run and returned rows", async () => {
    const { client, onChanged } = mount();
    await writePair();
    const add = screen.getByRole("button", { name: "Add to golden set" });
    expect(add).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText("It runs.")).toBeInTheDocument();
    expect(add).toBeEnabled();
    expect(client.validateGolden).toHaveBeenCalledWith("SELECT count(*) FROM dim_store");

    await userEvent.type(screen.getByLabelText("SQL"), " WHERE true");
    expect(add).toBeDisabled();
    expect(screen.getByText(/Edited since it was validated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await userEvent.type(screen.getByLabelText("Reasoning target"), "counting a dimension");

    await userEvent.click(add);
    expect(await screen.findByText(/Added Q49 to the golden set — 49 pairs now\./)).toBeInTheDocument();
    expect(screen.getByText(/Both stores are reloaded/)).toBeInTheDocument();
    expect(client.addGolden).toHaveBeenCalledWith(expect.objectContaining({ title: "Stores", reasoning_target: "counting a dimension" }));
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Added Q49/)).not.toBeInTheDocument();
  });

  it("cannot be validated with no SQL, and says why one that does not run cannot be added", async () => {
    mount({ validateGolden: vi.fn().mockResolvedValue(makeValidation({ valid: false, problems: ["it ran and returned no rows"], columns: [], rows: [], row_count: 0, plan_cost: null })) });
    await userEvent.click(await screen.findByRole("button", { name: "New pair" }));
    const validate = screen.getByRole("button", { name: "Validate against the live database" });
    expect(validate).toBeDisabled();
    await userEvent.type(screen.getByLabelText("SQL"), "SELECT 1 WHERE false");
    await userEvent.click(validate);
    expect(await screen.findByText("it ran and returned no rows")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to golden set" })).toBeDisabled();
  });

  it("previews the block, or why it would not parse", async () => {
    const previewGolden = vi.fn().mockResolvedValueOnce(makePreview()).mockResolvedValueOnce(makePreview({ valid: false, markdown: "", problems: ["keywords is empty"] }));
    mount({ previewGolden });
    await writePair();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/## Q49 - New/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("keywords is empty")).toBeInTheDocument();
  });

  it("says a pair reached the document but not the stores, and when a write was refused", async () => {
    mount({ addGolden: vi.fn().mockResolvedValue(makeGoldenResult({ reloaded: false })) });
    await writePair();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText("It runs.");
    await userEvent.click(screen.getByRole("button", { name: "Add to golden set" }));
    expect(await screen.findByText(/the stores catch up at the next load/)).toBeInTheDocument();
    cleanup();

    mount({ addGolden: vi.fn().mockRejectedValue(new Error("not_promotable")) });
    await writePair();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText("It runs.");
    await userEvent.click(screen.getByRole("button", { name: "Add to golden set" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not_promotable");
  });

  it("names the next pair when it knows it, and closes without writing", async () => {
    const { client } = mount({ golden: vi.fn().mockResolvedValue(makeGolden({ next_pair_id: "" })) });
    await userEvent.click(await screen.findByRole("button", { name: "New pair" }));
    expect(screen.getByText(/Written into the golden set as the next pair/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(client.addGolden).not.toHaveBeenCalled();
  });
});

describe("taking one out", () => {
  it("says what happens to a pair written by hand, then does it", async () => {
    const { client, onChanged } = mount();
    await userEvent.click(await screen.findByText("Daily net sales for one store"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Takes Q01 out of context_questions/translated_questions.md");
    expect(screen.getByRole("alertdialog")).not.toHaveTextContent("review queue");
    await userEvent.click(screen.getByRole("button", { name: "Remove Q01" }));
    expect(await screen.findByText("Took Q01 out of the golden set.")).toBeInTheDocument();
    expect(client.removeGolden).toHaveBeenCalledWith("Q01");
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Took Q01 out of the golden set.")).not.toBeInTheDocument();
  });

  it("says a promoted pair's submission goes back to the queue, and that it went", async () => {
    mount({ removeGolden: vi.fn().mockResolvedValue(makeGoldenRemoval({ pair_id: "Q47", submission: makeSubmission() })) });
    await userEvent.click(await screen.findByText("Promoted"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("submission sub-1 goes back to pending there");
    await userEvent.click(screen.getByRole("button", { name: "Remove Q47" }));
    expect(await screen.findByText(/Submission sub-1 is back in the review queue, pending\./)).toBeInTheDocument();
  });

  it("words the consequence from the pair alone", () => {
    expect(consequence(makePair({ submission_id: "s9" }))).toMatch(/submission s9 goes back to pending/);
  });
});
