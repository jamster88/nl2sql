/**
 * The snippet pane, against a fake service.
 *
 * What is pinned down is what protects the agent's prompt: nothing is saved
 * until the exact kind and SQL on screen have passed against the live
 * database, a write says what it did to the document and to the store, and
 * removing a snippet asks first and says where it is removed from.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SnippetsPane } from "../src/components/SnippetsPane";
import {
  fakeClient,
  makeSnippet,
  makeSnippetPreview,
  makeSnippetResult,
  makeSnippetSet,
  makeSnippetValidation,
} from "./helpers";

afterEach(cleanup);

function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  const onChanged = vi.fn();
  render(<SnippetsPane client={client} onChanged={onChanged} />);
  return { client, onChanged };
}

async function fill(label: RegExp, value: string) {
  const input = screen.getByLabelText(label);
  await userEvent.clear(input);
  if (value) await userEvent.type(input, value);
}

async function newHolidays() {
  await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
  await fill(/^Name/, "Holidays");
  await fill(/^Means/, "Restricts to holidays.");
  await fill(/^Keywords/, "holiday sales");
  await fill(/^Applies to/, "dim_date d");
  await fill(/^SQL/, "d.is_holiday");
}

describe("the list", () => {
  it("shows the document's snippets, the store beside them, and filters by kind and words", async () => {
    mount();
    expect(await screen.findByText("Sales on the fiscal calendar")).toBeInTheDocument();
    expect(screen.getByLabelText("Snippet store")).toHaveTextContent("Store: 2 snippets, all embedded");
    const kinds = screen.getByRole("group", { name: "Filter by kind" });
    expect(within(kinds).getByRole("button", { name: /all\s*2/ })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(within(kinds).getByRole("button", { name: /measure\s*1/ }));
    expect(screen.queryByText("Sales on the fiscal calendar")).not.toBeInTheDocument();
    expect(screen.getByText("Net sales")).toBeInTheDocument();

    await userEvent.click(within(kinds).getByRole("button", { name: /all/ }));
    await userEvent.type(screen.getByLabelText("Search snippets"), "fiscal calendar");
    expect(screen.queryByText("Net sales")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search snippets"), " nothing");
    expect(screen.getByText("No snippet matches.")).toBeInTheDocument();
  });

  it("says when the store is behind its document, or cannot be reached", async () => {
    const behind = makeSnippetSet({ store: { reachable: true, snippets: 1, embedded: 0, current: false, detail: "behind" } });
    mount({ snippets: vi.fn().mockResolvedValue(behind) });
    expect(await screen.findByLabelText("Snippet store")).toHaveClass("store-behind");
    cleanup();
    mount({ snippets: vi.fn().mockResolvedValue(makeSnippetSet({ store: { reachable: false, snippets: 0, embedded: 0, current: false, detail: "refused" } })) });
    expect(await screen.findByLabelText("Snippet store")).toHaveTextContent("The snippet store cannot be reached: refused");
  });

  it("reports a document that does not parse, and a service that is down", async () => {
    mount({ snippets: vi.fn().mockResolvedValue(makeSnippetSet({ snippets: [], count: 0, error: "does not parse" })) });
    expect(await screen.findByText("does not parse")).toBeInTheDocument();
    cleanup();
    mount({ snippets: vi.fn().mockRejectedValue(new Error("cannot reach the review service")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot reach the review service");
  });

  it("explains what a snippet is before one is open", async () => {
    mount();
    expect(await screen.findByText(/A snippet is one piece of SQL/)).toBeInTheDocument();
  });
});

describe("writing one", () => {
  it("cannot be saved until it has passed, and passing is for the exact SQL on screen", async () => {
    const { client, onChanged } = mount();
    await newHolidays();
    const add = screen.getByRole("button", { name: "Add snippet" });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", "Validate it against the live database first; it has to pass");

    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText(/It runs\./)).toBeInTheDocument();
    expect(screen.getByText(/32 of 731 rows match/)).toBeInTheDocument();
    expect(add).toBeEnabled();

    await userEvent.type(screen.getByLabelText(/^SQL/), " AND true");
    expect(add).toBeDisabled();
    expect(screen.getByText(/Edited since it was validated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await waitFor(() => expect(add).toBeEnabled());

    // The words around the SQL are not what was validated: editing them keeps it saveable.
    await userEvent.type(screen.getByLabelText(/^Note/), "from the calendar");
    expect(add).toBeEnabled();

    await userEvent.click(add);
    expect(await screen.findByText(/Added S33 in the snippet document — 33 snippets now\./)).toBeInTheDocument();
    expect(screen.getByText(/the agent is shown it from the next question/)).toBeInTheDocument();
    expect(screen.getByText("load_snippets")).toBeInTheDocument();
    expect(client.addSnippet).toHaveBeenCalledWith(expect.objectContaining({ name: "Holidays", sql: "d.is_holiday AND true", note: "from the calendar" }));
    expect(onChanged).toHaveBeenCalled();
    expect(client.snippets).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Added S33/)).not.toBeInTheDocument();
  });

  it("names the fields still empty rather than asking for a validation", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    await fill(/^Applies to/, "dim_date d");
    await fill(/^SQL/, "d.is_holiday");
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText(/It runs\./);
    expect(screen.getByRole("button", { name: "Add snippet" })).toHaveAttribute("title", "Fill in name, keywords, means first");
  });

  it("cannot be validated without both pieces of SQL", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    const validate = screen.getByRole("button", { name: "Validate against the live database" });
    expect(validate).toBeDisabled();
    await fill(/^Applies to/, "dim_date d");
    expect(validate).toBeDisabled();
  });

  it("shows why it does not run, and the query it was checked inside", async () => {
    const refused = makeSnippetValidation({ valid: false, problems: ["the database refused it: no column d.holiday"], rows: [], columns: [], rows_before: null, rows_after: null, tables: [], plan_cost: null })
    mount({ validateSnippet: vi.fn().mockResolvedValue(refused) });
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText("It cannot be saved.")).toBeInTheDocument();
    expect(screen.getByText("the database refused it: no column d.holiday")).toBeInTheDocument();
    expect(screen.getByText("The query it was checked inside")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add snippet" })).toBeDisabled();
  });

  it("says how a join changed the row count and passes on the warnings", async () => {
    const fanout = makeSnippetValidation({ kind: "join", rows_before: 10, rows_after: 50, warnings: ["the join multiplies rows"], plan_cost: null, probe_sql: "" });
    mount({ validateSnippet: vi.fn().mockResolvedValue(fanout) });
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    expect(await screen.findByText(/10 rows before the join, 50 after/)).toBeInTheDocument();
    expect(screen.getByText("the join multiplies rows")).toBeInTheDocument();
    expect(screen.queryByText("The query it was checked inside")).not.toBeInTheDocument();
  });

  it("reports a measure without counts", async () => {
    const measure = makeSnippetValidation({ kind: "measure", rows_before: null, rows_after: null, tables: [] });
    mount({ validateSnippet: vi.fn().mockResolvedValue(measure) });
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    const verdict = (await screen.findByText("It runs.")).closest("p");
    expect(verdict).toHaveTextContent(/^It runs\. · plan cost 12\.5 · 4\.2 ms$/);
  });

  it("offers to list the tables the SQL uses, and the schema to pick them from", async () => {
    const uses = makeSnippetValidation({ tables: ["dim_date", "fact_pos_retail_sales"] });
    mount({ validateSnippet: vi.fn().mockResolvedValue(uses) });
    await newHolidays();
    await userEvent.click(screen.getByText("Tables in the retail database"));
    await userEvent.click(screen.getByRole("button", { name: "dim_date" }));
    expect(screen.getByLabelText(/^Tables/)).toHaveValue("dim_date");
    await userEvent.click(screen.getByRole("button", { name: "dim_date" }));
    expect(screen.getByLabelText(/^Tables/)).toHaveValue("dim_date");

    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await userEvent.click(await screen.findByRole("button", { name: "List these tables" }));
    expect(screen.getByLabelText(/^Tables/)).toHaveValue("dim_date, fact_pos_retail_sales");
    expect(screen.queryByRole("button", { name: "List these tables" })).not.toBeInTheDocument();
  });

  it("shows the schema's error in place of the tables", async () => {
    mount({ schema: vi.fn().mockRejectedValue(new Error("refused")) });
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    await userEvent.click(await screen.findByText("Tables in the retail database"));
    expect(screen.getByText("refused")).toBeInTheDocument();
  });

  it("asks for the schema once, however many snippets are opened", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    await screen.findByText("Tables in the retail database");
    await userEvent.click(screen.getByText("Net sales"));
    expect(client.schema).toHaveBeenCalledTimes(1);
  });

  it("previews the section, or the reasons it cannot be written", async () => {
    const previewSnippet = vi
      .fn()
      .mockResolvedValueOnce(makeSnippetPreview())
      .mockResolvedValueOnce(makeSnippetPreview({ valid: false, markdown: "", problems: ["means is empty"] }));
    const { client } = mount({ previewSnippet });
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/## S33 - Holidays/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("means is empty")).toBeInTheDocument();
    expect(client.previewSnippet).toHaveBeenCalledWith(expect.objectContaining({ name: "Holidays" }), undefined);
  });

  it("shows the hint for each kind and none for a kind it does not know", async () => {
    mount({ snippets: vi.fn().mockResolvedValue(makeSnippetSet({ kinds: ["join", "filter", "measure", "dimension", "window"] })) });
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    expect(screen.getByText(/A WHERE condition, without the WHERE/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "join");
    expect(screen.getByText(/A JOIN clause, aliases and all/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "window");
    expect(screen.queryByText(/for example/)).not.toBeInTheDocument();
  });

  it("says what went wrong when a save is refused, and keeps the form", async () => {
    mount({ addSnippet: vi.fn().mockRejectedValue(new Error("writing S33 would change other snippets as well")) });
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText(/It runs\./);
    await userEvent.click(screen.getByRole("button", { name: "Add snippet" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("writing S33 would change other snippets as well");
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Holidays");
  });

  it("closes without writing", async () => {
    const { client } = mount();
    await newHolidays();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText(/^Name/)).not.toBeInTheDocument();
    expect(client.addSnippet).not.toHaveBeenCalled();
  });
});

describe("changing and removing one", () => {
  it("opens a snippet as its draft and saves it in place", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByText("Net sales"));
    expect(screen.getByRole("heading", { name: "Snippet S19" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Keywords/)).toHaveValue("net sales, revenue");
    expect(screen.getByText("Net sales").closest("button")).toHaveAttribute("aria-current", "true");

    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText(/It runs\./);
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(/Saved S19 in the snippet document — 32 snippets now\./)).toBeInTheDocument();
    expect(client.changeSnippet).toHaveBeenCalledWith("S19", expect.objectContaining({ kind: "measure" }));

    await userEvent.click(screen.getByText("Net sales"));
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(client.previewSnippet).toHaveBeenLastCalledWith(expect.any(Object), "S19");
  });

  it("asks before removing, says where from, and can be called off", async () => {
    const { client } = mount();
    await userEvent.click(await screen.findByText("Net sales"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "Takes S19 out of context_questions/sql_snippets.md, keeping the previous version beside it, and out of the snippet store",
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(client.removeSnippet).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove S19" }));
    expect(await screen.findByText(/Removed S19 from the snippet document — 31 snippets now\./)).toBeInTheDocument();
    // Said of a removal, the add's "shown it" would be the opposite of true.
    expect(screen.getByText(/the agent is not shown it from the next question/)).toBeInTheDocument();
    expect(client.removeSnippet).toHaveBeenCalledWith("S19");
  });

  it("says a write reached the document but not the store", async () => {
    const partial = makeSnippetResult({ reloaded: false, steps: [{ name: "load_snippets", ran: true, ok: false, detail: "cannot reach ollama" }] });
    mount({ changeSnippet: vi.fn().mockResolvedValue(partial) });
    await userEvent.click(await screen.findByText("Net sales"));
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText(/It runs\./);
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(/the store catches up at the next load/)).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });

  it("is not offered for a snippet that has not been written yet", async () => {
    mount({ snippets: vi.fn().mockResolvedValue(makeSnippetSet({ snippets: [makeSnippet()], count: 1 })) });
    await userEvent.click(await screen.findByRole("button", { name: "New snippet" }));
    expect(screen.queryByRole("button", { name: "Remove…" })).not.toBeInTheDocument();
  });
});
