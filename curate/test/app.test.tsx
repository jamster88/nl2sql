/**
 * The application: the tabs, the curator's name, and the status bar.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App, TABS } from "../src/App";
import { fakeClient, makeMeta } from "./helpers";

afterEach(cleanup);

function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  render(<App client={client} />);
  return client;
}

describe("the tabs", () => {
  it("opens on the snippets and moves between the three", async () => {
    const client = mount();
    expect(TABS.map((t) => t.title)).toEqual(["SQL snippets", "Golden pairs", "Corrections & completions"]);
    expect(await screen.findByRole("tab", { name: "SQL snippets" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("tabpanel", { name: "SQL snippets" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Golden pairs" }));
    expect(await screen.findByText("Daily net sales for one store")).toBeInTheDocument();
    expect(client.golden).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("tab", { name: "Corrections & completions" }));
    expect(await screen.findByText("top 10 SKUs by net sales")).toBeInTheDocument();
  });

  it("sends the curator's name with a fix", async () => {
    const client = mount();
    await userEvent.type(screen.getByLabelText("Curator"), "ann");
    await userEvent.click(screen.getByRole("tab", { name: "Corrections & completions" }));
    await userEvent.click(await screen.findByRole("button", { name: "New correction" }));
    await userEvent.type(screen.getByLabelText("Question"), "q");
    await userEvent.type(screen.getByLabelText("The query that answers it"), "SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText("It runs.");
    await userEvent.click(screen.getByRole("button", { name: "Add to corrections" }));
    await screen.findByText(/Stored W0002/);
    expect(client.addFix).toHaveBeenCalledWith("corrections", expect.any(Object), "ann");
  });
});

describe("the status bar", () => {
  it("says what the service is, and its warnings", async () => {
    mount({ meta: vi.fn().mockResolvedValue(makeMeta({ golden_count: 1, warnings: ["No REVIEW_TOKEN is set"] })) });
    const bar = await screen.findByRole("contentinfo");
    expect(await within(bar).findByText("nl2sql-review 6.0.1")).toBeInTheDocument();
    expect(within(bar).getByText("1 golden pair")).toBeInTheDocument();
    expect(within(bar).getByText("2 corrections · 1 completion")).toBeInTheDocument();
    expect(within(bar).getByText("auth bearer")).toBeInTheDocument();
    expect(within(bar).getByText("No REVIEW_TOKEN is set")).toBeInTheDocument();
  });

  it("counts stores the service did not report as empty", async () => {
    mount({ meta: vi.fn().mockResolvedValue(makeMeta({ fixes: {} })) });
    expect(await screen.findByText("0 corrections · 0 completions")).toBeInTheDocument();
  });

  it("says when the service cannot be reached, and recovers after a write", async () => {
    const meta = vi.fn().mockRejectedValueOnce(new Error("refused")).mockResolvedValue(makeMeta());
    mount({ meta });
    expect(await screen.findByText("Cannot reach the review service: refused")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Net sales"));
    await userEvent.click(screen.getByRole("button", { name: "Validate against the live database" }));
    await screen.findByText(/It runs\./);
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("nl2sql-review 6.0.1")).toBeInTheDocument();
    expect(screen.queryByText(/Cannot reach the review service/)).not.toBeInTheDocument();
  });

  it("reports something thrown that is not an Error", async () => {
    mount({ meta: vi.fn().mockRejectedValue("a string") });
    expect(await screen.findByText("Cannot reach the review service: a string")).toBeInTheDocument();
  });
});

describe("its default client", () => {
  it("talks to the page's own origin", async () => {
    // A new response per call: a body can be read once, and the panes ask too.
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(makeMeta()), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    try {
      render(<App />);
      expect(await screen.findByText("nl2sql-review 6.0.1")).toBeInTheDocument();
      expect(fetch.mock.calls.map((call) => call[0])).toContain("/v1/meta");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
