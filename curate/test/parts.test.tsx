/**
 * The small parts: wording helpers, the draft helpers, and the shared views.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EMPTY_SNIPPET, KIND_HINTS, missing, runKey, toDraft } from "../src/api/snippet";
import { cell, counted, matches, message, names } from "../src/api/text";
import { Problem } from "../src/components/Problem";
import { Rows } from "../src/components/Rows";
import { RunResult } from "../src/components/RunResult";
import { Steps } from "../src/components/Steps";
import { makeSnippet, makeValidation } from "./helpers";

afterEach(cleanup);

describe("wording", () => {
  it("counts, renders cells, names and matches", () => {
    expect([counted(1, "pair"), counted(2, "pair"), counted(0, "query", "queries")]).toEqual(["1 pair", "2 pairs", "0 queries"]);
    expect([cell(null), cell(undefined), cell(0), cell(true)]).toEqual(["NULL", "NULL", "0", "true"]);
    expect([message(new Error("x")), message(3)]).toEqual(["x", "3"]);
    expect(names(" a, ,b ,")).toEqual(["a", "b"]);
    expect(matches("NET  sales", "Net sales", "revenue")).toBe(true);
    expect(matches("net margin", "Net sales")).toBe(false);
    expect(matches("", "anything")).toBe(true);
  });
});

describe("the snippet draft", () => {
  it("is built from a stored snippet and keyed by what a validation is about", () => {
    const draft = toDraft(makeSnippet({ tables: ["a", "b"], keywords: ["x", "y"] }));
    expect(draft).toMatchObject({ tables: "a, b", keywords: "x, y", kind: "measure" });
    expect(runKey(draft)).toBe(runKey({ ...draft, name: "other", note: "n" }));
    expect(runKey(draft)).not.toBe(runKey({ ...draft, sql: "COUNT(*)" }));
    expect(runKey(draft)).toBe(runKey({ ...draft, sql: ` ${draft.sql} ` }));
    expect(missing(EMPTY_SNIPPET)).toEqual(["name", "keywords", "means", "applies_to", "sql"]);
    expect(Object.keys(KIND_HINTS)).toEqual(["join", "filter", "measure", "dimension"]);
  });
});

describe("the shared views", () => {
  it("shows rows, and says when there were more than are shown", () => {
    const { container, rerender } = render(<Rows columns={[]} rows={[]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<Rows columns={["a", "a"]} rows={[[1, null]]} total={5} />);
    expect(screen.getByText("NULL")).toBeInTheDocument();
    expect(screen.getByText("First 1 of 5 rows.")).toBeInTheDocument();
    rerender(<Rows columns={["a"]} rows={[[1]]} total={1} />);
    expect(screen.queryByText(/First/)).not.toBeInTheDocument();
  });

  it("words a run's result by its count, truncation and cost", () => {
    const { rerender } = render(<RunResult validation={makeValidation({ row_count: 2, truncated: true, plan_cost: null, warnings: ["more than 200"] })} stale={false} />);
    expect(screen.getByText("It runs.").closest("p")).toHaveTextContent(/^It runs\. 2\+ rows · 2 ms$/);
    expect(screen.getByText("more than 200")).toBeInTheDocument();
    rerender(<RunResult validation={makeValidation()} stale />);
    expect(screen.getByText("It runs.").closest("p")).toHaveTextContent(/^It runs\. 1 row · plan cost 1\.5 · 2 ms$/);
    expect(screen.getByText(/Edited since it was validated/)).toBeInTheDocument();
  });

  it("shows each loader step and nothing for none", () => {
    const { container, rerender } = render(<Steps steps={[]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(
      <Steps
        steps={[
          { name: "a", ran: true, ok: true, detail: "" },
          { name: "b", ran: false, ok: true, detail: "" },
          { name: "c", ran: true, ok: false, detail: "down" },
        ]}
      />,
    );
    expect(screen.getByText("a").closest("li")).toHaveClass("step-ok");
    expect(screen.getByText("b").closest("li")).toHaveClass("step-skipped");
    expect(screen.getByText("c").closest("li")).toHaveClass("step-failed");
    expect(screen.getByText(/— down/)).toBeInTheDocument();
  });

  it("shows a problem only when there is one", () => {
    const { container, rerender } = render(<Problem error={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<Problem error="it broke" />);
    expect(screen.getByRole("alert")).toHaveTextContent("it broke");
  });
});
