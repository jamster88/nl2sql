/**
 * The candidates pane: the other runs behind an answer (7.0).
 *
 * The record is whatever the agent staged, and the review service keeps it
 * unread. So the cases here are less about the happy path than about what
 * the record may lack: everything, for a submission from before 7.0; the
 * Judge, when it was off; a vote, when one run answered.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Candidates } from "../src/components/Candidates";
import { Original } from "../src/components/Original";
import type { EnsembleRecord, EnsembleRun } from "../src/api/types";
import { makeSubmission } from "./helpers";

afterEach(cleanup);

function run(overrides: Partial<EnsembleRun> = {}): EnsembleRun {
  return {
    index: 0,
    wording: "What were total net sales for Produce in FY2025?",
    origin: "original",
    changed: "",
    outcome: "answered",
    reasons: [],
    sql: "SELECT sum(net_sales_amt) FROM fact_pos_retail_sales;",
    signature: "719279.97",
    group: 0,
    ...overrides,
  };
}

/** Asked four ways: three agree, one answers differently, one never answered. */
function record(overrides: Partial<EnsembleRecord> = {}): EnsembleRecord {
  return {
    agreement: { agreed: 2, total: 4, level: "majority", why: "2 of 3 admissible agree" },
    line: "2 of 4 runs agreed; 1 answered differently; 1 could not answer.",
    chosen: 0,
    fused_from: [0, 1],
    dissent: [{ group: 1, members: [2], signature: "702113.40", differs: "filters on banner_name" }],
    judged: {
      verdicts: [
        { group: 0, accepted: true, why: "" },
        { group: 1, accepted: false, why: "it keeps one banner the question does not name" },
      ],
      set_aside: [2],
      error: "",
    },
    candidates: [
      run(),
      run({ index: 1, origin: "paraphrase", changed: "the order of the clauses", wording: "In FY2025, Produce's net sales?" }),
      run({
        index: 2,
        origin: "paraphrase",
        wording: "Produce net sales, fiscal 2025?",
        sql: "SELECT sum(net_sales_amt) FROM fact_pos_retail_sales WHERE banner_name = 'x';",
        signature: "702113.40",
        group: 1,
      }),
      run({
        index: 3,
        origin: "paraphrase",
        wording: "How much did Produce sell, net, in FY2025?",
        outcome: "gave_up",
        reasons: ["E1 answered: gave up after 7 attempts", "E3 complete"],
        sql: "",
        signature: "",
        group: null,
      }),
    ],
    discarded: [{ index: 4, text: "What did Dairy sell?", changed: "the department", reason: "F2 literals" }],
    ...overrides,
  };
}

describe("Candidates", () => {
  it("says a submission from before 7.0 was a single run, rather than drawing an empty list", () => {
    const { container } = render(<Candidates ensemble={{}} />);
    expect(screen.getByText("A single run; no ensemble record.")).toBeInTheDocument();
    expect(container.querySelector("details")).toBeNull();
  });

  it("says the same of a record with no runs in it", () => {
    render(<Candidates ensemble={record({ candidates: [] })} />);
    expect(screen.getByText("A single run; no ensemble record.")).toBeInTheDocument();
  });

  it("is folded away, titled by how many ways the question was asked and how the vote went", () => {
    const { container } = render(<Candidates ensemble={record()} />);
    const details = container.querySelector("details") as HTMLDetailsElement;

    expect(details.open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toBe("Asked 4 ways · majority, 2 of 4");
    expect(
      screen.getByText("2 of 4 runs agreed; 1 answered differently; 1 could not answer."),
    ).toBeInTheDocument();
  });

  it("shows each wording with where its run stands, and the SQL it wrote", () => {
    const { container } = render(<Candidates ensemble={record()} />);
    const runs = [...container.querySelectorAll(".candidates-runs > li")];

    expect(runs.map((item) => item.querySelector(".candidates-standing")?.textContent)).toEqual([
      "as asked · answered · answer A · delivered · says 719279.97",
      "reworded: the order of the clauses · answered · answer A · agreed · says 719279.97",
      "reworded · answered · answer B · set aside by the Judge · says 702113.40",
      "reworded · gave up",
    ]);
    expect(runs.map((item) => item.getAttribute("data-chosen"))).toEqual(["true", "false", "false", "false"]);
    expect(runs[2]?.querySelector(".code")?.textContent).toContain("banner_name");
    // A run that gave up wrote nothing worth showing, and says why it did not count.
    expect(runs[3]?.querySelector(".code")).toBeNull();
    expect(runs[3]?.querySelector(".candidates-reasons")?.textContent).toBe(
      "E1 answered: gave up after 7 attempts; E3 complete",
    );
  });

  it("names the answers that lost the vote and how their query differs", () => {
    render(<Candidates ensemble={record()} />);
    expect(
      within(screen.getByLabelText("Answers that lost the vote")).getByRole("listitem").textContent,
    ).toBe("Run 2 answered differently (702113.40): filters on banner_name.");
  });

  it("names several runs together, and says nothing of a difference that was not read", () => {
    const dissent = [
      { group: 1, members: [1, 3], signature: "34.65", differs: "" },
      { group: 2, members: [2, 4, 5], signature: "12", differs: "" },
    ];
    render(<Candidates ensemble={record({ dissent })} />);
    expect(
      within(screen.getByLabelText("Answers that lost the vote")).getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual(["Runs 1 and 3 answered differently (34.65).", "Runs 2, 4 and 5 answered differently (12)."]);
  });

  it("gives the Judge's verdict on each answer by its letter, with its reason where it gave one", () => {
    render(<Candidates ensemble={record()} />);
    expect(
      within(screen.getByLabelText("The Judge")).getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual([
      "The Judge accepted answer A.",
      "The Judge set aside answer B: it keeps one banner the question does not name.",
    ]);
  });

  it("says so when the Judge could not be asked", () => {
    const judged = { verdicts: [], set_aside: [], error: "TimeoutError" };
    render(<Candidates ensemble={record({ judged })} />);
    expect(screen.getByLabelText("The Judge").textContent).toBe(
      "The Judge could not be asked, so the runs voted alone: TimeoutError",
    );
  });

  it("lists the rewordings that were written and not run", () => {
    render(<Candidates ensemble={record()} />);
    expect(screen.getByLabelText("Rewordings that were not run").textContent).toBe(
      "Not run: What did Dairy sell? · F2 literals",
    );
  });

  it("shows only what the record holds: one run, no vote, no Judge, nothing discarded", () => {
    // The Judge off and one wording run: every optional part is absent, and
    // none of them is drawn as an empty section.
    const { container } = render(<Candidates ensemble={{ candidates: [run()], chosen: 0 }} />);

    expect(container.querySelector("summary")?.textContent).toBe("Asked 1 way");
    expect(container.querySelector(".candidates-line")).toBeNull();
    expect(container.querySelector(".candidates-notes")).toBeNull();
    expect(container.querySelector(".candidates-standing")?.textContent).toBe(
      "as asked · answered · answer A · delivered · says 719279.97",
    );
  });

  it("says why the vote went as it did when no line was written, and nothing when neither was", () => {
    const { container, rerender } = render(<Candidates ensemble={record({ line: "" })} />);
    expect(container.querySelector(".candidates-line")?.textContent).toBe("2 of 3 admissible agree");

    const agreement = { agreed: 1, total: 1, level: "single", why: "" };
    rerender(<Candidates ensemble={record({ line: "", agreement, judged: null })} />);
    expect(container.querySelector(".candidates-line")).toBeNull();
    expect(screen.queryByLabelText("The Judge")).toBeNull();
  });

  it("marks no run as agreeing when the record does not say which did", () => {
    const { container } = render(<Candidates ensemble={{ candidates: [run({ index: 1 })], chosen: 0 }} />);
    expect(container.querySelector(".candidates-standing")?.textContent).not.toContain("agreed");
  });

  it("shows a rewording as text, never as markup", () => {
    const hostile = run({ index: 1, origin: "paraphrase", wording: "<img src=x onerror=alert(1)>", changed: "<b>x</b>" });
    const { container } = render(<Candidates ensemble={record({ candidates: [run(), hostile] })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
  });
});

describe("Original, with the runs behind it", () => {
  it("says a 6.3 submission was a single run", () => {
    render(<Original submission={makeSubmission()} />);
    expect(screen.getByText("A single run; no ensemble record.")).toBeInTheDocument();
  });

  it("shows the candidates beneath the SQL that was delivered", () => {
    const { container } = render(<Original submission={makeSubmission({ ensemble: record() })} />);
    const delivered = container.querySelector(".original > .code") as HTMLElement;
    const candidates = container.querySelector(".candidates") as HTMLElement;
    expect(delivered.compareDocumentPosition(candidates) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
