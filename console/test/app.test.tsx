/**
 * The application, against a fake console.
 *
 * What is pinned here is the loop a troubleshooting session actually runs:
 * pick a table or paste the agent's query, run it one of three ways, read
 * the verdict beside the rows or the plan, look at what the agent's prompt
 * said about a table, and come back to an earlier query.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { ApiError } from "../src/api/client";
import { HISTORY_KEY } from "../src/api/history";
import type { QueryResult } from "../src/api/types";
import { fakeClient, makeMeta, makePrompt, makeResult, makeSchema, makeTable } from "./helpers";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function mount(overrides: Parameters<typeof fakeClient>[0] = {}) {
  const client = fakeClient(overrides);
  render(<App client={client} />);
  return client;
}

async function typeQuery(sql: string) {
  const editor = screen.getByLabelText("SQL");
  await userEvent.clear(editor);
  await userEvent.type(editor, sql);
}

describe("on opening", () => {
  it("shows what it is connected to and the schema", async () => {
    mount();
    expect(await screen.findByText(/nl2sql_retail as/)).toBeInTheDocument();
    expect(await screen.findByText("dim_store")).toBeInTheDocument();
  });

  it("says when the console cannot be reached", async () => {
    mount({ meta: vi.fn(async () => Promise.reject(new ApiError(0, "unreachable", "cannot reach the console: down"))) });
    expect(await screen.findByText("Cannot reach the console: cannot reach the console: down")).toBeInTheDocument();
  });

  it("says when the schema cannot be read", async () => {
    mount({ schema: vi.fn(async () => Promise.reject("database_unavailable")) });
    expect(await screen.findByText("database_unavailable")).toHaveClass("error");
  });

  it("takes the editor's limit from the console once it knows it", async () => {
    mount({ meta: vi.fn(async () => makeMeta({ limits: { ...makeMeta().limits, max_sql_length: 500 } })) });
    await waitFor(() => expect(screen.getByLabelText("SQL")).toHaveAttribute("maxLength", "500"));
  });

  it("builds its own client when none is given", async () => {
    const fetch = vi.fn(async (url: RequestInfo | URL) =>
      new Response(JSON.stringify(String(url) === "/v1/meta" ? makeMeta() : makeSchema()), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetch);
    try {
      render(<App />);
      await waitFor(() => expect(fetch).toHaveBeenCalledWith("/v1/meta", expect.anything()));
      expect(await screen.findByText("dim_store")).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("running a query", () => {
  it("runs what was typed, trimmed, and shows the verdict and the rows", async () => {
    const client = mount();
    await typeQuery("  SELECT store_key FROM dim_store  ");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(client.query).toHaveBeenCalledWith("SELECT store_key FROM dim_store", "run");
    expect(await screen.findByText("The agent would accept this query")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Rows" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Thrift & Table - Ashland")).toBeInTheDocument();
  });

  it("opens on the plan after Plan, with no rows tab to offer", async () => {
    const client = mount({ query: vi.fn(async () => makeResult({ mode: "plan", executed: false, columns: [], rows: [] })) });
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Plan" }));

    expect(client.query).toHaveBeenCalledWith("SELECT 1", "plan");
    expect(await screen.findByRole("region", { name: "Plan" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Plan" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Rows" })).not.toBeInTheDocument();
  });

  it("opens on the plan after Analyze too", async () => {
    const client = mount({ query: vi.fn(async () => makeResult({ mode: "analyze", columns: [], rows: [] })) });
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(client.query).toHaveBeenCalledWith("SELECT 1", "analyze");
    expect(await screen.findByRole("region", { name: "Plan" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Rows" })).not.toBeInTheDocument();
  });

  it("moves between the rows and the plan of a run", async () => {
    mount();
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("Thrift & Table - Ashland");

    await userEvent.click(screen.getByRole("tab", { name: "Plan" }));
    expect(screen.getByRole("region", { name: "Plan" })).toBeInTheDocument();
    expect(screen.queryByText("Thrift & Table - Ashland")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Rows" }));
    expect(screen.getByText("Thrift & Table - Ashland")).toBeInTheDocument();
  });

  it("says why a request failed, and clears it on the next", async () => {
    const query = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, "database_unavailable", "cannot reach the retail database: refused"))
      .mockResolvedValueOnce(makeResult());
    mount({ query });
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot reach the retail database: refused");

    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("The agent would accept this query");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is busy while the query runs", async () => {
    let finish: (value: QueryResult) => void = () => undefined;
    mount({ query: vi.fn((): Promise<QueryResult> => new Promise((resolve) => (finish = resolve))) });
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    finish(makeResult());
    expect(await screen.findByRole("button", { name: "Run" })).toBeEnabled();
  });
});

describe("the schema's actions", () => {
  it("starts a query on a table", async () => {
    mount({ schema: vi.fn(async () => makeSchema([makeTable({ name: "Odd Table" })])) });
    await userEvent.click(await screen.findByRole("button", { name: /Odd Table/ }));
    await userEvent.click(screen.getByRole("button", { name: "Query" }));
    expect(screen.getByLabelText("SQL")).toHaveValue('SELECT *\nFROM "Odd Table"\nLIMIT 50');
  });

  it("shows the agent's view of a table in its own tab", async () => {
    const client = mount();
    await userEvent.click(await screen.findByRole("button", { name: /dim_store/ }));
    await userEvent.click(screen.getByRole("button", { name: "Agent's view" }));

    expect(client.prompt).toHaveBeenCalledWith("dim_store");
    expect(await screen.findByRole("region", { name: "Agent's view" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Agent's view" })).toHaveAttribute("aria-selected", "true");
    // No query has been run, so there is nothing to show rows or a plan of.
    expect(screen.queryByRole("tab", { name: "Rows" })).not.toBeInTheDocument();
  });

  it("keeps the agent's view one tab away from a result", async () => {
    mount();
    await typeQuery("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("The agent would accept this query");
    await userEvent.click(screen.getByRole("button", { name: /dim_store/ }));
    await userEvent.click(screen.getByRole("button", { name: "Agent's view" }));
    await screen.findByRole("region", { name: "Agent's view" });

    await userEvent.click(screen.getByRole("tab", { name: "Rows" }));
    expect(screen.queryByRole("region", { name: "Agent's view" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Agent's view" }));
    expect(screen.getByRole("region", { name: "Agent's view" })).toBeInTheDocument();
  });

  it("says why the agent's view could not be shown, and recovers", async () => {
    const prompt = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(404, "unknown_table", "gone is not a table in public"))
      .mockResolvedValueOnce(makePrompt());
    mount({ prompt });
    await userEvent.click(await screen.findByRole("button", { name: /dim_store/ }));
    await userEvent.click(screen.getByRole("button", { name: "Agent's view" }));
    expect(await screen.findByText("gone is not a table in public")).toHaveClass("error");

    await userEvent.click(screen.getByRole("button", { name: "Agent's view" }));
    expect(await screen.findByRole("region", { name: "Agent's view" })).toBeInTheDocument();
    expect(screen.queryByText("gone is not a table in public")).not.toBeInTheDocument();
  });
});

describe("history", () => {
  it("remembers each query with the agent's verdict, and brings it back", async () => {
    mount({
      query: vi.fn(async () => makeResult({ agent: { accepted: false, stage: "static", issues: [], notes: [] } })),
    });
    await typeQuery("SELECT * FROM pg_stats");
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("The agent would refuse this at the static validator");

    const stored = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
    expect(stored).toMatchObject([{ sql: "SELECT * FROM pg_stats", mode: "run", accepted: false }]);

    await typeQuery("SELECT 2");
    await userEvent.click(screen.getByTitle("SELECT * FROM pg_stats"));
    expect(screen.getByLabelText("SQL")).toHaveValue("SELECT * FROM pg_stats");
  });

  it("opens with what this browser ran before, and can forget it", async () => {
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([{ sql: "SELECT 42", mode: "plan", at: "2026-09-28T10:00:00Z", accepted: true }]),
    );
    mount();
    expect(await screen.findByText("SELECT 42")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear history" }));
    expect(screen.queryByText("SELECT 42")).not.toBeInTheDocument();
    expect(localStorage.getItem(HISTORY_KEY)).toBe("[]");
  });
});
