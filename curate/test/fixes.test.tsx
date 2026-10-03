/**
 * The corrections-and-completions pane, against a fake service.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FixesPane, consequence } from "../src/components/FixesPane";
import { fakeClient, makeFix, makeFixRemoval, makeFixResult, makeSubmission, makeValidation } from "./helpers";

afterEach(cleanup);

function mount(overrides: Parameters<typeof fakeClient>[0] = {}, reviewer = "ann") {
  const client = fakeClient(overrides);
  const onChanged = vi.fn();
  render(<FixesPane client={client} reviewer={reviewer} onChanged={onChanged} />);
  return { client, onChanged };
}

async function writeFix() {
  await userEvent.click(await screen.findByRole("button", { name: "New correction" }));
  await userEvent.type(screen.getByLabelText("Question"), "top 10 SKUs");
  await userEvent.type(screen.getByLabelText("The query that answers it"), "SELECT sku_id, product_name FROM dim_product");
}

describe("the stores", () => {
  it("lists a store's fixes and switches between the two", async () => {
    const { client } = mount();
    expect(await screen.findByText("top 10 SKUs by net sales")).toBeInTheDocument();
    expect(screen.getByText("1 in the corrections store, newest first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Completions" }));
    expect(client.fixes).toHaveBeenLastCalledWith("completions");
    expect(screen.getByRole("button", { name: "New completion" })).toBeInTheDocument();
  });

  it("shows a fix whole, saying where it came from", async () => {
    mount({
      fixes: vi.fn().mockResolvedValue({
        kind: "corrections",
        fixes: [makeFix(), makeFix({ fix_id: "W0002", submission_id: "sub-w", reviewer: "", embedded: false, incorrect_sql: "", review_note: "", source: "review" })],
        count: 2,
      }),
    });
    await userEvent.click(await screen.findByText("W0001"));
    const curated = screen.getByRole("region", { name: "Fix W0001" });
    expect(curated).toHaveTextContent("Written by hand · ann · embedded");
    expect(curated).toHaveTextContent("What the agent wrote:");
    expect(curated).toHaveTextContent("names, not ids");
    await userEvent.click(screen.getByText("W0002"));
    const reviewed = screen.getByRole("region", { name: "Fix W0002" });
    expect(reviewed).toHaveTextContent("From submission sub-w · not embedded yet");
    expect(reviewed).not.toHaveTextContent("What the agent wrote:");
  });

  it("reports a store that cannot be read", async () => {
    mount({ fixes: vi.fn().mockRejectedValue(new Error("the corrections store could not be read")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read");
  });

  it("explains itself before anything is open", async () => {
    mount();
    expect(await screen.findByText(/a record of a mistake is not a benchmark answer/)).toBeInTheDocument();
  });
});

describe("adding one", () => {
  it("needs the exact queries on screen to have passed, and names the curator", async () => {
    const { client, onChanged } = mount();
    await writeFix();
    const add = screen.getByRole("button", { name: "Add to corrections" });
    expect(add).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText("It runs.")).toBeInTheDocument();
    expect(add).toBeEnabled();
    expect(client.validateFix).toHaveBeenCalledWith("corrections", "SELECT sku_id, product_name FROM dim_product", "");

    // What it corrects is part of what was validated: a fix has to differ from it.
    await userEvent.type(screen.getByLabelText("What the agent wrote, if you have it"), "SELECT sku_id FROM dim_product");
    expect(add).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await userEvent.type(screen.getByLabelText("Note"), "names, not ids");

    await userEvent.click(add);
    expect(await screen.findByText(/Stored W0002 in the corrections store\./)).toBeInTheDocument();
    expect(screen.getByText(/Its question is embedded\./)).toBeInTheDocument();
    expect(client.addFix).toHaveBeenCalledWith(
      "corrections",
      { question: "top 10 SKUs", sql: "SELECT sku_id, product_name FROM dim_product", incorrect_sql: "SELECT sku_id FROM dim_product", review_note: "names, not ids" },
      "ann",
    );
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Stored W0002/)).not.toBeInTheDocument();
  });

  it("needs a question, and a query to validate", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "New correction" }));
    expect(screen.getByRole("button", { name: "Validate against the live database" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("The query that answers it"), "SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText("It runs.");
    expect(screen.getByRole("button", { name: "Add to corrections" })).toBeDisabled();
  });

  it("says why a query cannot be stored, and that one was stored without its vector", async () => {
    mount({ validateFix: vi.fn().mockResolvedValue(makeValidation({ valid: false, problems: ["this is the query the agent generated; a fix has to change it"], columns: [], rows: [], plan_cost: null })) });
    await writeFix();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText(/a fix has to change it/)).toBeInTheDocument();
    cleanup();

    mount({ addFix: vi.fn().mockResolvedValue(makeFixResult({ embedded: false, embed_detail: "not embedded: down" })) });
    await writeFix();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText("It runs.");
    await userEvent.click(screen.getByRole("button", { name: "Add to corrections" }));
    expect(await screen.findByText(/Not embedded yet: not embedded: down\./)).toBeInTheDocument();
  });

  it("closes without writing", async () => {
    const { client } = mount();
    await writeFix();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    expect(client.addFix).not.toHaveBeenCalled();
  });
});

describe("taking one out", () => {
  it("asks first, then deletes a fix written by hand", async () => {
    const { client, onChanged } = mount();
    await userEvent.click(await screen.findByText("W0001"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Deletes W0001 and its vector from the corrections store.");
    await userEvent.click(screen.getByRole("button", { name: "Remove W0001" }));
    expect(await screen.findByText("Deleted W0001 from the corrections store.")).toBeInTheDocument();
    expect(client.removeFix).toHaveBeenCalledWith("corrections", "W0001");
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  });

  it("says a reviewed fix's submission goes back, and one already gone was gone", async () => {
    mount({
      fixes: vi.fn().mockResolvedValue({ kind: "corrections", fixes: [makeFix({ submission_id: "sub-w" })], count: 1 }),
      removeFix: vi.fn().mockResolvedValue(makeFixRemoval({ found: false, submission: makeSubmission({ id: "sub-w" }) })),
    });
    await userEvent.click(await screen.findByText("W0001"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("submission sub-w goes back to pending there");
    await userEvent.click(screen.getByRole("button", { name: "Remove W0001" }));
    expect(await screen.findByText(/W0001 was already gone from the corrections store\./)).toBeInTheDocument();
    expect(screen.getByText(/Submission sub-w is back in the review queue, pending\./)).toBeInTheDocument();
  });

  it("says why a removal did not work, and keeps the fix on screen", async () => {
    mount({ removeFix: vi.fn().mockRejectedValue(new Error("the corrections store could not be written")) });
    await userEvent.click(await screen.findByText("W0001"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove W0001" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be written");
    expect(screen.getByRole("region", { name: "Fix W0001" })).toBeInTheDocument();
  });

  it("words the consequence from the fix alone", () => {
    expect(consequence("completions", makeFix({ fix_id: "I0001" }))).toBe("Deletes I0001 and its vector from the completions store.");
  });
});
