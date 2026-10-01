/**
 * Each panel on its own. The ones that carry the diagnosis -- the verdict
 * and the rows -- are held to what they must never do: show a refused query
 * as accepted, show NULL as an empty string, or show a truncated result as
 * a complete one.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { History } from "../src/components/History";
import { PlanView } from "../src/components/PlanView";
import { PromptView } from "../src/components/PromptView";
import { ResultTable } from "../src/components/ResultTable";
import { SchemaBrowser } from "../src/components/SchemaBrowser";
import { SqlEditor } from "../src/components/SqlEditor";
import { StatusBar } from "../src/components/StatusBar";
import { Verdict } from "../src/components/Verdict";
import { makeMeta, makePrompt, makeResult, makeTable } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StatusBar", () => {
  it("shows the connection and the agent's limits", () => {
    render(<StatusBar meta={makeMeta()} error={null} />);
    expect(screen.getByText("nl2sql_reader")).toHaveClass("good");
    expect(screen.getByText(/\(read-only\)/)).toBeInTheDocument();
    expect(screen.getByText(/Postgres 18\.6/)).toBeInTheDocument();
    expect(screen.getByText(/30,000 ms · 50 rows · plan cost 1,000,000/)).toBeInTheDocument();
    expect(screen.getByText(/shows 1000 rows · auth none/)).toBeInTheDocument();
  });

  it("marks a role that could write, and shows every warning", () => {
    render(
      <StatusBar
        meta={makeMeta({ role: "nl2sql", read_only: false, warnings: ["no token", "owner role"] })}
        error={null}
      />,
    );
    expect(screen.getByText("nl2sql")).toHaveClass("bad");
    expect(screen.getByText(/\(can write\)/)).toBeInTheDocument();
    expect(screen.getByText("no token")).toHaveClass("status-warn");
    expect(screen.getByText("owner role")).toBeInTheDocument();
  });

  it("says when the console cannot be reached, and nothing else", () => {
    const { container } = render(<StatusBar meta={null} error="HTTP 502" />);
    expect(screen.getByText("Cannot reach the console: HTTP 502")).toBeInTheDocument();
    expect(container.querySelectorAll(".status")).toHaveLength(1);
  });
});

describe("SchemaBrowser", () => {
  const tables = [
    makeTable(),
    makeTable({
      name: "fact_pos_retail_sales",
      comment: null,
      approx_rows: 1291781,
      columns: [{ name: "net_sales_amt", data_type: "numeric(12,2)", not_null: false, comment: null }],
      constraints: [],
    }),
  ];

  function mount(overrides: Partial<Parameters<typeof SchemaBrowser>[0]> = {}) {
    const props = { tables, error: null, onQuery: vi.fn(), onPrompt: vi.fn(), ...overrides };
    render(<SchemaBrowser {...props} />);
    return props;
  }

  it("lists every table with its size estimate", () => {
    mount();
    expect(screen.getByText("dim_store")).toBeInTheDocument();
    expect(screen.getByText("~1,291,781 rows")).toBeInTheDocument();
  });

  it("opens a table to show its comment, columns and keys, and closes it again", async () => {
    mount();
    const button = screen.getByRole("button", { name: /dim_store/ });
    await userEvent.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("One row per store.")).toBeInTheDocument();
    expect(screen.getByText("Surrogate key.")).toBeInTheDocument();
    expect(screen.getByText("integer not null")).toBeInTheDocument();
    expect(screen.getByText("character varying(100)")).toBeInTheDocument();
    expect(screen.getByText("PRIMARY KEY (store_key)")).toBeInTheDocument();

    await userEvent.click(button);
    expect(screen.queryByText("One row per store.")).not.toBeInTheDocument();
  });

  it("says out loud when a table has no comment", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: /fact_pos_retail_sales/ }));
    expect(screen.getByText(/No comment: the agent is told nothing/)).toHaveClass("warn");
  });

  it("hands its two actions the table's name", async () => {
    const props = mount();
    await userEvent.click(screen.getByRole("button", { name: /dim_store/ }));
    await userEvent.click(screen.getByRole("button", { name: "Query" }));
    await userEvent.click(screen.getByRole("button", { name: "Agent's view" }));

    expect(props.onQuery).toHaveBeenCalledWith("dim_store");
    expect(props.onPrompt).toHaveBeenCalledWith("dim_store");
  });

  it("filters by table name and by column name", async () => {
    mount();
    const filter = screen.getByLabelText("Filter tables and columns");

    await userEvent.type(filter, "NET_SALES");
    expect(screen.queryByText("dim_store")).not.toBeInTheDocument();
    expect(screen.getByText("fact_pos_retail_sales")).toBeInTheDocument();

    await userEvent.clear(filter);
    await userEvent.type(filter, "dim_");
    expect(screen.getByText("dim_store")).toBeInTheDocument();
    expect(screen.queryByText("fact_pos_retail_sales")).not.toBeInTheDocument();

    await userEvent.clear(filter);
    await userEvent.type(filter, "zzz");
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it("says it is loading before there are tables", () => {
    mount({ tables: [] });
    expect(screen.getByText("Loading the schema…")).toBeInTheDocument();
  });

  it("shows a failure instead of the loading line", () => {
    mount({ tables: [], error: "database_unavailable" });
    expect(screen.getByText("database_unavailable")).toHaveClass("error");
    expect(screen.queryByText("Loading the schema…")).not.toBeInTheDocument();
  });
});

describe("SqlEditor", () => {
  function mount(overrides: Partial<Omit<Parameters<typeof SqlEditor>[0], "onChange" | "onRun">> = {}) {
    const onChange = vi.fn();
    const onRun = vi.fn();
    render(<SqlEditor sql="SELECT 1" busy={false} maxLength={20000} onChange={onChange} onRun={onRun} {...overrides} />);
    return { onChange, onRun };
  }

  it("runs, plans and analyzes", async () => {
    const props = mount();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await userEvent.click(screen.getByRole("button", { name: "Plan" }));
    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(props.onRun.mock.calls).toEqual([["run"], ["plan"], ["analyze"]]);
  });

  it("clears the query", async () => {
    const props = mount();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(props.onChange).toHaveBeenCalledWith("");
  });

  it("reports every edit", () => {
    const props = mount();
    fireEvent.change(screen.getByLabelText("SQL"), { target: { value: "SELECT 2" } });
    expect(props.onChange).toHaveBeenCalledWith("SELECT 2");
  });

  it("runs on Ctrl+Enter and on Cmd+Enter, and on nothing else", () => {
    const props = mount();
    const editor = screen.getByLabelText("SQL");
    fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(editor, { key: "Enter", metaKey: true });
    fireEvent.keyDown(editor, { key: "Enter" });
    fireEvent.keyDown(editor, { key: "a", ctrlKey: true });
    expect(props.onRun.mock.calls).toEqual([["run"], ["run"]]);
  });

  it("does nothing with an empty query", () => {
    const props = mount({ sql: "   " });
    fireEvent.keyDown(screen.getByLabelText("SQL"), { key: "Enter", ctrlKey: true });
    expect(props.onRun).not.toHaveBeenCalled();
    for (const name of ["Run", "Plan", "Analyze", "Clear"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });

  it("does nothing while a query is running", () => {
    const props = mount({ busy: true });
    fireEvent.keyDown(screen.getByLabelText("SQL"), { key: "Enter", ctrlKey: true });
    expect(props.onRun).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
  });

  it("caps the query at the length the console accepts", () => {
    mount({ maxLength: 123 });
    expect(screen.getByLabelText("SQL")).toHaveAttribute("maxLength", "123");
  });
});

describe("Verdict", () => {
  it("says a query the agent would run is accepted, with its cost against the ceiling", () => {
    render(<Verdict result={makeResult({ plan_cost: 25317.4 })} />);
    const panel = screen.getByRole("region", { name: "Agent verdict" });
    expect(panel).toHaveClass("verdict-good");
    expect(within(panel).getByText("The agent would accept this query")).toBeInTheDocument();
    expect(panel).toHaveTextContent("estimated cost 25,317.4 of the 1,000,000 ceiling (2.5%)");
    expect(panel).toHaveTextContent("ran in 4.2 ms");
  });

  it("names the gate that would refuse it, in that gate's words", () => {
    render(
      <Verdict
        result={makeResult({
          executed: false,
          columns: [],
          rows: [],
          plan_cost: 4200000,
          agent: {
            accepted: false,
            stage: "planner",
            issues: [{ stage: "planner", message: "estimated plan cost 4,200,000.00 exceeds the ceiling of 1,000,000.00" }],
            notes: [],
          },
        })}
      />,
    );
    const panel = screen.getByRole("region", { name: "Agent verdict" });
    expect(panel).toHaveClass("verdict-bad");
    expect(within(panel).getByText("The agent would refuse this at the planner gate")).toBeInTheDocument();
    expect(within(panel).getByText("planner gate", { selector: ".gate" })).toBeInTheDocument();
    expect(within(panel).getByText(/exceeds the ceiling/)).toBeInTheDocument();
    expect(within(panel).getByText(/estimated cost 4,200,000/)).toHaveClass("bad");
    expect(panel).toHaveTextContent("did not run in");
  });

  it("says a query the validator stopped was not run here either", () => {
    render(
      <Verdict
        result={makeResult({
          executed: false,
          plan: null,
          plan_cost: null,
          columns: [],
          rows: [],
          agent: {
            accepted: false,
            stage: "static",
            issues: [{ stage: "static", message: 'The CTE "d" is a DELETE' }],
            notes: [],
          },
        })}
      />,
    );
    expect(screen.getByText("The agent would refuse this at the static validator")).toBeInTheDocument();
    expect(screen.getByText(/Not run here either/)).toBeInTheDocument();
    expect(screen.queryByText(/estimated cost/)).not.toBeInTheDocument();
  });

  it("does not say 'not run' of a query that failed at runtime", () => {
    render(
      <Verdict
        result={makeResult({
          executed: false,
          error: "canceling statement due to statement timeout",
          agent: {
            accepted: false,
            stage: "runtime",
            issues: [{ stage: "runtime", message: "canceling statement due to statement timeout" }],
            notes: [],
          },
        })}
      />,
    );
    expect(screen.getByText("The agent would refuse this at the executor")).toBeInTheDocument();
    expect(screen.queryByText(/Not run here either/)).not.toBeInTheDocument();
  });

  it("says a planned query passed two gates and was not run", () => {
    render(<Verdict result={makeResult({ mode: "plan", executed: false, columns: [], rows: [] })} />);
    expect(screen.getByText("Passes the static validator and the planner gate")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Agent verdict" })).toHaveTextContent("planned, not run in");
  });

  it("shows what is not a refusal but changes the answer", () => {
    render(<Verdict result={makeResult({ agent: { accepted: true, stage: null, issues: [], notes: ["the agent reads at most 50 rows"] } })} />);
    expect(screen.getByText("the agent reads at most 50 rows")).toHaveClass("warn");
  });
});

describe("ResultTable", () => {
  it("heads each column with its name and type, and right-aligns numbers", () => {
    render(<ResultTable result={makeResult()} />);
    const header = screen.getByRole("columnheader", { name: /store_key/ });
    expect(header).toHaveTextContent("integer");
    expect(header).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: /store_name/ })).not.toHaveClass("num");
    expect(screen.getByText("Thrift & Table - Ashland")).toBeInTheDocument();
  });

  it("shows NULL as NULL, styled apart from a value", () => {
    render(<ResultTable result={makeResult({ rows: [[null, "", "x"]] })} />);
    const nullCell = screen.getByText("NULL");
    expect(nullCell).toHaveClass("null");
    expect(nullCell).toHaveClass("num");
    expect(screen.getByText("x")).not.toHaveAttribute("class");
  });

  it("says how many rows, and when there were more", () => {
    render(<ResultTable result={makeResult({ truncated: true, max_rows: 2 })} />);
    expect(screen.getByText("2 rows")).toBeInTheDocument();
    expect(screen.getByText(/the first 2; more came back and were not read/)).toBeInTheDocument();
  });

  it("does not say there were more when there were not", () => {
    render(<ResultTable result={makeResult()} />);
    expect(screen.queryByText(/more came back/)).not.toBeInTheDocument();
  });

  it("says there are no rows when the query was not run to completion", () => {
    render(<ResultTable result={makeResult({ columns: [], rows: [], row_count: 0 })} />);
    expect(screen.getByText(/No rows: the query was not run to completion/)).toBeInTheDocument();
  });

  it("copies the rows as CSV", async () => {
    const copy = vi.fn(async () => undefined);
    render(<ResultTable result={makeResult()} copy={copy} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy CSV" }));

    await screen.findByRole("button", { name: "Copied" });
    expect(copy).toHaveBeenCalledWith(
      "store_key,store_name,city\n1,Thrift & Table - Ashland,Ashland\n2,Corner Fresh Grocers - Salem,",
    );
  });

  it("says when the copy failed", async () => {
    const copy = vi.fn(async () => {
      throw new Error("denied");
    });
    render(<ResultTable result={makeResult()} copy={copy} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy CSV" }));
    await screen.findByRole("button", { name: "Copy failed" });
  });

  it("writes to the browser's clipboard by default", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ResultTable result={makeResult({ rows: [[1, "a", "b"]] })} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy CSV" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("store_key,store_name,city\n1,a,b"));
  });
});

describe("PlanView", () => {
  it("leads with the total the planner gate compares", () => {
    render(<PlanView plan={makeResult().plan} maxPlanCost={1000000} />);
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent(
      "Estimated total cost 1.4 against the agent's ceiling of 1,000,000",
    );
    expect(screen.getByText("Seq Scan")).toBeInTheDocument();
    expect(screen.getByText("dim_store", { exact: false, selector: "span.mono" })).toBeInTheDocument();
    expect(screen.getByText("0..1.4")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Loops" })).not.toBeInTheDocument();
  });

  it("adds what happened after Analyze, and the two timings", () => {
    const plan = [
      {
        Plan: {
          "Node Type": "Aggregate",
          "Total Cost": 23325.26,
          "Actual Total Time": 61.2,
          "Actual Rows": 1,
          "Actual Loops": 1,
          Plans: [{ "Node Type": "Seq Scan", "Relation Name": "fact_pos_retail_sales", Filter: "(x > 1)" }],
        },
        "Planning Time": 0.21,
        "Execution Time": 61.4,
      },
    ];
    render(<PlanView plan={plan} maxPlanCost={1000000} />);
    const region = screen.getByRole("region", { name: "Plan" });
    expect(region).toHaveTextContent("planning 0.21 ms");
    expect(region).toHaveTextContent("execution 61.4 ms");
    expect(screen.getByRole("columnheader", { name: "Loops" })).toBeInTheDocument();
    expect(screen.getByText("61.2")).toBeInTheDocument();
    expect(screen.getByText("Filter: (x > 1)")).toBeInTheDocument();
  });

  it("keeps the raw document a click away", () => {
    render(<PlanView plan={makeResult().plan} maxPlanCost={1000000} />);
    expect(screen.getByText("The plan as Postgres wrote it")).toBeInTheDocument();
    expect(screen.getByText(/"Node Type": "Seq Scan"/)).toBeInTheDocument();
  });

  it("says there is no plan when the query never reached the planner", () => {
    render(<PlanView plan={null} maxPlanCost={1000000} />);
    expect(screen.getByText(/No plan: the query did not get as far as the planner/)).toBeInTheDocument();
  });
});

describe("PromptView", () => {
  it("shows the agent's block for the table, verbatim", () => {
    render(<PromptView prompt={makePrompt()} />);
    expect(screen.getByRole("region", { name: "Agent's view" })).toHaveTextContent("with the 3 sample rows it carries");
    expect(screen.getByText(/=== dim_store ===/)).toBeInTheDocument();
  });
});

describe("History", () => {
  const entries = [
    { sql: "SELECT 1", mode: "run" as const, at: "2026-09-28T10:00:00Z", accepted: true },
    { sql: "SELECT * FROM pg_stats", mode: "plan" as const, at: "2026-09-28T09:00:00Z", accepted: false },
  ];

  it("lists what was run, marked by whether the agent would have accepted it", () => {
    const { container } = render(<History entries={entries} onPick={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText("SELECT 1")).toBeInTheDocument();
    expect(container.querySelectorAll(".dot-good")).toHaveLength(1);
    expect(container.querySelectorAll(".dot-bad")).toHaveLength(1);
  });

  it("hands back the entry picked, and clears", async () => {
    const onPick = vi.fn();
    const onClear = vi.fn();
    render(<History entries={entries} onPick={onPick} onClear={onClear} />);
    await userEvent.click(screen.getByText("SELECT * FROM pg_stats"));
    await userEvent.click(screen.getByRole("button", { name: "Clear history" }));
    expect(onPick).toHaveBeenCalledWith(entries[1]);
    expect(onClear).toHaveBeenCalled();
  });

  it("explains itself when empty, and offers nothing to clear", () => {
    render(<History entries={[]} onPick={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/in this browser only/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear history" })).not.toBeInTheDocument();
  });
});
