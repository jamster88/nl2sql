/**
 * The ensemble's surfaces (7.0): the lanes, the badge, the notes, the runs.
 *
 * What these hold is that the page draws what the server said and no more.
 * The lanes are drawn from each event's `candidate`, never from a step's
 * name; the split between the ensemble's own steps and a run's comes from
 * `/v1/meta`; and the sentence about the vote is the server's, not one this
 * page composed from the counts.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerView } from "../src/components/AnswerView";
import {
  AgreementBadge,
  Candidates,
  EnsembleNotes,
  LEVEL_LABELS,
  answerLetter,
  runsNamed,
} from "../src/components/Candidates";
import { Progress, runName } from "../src/components/Progress";
import { createFeedbackStore } from "../src/feedback/store";
import { afterLead } from "../src/api/text";
import type { ProgressEvent } from "../src/api/types";
import {
  OUTER_NODES,
  makeAnswer,
  makeCandidate,
  makeEnsemble,
  makeEnsembleSettings,
  makeJob,
  makeMeta,
  memoryStorage,
} from "./helpers";

afterEach(cleanup);

const store = () => createFeedbackStore(memoryStorage());

const INNER = ["supervise", "retrieve_schema", "generate_sql", "execute_query", "finish"];
const NODES = [...OUTER_NODES, ...INNER];

let seq = 0;
/** An event of the ensemble's own (`candidate` null) or of one run's. */
function event(step: string, candidate: number | null = null, detail = ""): ProgressEvent {
  seq += 1;
  return { seq, step, label: step.replace("_", " "), detail, at: "", candidate };
}

function chips(list: HTMLElement): string[] {
  return within(list)
    .getAllByRole("listitem")
    .map((item) => `${item.textContent}:${item.getAttribute("data-state")}`);
}

describe("Progress, under the ensemble", () => {
  const ensemble = makeEnsembleSettings();

  it("draws a waiting lane for every run it was told to expect, before anything has happened", () => {
    render(<Progress events={[]} nodes={NODES} done={false} ensemble={ensemble} />);

    expect(screen.getByRole("status").textContent).toBe("Queued");
    expect(screen.getByText("0 / 4 runs")).toBeInTheDocument();
    const lanes = within(screen.getByLabelText("The runs")).getAllByText("waiting");
    expect(lanes).toHaveLength(4);
    // Nothing says yet which of its steps come before the runs, so they are
    // all above and nothing is below.
    expect(within(screen.getByLabelText("Before the runs")).getAllByRole("listitem")).toHaveLength(11);
    expect(screen.queryByLabelText("After the runs")).toBeNull();
  });

  it("gives every waiting lane the steps a run takes, none of them done", () => {
    const { container } = render(<Progress events={[]} nodes={NODES} done={false} ensemble={ensemble} />);
    const lane = container.querySelector('.progress-lane[data-state="waiting"]') as HTMLElement;
    expect(chips(lane)).toEqual(INNER.map((step) => `${step}:pending`));
  });

  it("reports one of its own steps in its own words, with no run named", () => {
    const events = [event("screen", null, "proceed"), event("paraphrase", null, "3 rewording(s)")];
    render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);
    expect(screen.getByRole("status").textContent).toBe("paraphrase — 3 rewording(s)");
  });

  it("puts a run's step in that run's lane, and names the run", () => {
    const events = [
      event("screen"),
      event("paraphrase"),
      event("plan_wave"),
      event("supervise", 0),
      event("generate_sql", 0, "3 tables"),
    ];
    render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);

    expect(screen.getByRole("status").textContent).toBe("As asked: generate sql — 3 tables");
    const lane = screen.getByLabelText("As asked");
    expect(lane.getAttribute("data-state")).toBe("active");
    expect(chips(lane)).toEqual([
      "supervise:done",
      "retrieve_schema:pending",
      "generate sql:done",
      "execute_query:pending",
      "finish:pending",
    ]);
    expect(screen.getByText("1 / 4 runs")).toBeInTheDocument();
  });

  it("divides its own steps at the first run: what came before above, the rest below", () => {
    const events = [event("screen"), event("paraphrase"), event("plan_wave"), event("supervise", 0)];
    render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);

    // `refuse` never ran and stays with its neighbours: the division is by
    // place in the server's list, not by what has finished.
    expect(chips(screen.getByLabelText("Before the runs"))).toEqual([
      "screen:done",
      "refuse:pending",
      "paraphrase:done",
      "screen_paraphrase:pending",
      "plan wave:done",
    ]);
    expect(chips(screen.getByLabelText("After the runs"))).toEqual(
      ["answer", "validate", "judge", "vote", "fuse", "deliver"].map((step) => `${step}:pending`),
    );
  });

  it("keeps a lane for each run in the order they began, and marks the one reporting", () => {
    const events = [
      event("plan_wave"),
      event("supervise", 0),
      event("finish", 0),
      event("supervise", 2, "seeded"),
    ];
    const { container } = render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);

    const lanes = [...container.querySelectorAll(".progress-lane")].map(
      (lane) => `${lane.querySelector(".progress-lane-name")?.textContent}:${lane.getAttribute("data-state")}`,
    );
    expect(lanes).toEqual(["As asked:reported", "Rewording 2:active", "Run 3:waiting", "Run 4:waiting"]);
    expect(screen.getByRole("status").textContent).toBe("Rewording 2: supervise — seeded");
    // A lane that is not the one reporting still says where its run got to.
    expect(screen.getByLabelText("As asked").querySelector(".progress-lane-current")?.textContent).toBe("finish");
  });

  it("stops waiting for runs once the ensemble has moved on to their answers", () => {
    // Three rewordings were asked for and two were faithful: the third lane
    // would otherwise wait for a run that is not coming.
    const events = [
      event("plan_wave"),
      event("finish", 0),
      event("finish", 1),
      event("finish", 2),
      event("answer", null, "3 run(s)"),
      event("validate"),
    ];
    const { container } = render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);

    expect(container.querySelectorAll(".progress-lane")).toHaveLength(3);
    expect(screen.getByText("3 / 3 runs")).toBeInTheDocument();
    expect(container.querySelector('.progress-lane[data-state="active"]')).toBeNull();
    expect(chips(screen.getByLabelText("After the runs")).slice(0, 2)).toEqual(["answer:done", "validate:done"]);
  });

  it("adds a lane for a run of the second wave when it reports", () => {
    const events = [
      event("plan_wave"),
      event("finish", 0),
      event("vote", null, "open"),
      event("plan_wave", null, "wave 2"),
      event("supervise", 5),
    ];
    render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);
    expect(screen.getByLabelText("Rewording 5").getAttribute("data-state")).toBe("active");
    expect(screen.getByText("2 / 2 runs")).toBeInTheDocument();
    // The planner ran before the first run, so it stays above, with its latest word.
    expect(within(screen.getByLabelText("Before the runs")).getByTitle("wave 2")).toBeInTheDocument();
  });

  it("says how many steps and runs it took when it is finished", () => {
    const events = [event("screen"), event("finish", 0), event("finish", 1), event("deliver")];
    const { container } = render(<Progress events={events} nodes={NODES} done ensemble={ensemble} />);
    expect(screen.getByRole("status").textContent).toBe("Finished — 4 steps in 2 runs");
    expect(container.querySelector('.progress-lane[data-state="active"]')).toBeNull();
  });

  it("uses the singular for one run", () => {
    const events = [event("screen"), event("finish", 0)];
    render(<Progress events={events} nodes={NODES} done ensemble={makeEnsembleSettings({ paraphrases: 0 })} />);
    expect(screen.getByRole("status").textContent).toBe("Finished — 2 steps in 1 run");
  });

  it("shows no lanes for a question that was refused before any run", () => {
    const events = [event("screen", null, "injection"), event("refuse")];
    const { container } = render(<Progress events={events} nodes={NODES} done ensemble={ensemble} />);
    expect(screen.queryByLabelText("The runs")).toBeNull();
    expect(container.querySelector(".progress-count")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Finished — 2 steps");
  });

  it("draws lanes from the stream alone when /v1/meta never said there would be runs", () => {
    // The stream is the truth. With no node list every step is shown as it
    // arrives, in the lane of the run that reported it.
    const events = [event("supervise", 0), event("repair", 0)];
    render(<Progress events={events} nodes={[]} done={false} />);

    expect(chips(screen.getByLabelText("As asked"))).toEqual(["supervise:done", "repair:done"]);
    expect(screen.getByText("1 / 1 runs")).toBeInTheDocument();
    expect(screen.queryByLabelText("Before the runs")).toBeNull();
    expect(screen.queryByLabelText("After the runs")).toBeNull();
  });

  it("shows one of its own steps the server never listed", () => {
    const events = [event("screen"), event("reconsider"), event("supervise", 0)];
    render(<Progress events={events} nodes={NODES} done={false} ensemble={ensemble} />);
    expect(within(screen.getByLabelText("Before the runs")).getByText("reconsider")).toBeInTheDocument();
  });

  it("is one row of steps, as before, on a server with the ensemble off", () => {
    const events = [{ seq: 1, step: "supervise", label: "screen", detail: "", at: "" }];
    const { container } = render(
      <Progress events={events} nodes={INNER} done={false} ensemble={makeEnsembleSettings({ enabled: false, nodes: [] })} />,
    );
    expect(container.querySelector(".progress-lanes")).toBeNull();
    expect(screen.getByText("1 / 5")).toBeInTheDocument();
  });

  it("names the question as asked and each rewording by its number", () => {
    expect(runName(0)).toBe("As asked");
    expect(runName(3)).toBe("Rewording 3");
  });
});

describe("the agreement badge", () => {
  it("says the level, the count and the agent's own sentence about the vote", () => {
    const { container } = render(<AgreementBadge ensemble={makeEnsemble()} />);
    expect(screen.getByText("Most agree · 3 of 4")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 runs agreed; 1 answered differently.")).toBeInTheDocument();
    expect(container.querySelector(".agreement")?.getAttribute("data-level")).toBe("majority");
  });

  it("has a word for every level the server can send", () => {
    expect(Object.keys(LEVEL_LABELS).sort()).toEqual(
      ["contested", "judged", "majority", "none", "single", "unanimous"],
    );
  });

  it("falls back to why the vote went as it did when no line was written", () => {
    const agreement = { admissible: 0, agreed: 0, total: 4, level: "none" as const, why: "no run answered", set_aside: 0 };
    render(<AgreementBadge ensemble={makeEnsemble({ agreement, line: "" })} />);
    expect(screen.getByText("No answer · 0 of 4")).toBeInTheDocument();
    expect(screen.getByText("no run answered")).toBeInTheDocument();
  });

  it("is the badge alone when there is nothing to say beside it", () => {
    const agreement = { admissible: 1, agreed: 1, total: 1, level: "single" as const, why: "", set_aside: 0 };
    const { container } = render(<AgreementBadge ensemble={makeEnsemble({ agreement, line: "" })} />);
    expect(container.querySelector(".agreement-line")).toBeNull();
  });

  it("shows a reason the Judge wrote as text, never as markup", () => {
    const line = 'The Judge set aside the answer 3 of 4 runs gave -- <img src=x onerror="alert(1)"> -- and accepted this one.';
    const { container } = render(<AgreementBadge ensemble={makeEnsemble({ line })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".agreement-line")?.textContent).toBe(line);
  });
});

describe("the ensemble's notes", () => {
  it("names each answer that lost, with how its query differs", () => {
    render(<EnsembleNotes ensemble={makeEnsemble()} />);
    expect(
      screen.getByText("Run 2 answered differently (Dairy & Eggs 512004.10): filters on banner_name."),
    ).toBeInTheDocument();
  });

  it("names several runs together, and says nothing of a difference it could not read", () => {
    const dissent = [{ group: 1, members: [1, 2, 3], signature: "34.65", differs: "" }];
    render(<EnsembleNotes ensemble={makeEnsemble({ dissent })} />);
    expect(screen.getByText("Runs 1, 2 and 3 answered differently (34.65).")).toBeInTheDocument();
    expect(runsNamed([1, 3])).toBe("Runs 1 and 3");
  });

  it("says which run a joined column is from, since the query shown does not return it", () => {
    const joined_columns = [{ column: "region", from_candidate: 2, key: "store_id", table: "dim_store" }];
    render(<EnsembleNotes ensemble={makeEnsemble({ joined_columns, dissent: [] })} />);
    expect(
      screen.getByText(
        "region is from run 2, which agreed, joined on store_id; this answer's query does not return it.",
      ),
    ).toBeInTheDocument();
  });

  it("says which column was left out, and why", () => {
    const declined_columns = [{ column: "units", from_candidate: 1, why: "two rows matched one key" }];
    render(<EnsembleNotes ensemble={makeEnsemble({ declined_columns, dissent: [] })} />);
    expect(
      screen.getByText("units, which run 1 also returned, was left out: two rows matched one key."),
    ).toBeInTheDocument();
  });

  it("is absent when every run agreed and nothing was joined or declined", () => {
    const { container } = render(<EnsembleNotes ensemble={makeEnsemble({ dissent: [] })} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("the runs, folded away", () => {
  it("is titled by how many ways the question was asked", () => {
    render(<Candidates ensemble={makeEnsemble()} />);
    expect(screen.getByText("The question, asked 4 ways")).toBeInTheDocument();
  });

  it("uses the singular for one way", () => {
    render(<Candidates ensemble={makeEnsemble({ candidates: [makeCandidate()] })} />);
    expect(screen.getByText("The question, asked 1 way")).toBeInTheDocument();
  });

  it("is absent when nothing ran", () => {
    const { container } = render(<Candidates ensemble={makeEnsemble({ candidates: [] })} />);
    expect(container.firstChild).toBeNull();
  });

  it("lists each wording, what was changed to make it, how it ended and its SQL", () => {
    const { container } = render(<Candidates ensemble={makeEnsemble()} />);
    const runs = [...container.querySelectorAll(".candidate")];

    expect(runs.map((run) => run.querySelector(".candidate-wording")?.textContent)).toEqual([
      "0Which department had the highest net sales in fiscal year 2025?",
      "1In fiscal 2025, which department sold the most, net?",
      "2Top department by net sales, FY2025?",
      "3Which department's net sales were highest in FY2025?",
    ]);
    expect(runs.map((run) => run.querySelector(".candidate-facts")?.textContent)).toEqual([
      "As asked · answered · answer A · delivered · says Produce 719279.97 · 31.3s",
      "Reworded: the order of the clauses · answered · answer A · agreed · says Produce 719279.97 · 31.3s",
      "Reworded: shortened · answered · answer B · says Dairy & Eggs 512004.10 · 3 attempts · 31.3s",
      "Reworded · answered · answer A · agreed · says Produce 719279.97 · 31.3s",
    ]);
    expect(runs[2]?.querySelector(".sql")?.textContent).toContain("banner_name");
    expect(runs.map((run) => run.getAttribute("data-chosen"))).toEqual(["true", "false", "false", "false"]);
  });

  it("says why a run did not count, and shows no SQL where none was written", () => {
    const gaveUp = makeCandidate({
      index: 1,
      origin: "paraphrase",
      outcome: "gave_up",
      admissible: false,
      reasons: ["E1 answered: gave up after 7 attempts"],
      sql: "",
      signature: "",
      group: null,
      wave: 2,
    });
    const { container } = render(
      <Candidates ensemble={makeEnsemble({ candidates: [makeCandidate(), gaveUp], fused_from: [0] })} />,
    );
    const run = container.querySelectorAll(".candidate")[1] as HTMLElement;

    expect(run.querySelector(".candidate-facts")?.textContent).toBe("Reworded · gave up · wave 2 · 31.3s");
    expect(within(run).getByText("E1 answered: gave up after 7 attempts")).toBeInTheDocument();
    expect(run.querySelector(".sql")).toBeNull();
    expect(run.getAttribute("data-admissible")).toBe("false");
  });

  it("marks the runs whose answer the Judge set aside, and gives its reason by the answer's letter", () => {
    const judged = {
      verdicts: [
        { group: 0, accepted: false, why: "it filters on the example channels" },
        { group: 1, accepted: true, why: "" },
      ],
      set_aside: [0, 1, 3],
      overruled: true,
      instead_of: 0,
      model: "",
      error: "",
    };
    const { container } = render(<Candidates ensemble={makeEnsemble({ judged, chosen: 2, fused_from: [2] })} />);

    expect(container.querySelector(".candidate-facts")?.textContent).toContain("set aside by the Judge");
    const judge = screen.getByLabelText("The Judge");
    expect(within(judge).getByText("Answer A: set aside — it filters on the example channels")).toBeInTheDocument();
    expect(within(judge).getByText("Answer B: accepted")).toBeInTheDocument();
    expect(judge.querySelector("h3")?.textContent).toBe("The Judge");
    expect(answerLetter(2)).toBe("C");
  });

  it("names the model that judged, when the server says which", () => {
    render(<Candidates ensemble={makeEnsemble()} />);
    expect(screen.getByLabelText("The Judge").querySelector("h3")?.textContent).toBe("The Judge · judge-model");
  });

  it("says so when the Judge could not be asked", () => {
    const judged = { verdicts: [], set_aside: [], overruled: false, instead_of: null, model: "", error: "TimeoutError" };
    render(<Candidates ensemble={makeEnsemble({ judged })} />);
    expect(
      screen.getByText("It could not be asked, so the runs voted alone: TimeoutError"),
    ).toBeInTheDocument();
  });

  it("leaves the Judge out when it was not asked", () => {
    render(<Candidates ensemble={makeEnsemble({ judged: null })} />);
    expect(screen.queryByLabelText("The Judge")).toBeNull();
  });

  it("lists the rewordings that were written and not run, and the check each failed", () => {
    const discarded = [
      { index: 4, text: "Which store had the highest sales?", changed: "department to store", reason: "F2 literals" },
      { index: 5, text: "Best department?", changed: "", reason: "F5 distinct" },
    ];
    render(<Candidates ensemble={makeEnsemble({ discarded })} />);
    const section = screen.getByLabelText("Rewordings that were not run");

    expect(within(section).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Which store had the highest sales? · changed: department to store · F2 literals",
      "Best department? · F5 distinct",
    ]);
  });

  it("says nothing of discarded rewordings when there were none", () => {
    render(<Candidates ensemble={makeEnsemble({ discarded: [] })} />);
    expect(screen.queryByLabelText("Rewordings that were not run")).toBeNull();
  });

  it("shows a rewording as text, never as markup", () => {
    const hostile = makeCandidate({ index: 1, origin: "paraphrase", wording: "<script>alert(1)</script>", changed: "<b>x</b>" });
    const { container } = render(<Candidates ensemble={makeEnsemble({ candidates: [makeCandidate(), hostile] })} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });
});

describe("AnswerView, under the ensemble", () => {
  const pipeline = makeMeta().pipeline;
  const asked = (answer = makeAnswer({ ensemble: makeEnsemble() })) =>
    render(<AnswerView job={makeJob({ answer })} store={store()} pipeline={pipeline} />);

  it("shows the agreement beside the sentence, the dissent among the notes, and the runs folded away", async () => {
    const { container } = asked();

    const badge = container.querySelector(".agreement") as HTMLElement;
    const headline = container.querySelector(".answer-headline") as HTMLElement;
    expect(badge.compareDocumentPosition(headline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByLabelText("What the other runs said")).toBeInTheDocument();

    await userEvent.click(screen.getByText("The question, asked 4 ways"));
    expect(screen.getByText("In fiscal 2025, which department sold the most, net?")).toBeVisible();
  });

  it("is the answer it always was when one run gave it", () => {
    for (const ensemble of [null, undefined]) {
      const { container, unmount } = asked(makeAnswer(ensemble === null ? { ensemble } : {}));
      expect(container.querySelector(".agreement")).toBeNull();
      expect(screen.queryByText(/The question, asked/)).toBeNull();
      unmount();
    }
  });

  it("falls back to the prose after the agreement line, which the badge already says", () => {
    const answer = makeAnswer({
      claims: [],
      narrative: "",
      answer: "*3 of 4 runs agreed; 1 answered differently.*\n\nProduce led.\n\n| department |\n| --- |",
      ensemble: makeEnsemble(),
    });
    const { container } = asked(answer);
    expect(container.querySelector(".answer-headline")?.textContent).toBe("Produce led.");
  });

  it("shows no sentence when the agreement line is all the document has", () => {
    const answer = makeAnswer({ claims: [], narrative: "", answer: "*3 of 4 runs agreed.*", ensemble: makeEnsemble() });
    const { container } = asked(answer);
    expect(container.querySelector(".answer-headline")).toBeNull();
  });

  it("reads the document from its top when the ensemble wrote no line", () => {
    const answer = makeAnswer({
      claims: [],
      narrative: "",
      answer: "Produce led.\n\n| department |",
      ensemble: makeEnsemble({ line: "" }),
    });
    const { container } = asked(answer);
    expect(container.querySelector(".answer-headline")?.textContent).toBe("Produce led.");
  });

  it("shows no badge on a refusal: nothing ran, and the notice says so", () => {
    const agreement = { admissible: 0, agreed: 0, total: 0, level: "none" as const, why: "screened out", set_aside: 0 };
    const answer = makeAnswer({
      verdict: "refused",
      result: null,
      sql: "",
      ensemble: makeEnsemble({ agreement, line: "", candidates: [], dissent: [], discarded: [], judged: null }),
    });
    const { container } = asked(answer);
    expect(container.querySelector(".agreement")).toBeNull();
    expect(screen.queryByText(/The question, asked/)).toBeNull();
  });
});

describe("afterLead", () => {
  it("is the document without its first block", () => {
    expect(afterLead("*line*\n\nProse.\n\n| t |")).toBe("Prose.\n\n| t |");
    expect(afterLead("*line*\n  \nProse.")).toBe("Prose.");
  });

  it("is empty when the first block is all there is", () => {
    expect(afterLead("*line*")).toBe("");
    expect(afterLead("")).toBe("");
  });

  it("drops as many blocks as it is told: a follow-up's reading as well as the agreement (8.0)", () => {
    const document = "Read as: *q*\n\n*line*\n\nProse.\n\n| t |";
    expect(afterLead(document, 2)).toBe("Prose.\n\n| t |");
    expect(afterLead(document, 0)).toBe(document);
    expect(afterLead("Read as: *q*\n\n*line*", 2)).toBe("");
  });
});
