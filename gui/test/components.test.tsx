import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnswerView } from "../src/components/AnswerView";
import { AskBox, EXAMPLES } from "../src/components/AskBox";
import { Disclosure } from "../src/components/Disclosure";
import { History } from "../src/components/History";
import { Progress } from "../src/components/Progress";
import { ResultTable } from "../src/components/ResultTable";
import { StatusBar } from "../src/components/StatusBar";
import { createFeedbackStore } from "../src/feedback/store";
import { ApiError } from "../src/api/client";
import { makeAnswer, makeJob, makeMeta, makeResult, memoryStorage } from "./helpers";

afterEach(cleanup);

const store = () => createFeedbackStore(memoryStorage());

describe("AskBox", () => {
  it("asks what was typed", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} onCancel={vi.fn()} busy={false} showExamples={false} />);

    await userEvent.type(screen.getByLabelText(/Ask the retail database/), "how many stores?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(onAsk).toHaveBeenCalledWith("how many stores?");
  });

  it("submits on Enter and adds a line on shift-Enter", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} onCancel={vi.fn()} busy={false} showExamples={false} />);
    const box = screen.getByLabelText(/Ask the retail database/);

    await userEvent.type(box, "first{Shift>}{Enter}{/Shift}second");
    expect(onAsk).not.toHaveBeenCalled();

    await userEvent.type(box, "{Enter}");
    expect(onAsk).toHaveBeenCalledWith("first\nsecond");
  });

  it("will not send an empty or whitespace-only question", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} onCancel={vi.fn()} busy={false} showExamples={false} />);

    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Ask the retail database/), "   {Enter}");
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("offers a Stop button instead of Ask while a question is running", async () => {
    const onCancel = vi.fn();
    render(<AskBox onAsk={vi.fn()} onCancel={onCancel} busy showExamples={false} />);

    expect(screen.queryByRole("button", { name: "Ask" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("does not submit while busy", () => {
    const onAsk = vi.fn();
    const { container } = render(
      <AskBox onAsk={onAsk} onCancel={vi.fn()} busy showExamples={false} />,
    );
    fireEvent.submit(container.querySelector("form") as Element);
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("asks an example straight away when one is clicked", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} onCancel={vi.fn()} busy={false} showExamples />);

    await userEvent.click(screen.getByRole("button", { name: EXAMPLES[0] }));
    expect(onAsk).toHaveBeenCalledWith(EXAMPLES[0]);
  });

  it("clears the box on send, so the next question replaces rather than appends", async () => {
    render(<AskBox onAsk={vi.fn()} onCancel={vi.fn()} busy={false} showExamples={false} />);
    const box = screen.getByLabelText(/Ask the retail database/);

    await userEvent.type(box, "how many stores?{Enter}");
    expect(box).toHaveValue("");
  });

  it("hides the examples once they are no longer the whole page", () => {
    render(<AskBox onAsk={vi.fn()} onCancel={vi.fn()} busy={false} showExamples={false} />);
    expect(screen.queryByText("Try")).toBeNull();
  });
});

describe("Progress", () => {
  const nodes = ["screen", "generate_sql", "execute"];

  it("shows the node the agent is on, in its own words", () => {
    const events = [{ seq: 1, step: "screen", label: "screening", detail: "in scope", at: "" }];
    render(<Progress events={events} nodes={nodes} done={false} />);

    expect(screen.getByRole("status").textContent).toBe("screening — in scope");
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
  });

  it("says queued before anything has happened", () => {
    render(<Progress events={[]} nodes={nodes} done={false} />);
    expect(screen.getByRole("status").textContent).toBe("Queued");
  });

  it("omits the dash when a step has no detail", () => {
    const events = [{ seq: 1, step: "screen", label: "screening", detail: "", at: "" }];
    render(<Progress events={events} nodes={nodes} done={false} />);
    expect(screen.getByRole("status").textContent).toBe("screening");
  });

  it("marks the steps that have finished and leaves the rest pending", () => {
    const events = [{ seq: 1, step: "screen", label: "screening", detail: "", at: "" }];
    const { container } = render(<Progress events={events} nodes={nodes} done={false} />);

    expect(container.querySelectorAll('[data-state="done"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-state="pending"]')).toHaveLength(2);
  });

  it("shows a streamed step the server never listed, because the stream is the truth", () => {
    const events = [{ seq: 1, step: "repair_sql", label: "repair", detail: "", at: "" }];
    const { container } = render(<Progress events={events} nodes={nodes} done={false} />);
    const steps = [...container.querySelectorAll(".progress-step")].map((node) => node.textContent);

    expect(steps).toEqual(["screen", "generate_sql", "execute", "repair"]);
    expect(screen.getByText("1 / 4")).toBeInTheDocument();
  });

  it("reports the total when it is finished", () => {
    const events = [
      { seq: 1, step: "screen", label: "screening", detail: "", at: "" },
      { seq: 2, step: "execute", label: "run", detail: "", at: "" },
    ];
    render(<Progress events={events} nodes={nodes} done />);
    expect(screen.getByRole("status").textContent).toBe("Finished — 2 steps");
  });

  it("drops the counter before /v1/meta has said what the nodes are", () => {
    render(<Progress events={[]} nodes={[]} done={false} />);
    expect(screen.queryByText(/\d+ \/ \d+/)).toBeNull();
  });
});

describe("ResultTable", () => {
  it("shows the rows as they came, without re-rounding a numeric", () => {
    render(<ResultTable result={makeResult()} />);
    expect(screen.getByText("719279.97")).toBeInTheDocument();
    expect(screen.getByText("3 rows")).toBeInTheDocument();
  });

  it("uses the singular for one row", () => {
    render(<ResultTable result={makeResult({ rows: [["Produce", 1]], row_count: 1 })} />);
    expect(screen.getByText("1 row")).toBeInTheDocument();
  });

  it("says when the server truncated the result", () => {
    render(<ResultTable result={makeResult({ truncated: true })} />);
    expect(screen.getByText(/truncated by the server's row limit/)).toBeInTheDocument();
  });

  it("distinguishes a query that matched nothing from one that could not run", () => {
    render(<ResultTable result={makeResult({ rows: [], row_count: 0 })} />);
    expect(screen.getByText("The query ran and matched nothing.")).toBeInTheDocument();
  });

  it("copes with a result carrying no columns", () => {
    render(<ResultTable result={makeResult({ columns: [], rows: [] })} />);
    expect(screen.getByText("The query returned no columns.")).toBeInTheDocument();
  });

  it("right-aligns a numeric column and leaves text alone", () => {
    const { container } = render(<ResultTable result={makeResult()} />);
    const headers = container.querySelectorAll("th");
    expect(headers[0]).toHaveAttribute("data-numeric", "false");
    expect(headers[1]).toHaveAttribute("data-numeric", "true");
  });

  it("shows a null as a dash rather than as an empty cell", () => {
    render(<ResultTable result={makeResult({ rows: [["Produce", null]], row_count: 1 })} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("serialises a structured cell rather than printing [object Object]", () => {
    render(<ResultTable result={makeResult({ rows: [["Produce", { a: 1 }]], row_count: 1 })} />);
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
  });

  it("marks the cells a claim was read from", () => {
    const { container } = render(
      <ResultTable result={makeResult()} highlight={new Set(["0:net_sales"])} />,
    );
    expect(container.querySelectorAll('[data-highlight="true"]')).toHaveLength(1);
  });
});

describe("Disclosure", () => {
  it("starts closed and carries a badge", () => {
    const { container } = render(
      <Disclosure summary="SQL" badge="2 attempts">
        <p>body</p>
      </Disclosure>,
    );
    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByText("2 attempts")).toBeInTheDocument();
  });

  it("can start open and can omit the badge", () => {
    const { container } = render(
      <Disclosure summary="SQL" open>
        <p>body</p>
      </Disclosure>,
    );
    expect(container.querySelector("details")).toHaveAttribute("open");
    expect(container.querySelector(".disclosure-badge")).toBeNull();
  });
});

describe("History", () => {
  it("is absent until something has been asked", () => {
    const { container } = render(
      <History jobs={[]} currentId={undefined} onSelect={vi.fn()} store={store()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what was asked and selects it again", async () => {
    const job = makeJob({ id: "job-1" });
    const onSelect = vi.fn();
    render(<History jobs={[job]} currentId={undefined} onSelect={onSelect} store={store()} />);

    await userEvent.click(screen.getByRole("button", { name: new RegExp(job.question) }));
    expect(onSelect).toHaveBeenCalledWith(job);
  });

  it("marks the one on screen", () => {
    const job = makeJob({ id: "job-1" });
    render(<History jobs={[job]} currentId="job-1" onSelect={vi.fn()} store={store()} />);
    expect(screen.getByRole("button", { name: new RegExp(job.question) })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("shows the verdict already recorded against an answer", () => {
    const feedback = store();
    feedback.set("job-1", "q", "yes");
    render(
      <History jobs={[makeJob({ id: "job-1" })]} currentId={undefined} onSelect={vi.fn()} store={feedback} />,
    );
    expect(screen.getByText("✓")).toBeInTheDocument();
  });

  it("shows a no as well", () => {
    const feedback = store();
    feedback.set("job-1", "q", "no");
    render(
      <History jobs={[makeJob({ id: "job-1" })]} currentId={undefined} onSelect={vi.fn()} store={feedback} />,
    );
    expect(screen.getByText("✗")).toBeInTheDocument();
  });

  it("shows a correct-but-incomplete verdict as its own mark, with its words", () => {
    const feedback = store();
    feedback.set("job-1", "q", "incomplete");
    render(
      <History jobs={[makeJob({ id: "job-1" })]} currentId={undefined} onSelect={vi.fn()} store={feedback} />,
    );
    expect(screen.getByText("◐")).toHaveAttribute("title", "Marked correct but incomplete");
    expect(screen.getByText("◐")).toHaveAttribute("data-verdict", "incomplete");
  });

  it("names a status that is not a success", () => {
    render(
      <History
        jobs={[makeJob({ id: "job-1", status: "failed" })]}
        currentId={undefined}
        onSelect={vi.fn()}
        store={store()}
      />,
    );
    expect(screen.getByText("failed")).toBeInTheDocument();
  });
});

describe("StatusBar", () => {
  it("says what it is connected to", () => {
    render(<StatusBar meta={makeMeta()} error={null} warnings={[]} />);
    expect(screen.getByText("nl2sql-agent")).toBeInTheDocument();
    expect(screen.getByText("qwen2.5-coder:32b")).toBeInTheDocument();
    expect(screen.getByText("3 tables")).toBeInTheDocument();
    expect(screen.getByText("no token")).toBeInTheDocument();
  });

  it("uses the singular for a single table, and names a token when one is required", () => {
    render(
      <StatusBar
        meta={makeMeta({ tables: ["dim_store"], authentication: "bearer" })}
        error={null}
        warnings={[]}
      />,
    );
    expect(screen.getByText("1 table")).toBeInTheDocument();
    expect(screen.getByText("token required")).toBeInTheDocument();
  });

  it("shows the server's own warnings", () => {
    render(<StatusBar meta={makeMeta()} error={null} warnings={["database: still starting"]} />);
    expect(screen.getByText("database: still starting")).toBeInTheDocument();
  });

  it("omits an empty scope rather than leaving a gap", () => {
    const { container } = render(<StatusBar meta={makeMeta({ scope: "" })} error={null} warnings={[]} />);
    expect(container.querySelector(".status-scope")).toBeNull();
  });

  it("says plainly when it cannot reach the API, since the rest of the page just looks empty", () => {
    render(
      <StatusBar meta={null} error={new ApiError(0, "unreachable", "cannot reach the API")} warnings={[]} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Not connected.");
    expect(screen.getByText("./launch.sh --gui")).toBeInTheDocument();
  });

  it("says it is connecting while it still might be", () => {
    render(<StatusBar meta={null} error={null} warnings={[]} />);
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
  });
});

describe("AnswerView", () => {
  const pipeline = makeMeta().pipeline;

  it("leads with the sentence, then the chart, then the rows", () => {
    const { container } = render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);

    expect(container.querySelector(".answer-headline")).toHaveTextContent(
      "Produce led with $719,279.97.",
    );
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByText("719279.97")).toBeInTheDocument();
    expect(screen.getByText("61.4s")).toBeInTheDocument();
  });

  it("never renders the markdown document the terminal gets", () => {
    // `answer` is prose, then the rows as a markdown table, then the
    // caveats. This page draws the table itself.
    const { container } = render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")?.textContent).not.toContain("|");
  });

  it("builds the sentence from the claims, so each one is traceable", () => {
    const job = makeJob({
      answer: makeAnswer({
        claims: [
          { text: "Produce led.", value: null, cells: [[0, "net_sales"]], formula: null },
          { text: "Bakery trailed.", value: null, cells: [[2, "net_sales"]], formula: null },
        ],
      }),
    });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);

    expect(container.querySelector(".answer-headline")).toHaveTextContent(
      "Produce led. Bakery trailed.",
    );
    expect(container.querySelectorAll(".claim")).toHaveLength(2);
  });

  it("lights up the cells the hovered sentence was read from", () => {
    const { container } = render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);
    const claim = container.querySelector(".claim") as Element;

    fireEvent.mouseEnter(claim);
    expect(container.querySelectorAll('[data-highlight="true"]')).toHaveLength(1);

    fireEvent.mouseLeave(claim);
    expect(container.querySelectorAll('[data-highlight="true"]')).toHaveLength(0);
  });

  it("shows a claim's formula when it has one", () => {
    const job = makeJob({
      answer: makeAnswer({
        claims: [{ text: "Margin was 31%.", value: 0.31, cells: [], formula: "gross / net" }],
      }),
    });
    render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(screen.getByText("gross / net")).toBeInTheDocument();
  });

  it("falls back to the narrative when the audit left no claims", () => {
    const job = makeJob({ answer: makeAnswer({ claims: [] }) });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")).toHaveTextContent(/ahead of Dairy & Eggs/);
  });

  it("falls back to the markdown answer's first block when there is no narrative", () => {
    const job = makeJob({
      answer: makeAnswer({
        claims: [],
        narrative: "",
        answer: "Produce led.\n\n| department | net_sales |\n| --- | --- |\n| Produce | 719279.97 |",
      }),
    });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")).toHaveTextContent("Produce led.");
    expect(container.querySelector(".answer-headline")?.textContent).not.toContain("|");
  });

  it("shows nothing rather than an empty paragraph when there is no prose at all", () => {
    const job = makeJob({ answer: makeAnswer({ claims: [], narrative: "", answer: "" }) });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")).toBeNull();
  });

  it("un-escapes the markup the pipeline escaped for markdown", () => {
    // React renders text, so an escaped ampersand would otherwise arrive as
    // five characters on screen -- in a banner called Thrift & Table, say.
    const job = makeJob({
      answer: makeAnswer({
        claims: [
          { text: "Thrift &amp; Table led, ahead of &lt;none&gt;.", value: null, cells: [], formula: null },
        ],
      }),
    });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")).toHaveTextContent(
      "Thrift & Table led, ahead of <none>.",
    );
  });

  it("says the numbers were checked when the audit passed", () => {
    render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);
    expect(screen.getByText(/checked against the rows above/)).toBeInTheDocument();
  });

  it("reports what the audit found when it did not", () => {
    const job = makeJob({
      answer: makeAnswer({
        audit: {
          passed: false,
          unsupported_claims: ["Sales doubled."],
          drop_reasons: ["a sentence was dropped"],
          redactions: [],
          semantic_issue: "the question asked for a rate, the SQL returned a total",
        },
      }),
    });
    render(<AnswerView job={job} store={store()} pipeline={pipeline} />);

    expect(screen.getByText("Unsupported: Sales doubled.")).toBeInTheDocument();
    expect(screen.getByText("a sentence was dropped")).toBeInTheDocument();
    expect(screen.getByText(/the SQL returned a total/)).toBeInTheDocument();
  });

  it("hides the audit entirely on a server running without one", () => {
    const { container } = render(
      <AnswerView job={makeJob()} store={store()} pipeline={{ ...pipeline, audit: false }} />,
    );
    expect(container.querySelector(".audit")).toBeNull();
  });

  it("assumes both stages ran when /v1/meta has not landed", () => {
    render(<AnswerView job={makeJob()} store={store()} pipeline={null} />);
    expect(screen.getByText(/checked against the rows above/)).toBeInTheDocument();
  });

  describe("when there is no answer to show", () => {
    it("puts a refusal where the answer would be, and draws no empty table", () => {
      const job = makeJob({
        answer: makeAnswer({
          verdict: "out_of_domain",
          answer: "That is not something this database covers.",
          result: null,
          chart: null,
        }),
      });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);

      expect(screen.getByText("That is outside what this database covers")).toBeInTheDocument();
      expect(screen.queryByRole("table")).toBeNull();
    });

    it("asks the clarifying question back when the question was ambiguous", () => {
      const job = makeJob({
        answer: makeAnswer({
          verdict: "ambiguous",
          clarification: "Which fiscal year did you mean?",
          result: null,
        }),
      });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
      expect(screen.getByText("Which fiscal year did you mean?")).toBeInTheDocument();
    });

    it("falls back to a generic title for a verdict it has no wording for", () => {
      const job = makeJob({ answer: makeAnswer({ verdict: "something_new", result: null }) });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
      expect(screen.getByText("The agent did not answer")).toBeInTheDocument();
    });

    it("shows a failure with the SQL it tried, which is the useful part", () => {
      const job = makeJob({
        status: "failed",
        error: "the query timed out after 30s",
        answer: makeAnswer({ result: null }),
      });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);

      expect(screen.getByRole("alert").textContent).toContain("the query timed out after 30s");
      expect(screen.getAllByText(/SELECT department/).length).toBeGreaterThan(0);
    });

    it("says so even when the server gave no reason", () => {
      const job = makeJob({ status: "failed", error: null, answer: null });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
      expect(screen.getByText("The server did not say why.")).toBeInTheDocument();
    });
  });

  describe("the details", () => {
    it("folds the SQL, the matched values and the cost away", async () => {
      render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);

      await userEvent.click(screen.getByText("SQL"));
      expect(screen.getByText(/SELECT department/)).toBeVisible();
      expect(screen.getByText(/dim_product, fact_pos_retail_sales/)).toBeInTheDocument();
      expect(screen.getByText(/125,767.4/)).toBeInTheDocument();

      expect(screen.getByText("Matched values")).toBeInTheDocument();
      expect(screen.getByText("produse")).toBeInTheDocument();
      expect(screen.getByText(/dim_product.department \(0.81\)/)).toBeInTheDocument();
    });

    it("says how many attempts it took, but only when it took more than one", () => {
      const { rerender } = render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);
      expect(screen.queryByText(/attempts/)).toBeNull();

      rerender(
        <AnswerView
          job={makeJob({ answer: makeAnswer({ attempts: 3 }) })}
          store={store()}
          pipeline={pipeline}
        />,
      );
      expect(screen.getByText("3 attempts")).toBeInTheDocument();
    });

    it("shows the per-node cost as a bar per step", () => {
      const { container } = render(<AnswerView job={makeJob()} store={store()} pipeline={pipeline} />);
      const trace = container.querySelector(".trace") as Element;

      expect(within(trace as HTMLElement).getByText("generate_sql")).toBeInTheDocument();
      expect(within(trace as HTMLElement).getByText("8.12s")).toBeInTheDocument();
      expect(within(trace as HTMLElement).getByText("1 model call")).toBeInTheDocument();
    });

    it("uses the plural for several model calls", () => {
      const job = makeJob({
        answer: makeAnswer({ trace: [{ node: "repair", ms: 10, model_calls: 2, detail: "" }] }),
      });
      render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
      expect(screen.getByText("2 model calls")).toBeInTheDocument();
    });

    it("leaves out a panel there is nothing to put in", () => {
      const job = makeJob({
        answer: makeAnswer({ sql: "", literals: [], trace: [], plan_cost: null, tables: [] }),
      });
      const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
      expect(container.querySelectorAll("details")).toHaveLength(0);
    });
  });

  it("carries the feedback buttons, and remembers what was clicked", async () => {
    const feedback = store();
    render(<AnswerView job={makeJob()} store={feedback} pipeline={pipeline} />);

    await userEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect(feedback.get("job-1")?.verdict).toBe("yes");
    expect(feedback.get("job-1")?.question).toBe(makeJob().question);
  });
});
