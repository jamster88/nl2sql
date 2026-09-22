/**
 * The answer.
 *
 * Ordered by what a reader wants in the order they want it: the sentence
 * first, then the picture, then the rows, then -- folded away -- the SQL,
 * the matched literals and the per-node cost that say how the sentence was
 * arrived at. Someone who trusts the agent never opens the last three;
 * someone checking it opens all of them, and they are all there.
 *
 * Two things about the sentence are worth knowing, because both look like
 * bugs in this file if they are not:
 *
 * * **It comes from `narrative`, not `answer`.** `answer` is a whole
 *   markdown document -- the prose, then the rows as a markdown table, then
 *   the caveats -- which is what a terminal wants. Rendering it here would
 *   print the pipe characters of a table this page is already drawing.
 * * **It is unescaped on the way in.** The prose is escaped for markdown,
 *   which renders raw HTML; React renders text, so `&amp;` would arrive on
 *   screen as five characters. See `api/text.ts`.
 *
 * And the sentence is not one block: the audit ties each of its sentences to
 * the cells it was read from, so each is its own hoverable span and hovering
 * one lights up its cells in the table. That is the clearest thing this API
 * makes possible, and it costs a `Set`.
 *
 * Three cases are not an answer at all and are handled before any of it:
 *
 * * **A refusal is a success.** The agent decided the question was out of
 *   scope or unsafe and said so; `status` is still `succeeded` and there is
 *   no table because no query ran. Showing an empty result grid here would
 *   report a failure that did not happen.
 * * **An ambiguous question comes back with a clarification**, which is a
 *   question for the user, so it is put where an answer would be.
 * * **A failed run** still carries the SQL it tried and the attempts it
 *   made, and those are the useful part.
 */

import { useState } from "react";

import { Chart } from "../charts/Chart";
import { Disclosure } from "./Disclosure";
import { FeedbackBar } from "./FeedbackBar";
import { ResultTable, cellKey } from "./ResultTable";
import { useFeedback } from "../feedback/useFeedback";
import { leadParagraph, plainText } from "../api/text";
import type { FeedbackStore } from "../feedback/store";
import type { Answer, Job, Pipeline } from "../api/types";

export interface AnswerViewProps {
  job: Job;
  store: FeedbackStore;
  /** From `/v1/meta`: which optional stages ran, so nothing empty is framed. */
  pipeline: Pipeline | null;
}

export function AnswerView({ job, store, pipeline }: AnswerViewProps) {
  const [highlight, setHighlight] = useState<ReadonlySet<string>>(new Set());
  const feedback = useFeedback(store, job.id, job.question);
  const answer = job.answer;

  return (
    <article className="answer" aria-label="Answer">
      <header className="answer-head">
        <h2 className="answer-question">{job.question}</h2>
        {job.duration_ms !== null && (
          <span className="answer-timing muted">{(job.duration_ms / 1000).toFixed(1)}s</span>
        )}
      </header>

      {job.status === "failed" || answer === null ? (
        <Failure job={job} />
      ) : answer.verdict !== "proceed" ? (
        <Refusal answer={answer} />
      ) : (
        <>
          <Narrative answer={answer} onHighlight={setHighlight} />

          {answer.result && answer.chart && (
            <Chart result={answer.result} spec={answer.chart} />
          )}

          {answer.result && <ResultTable result={answer.result} highlight={highlight} />}

          {pipeline?.audit !== false && <Audit answer={answer} />}
        </>
      )}

      <div className="answer-details">
        {answer?.sql && (
          <Disclosure summary="SQL" badge={answer.attempts > 1 ? `${answer.attempts} attempts` : undefined}>
            <pre className="sql">{answer.sql}</pre>
            {answer.tables.length > 0 && (
              <p className="muted">Tables: {answer.tables.join(", ")}</p>
            )}
            {answer.plan_cost !== null && (
              <p className="muted">Planner cost estimate: {answer.plan_cost.toLocaleString("en-US")}</p>
            )}
          </Disclosure>
        )}

        {answer && answer.literals.length > 0 && (
          <Disclosure summary="Matched values" badge={String(answer.literals.length)}>
            <ul className="literals">
              {answer.literals.map((literal, index) => (
                <li key={index}>
                  <code>{literal.phrase}</code> → <code>{literal.value}</code>{" "}
                  <span className="muted">
                    in {literal.table}.{literal.column} ({literal.score.toFixed(2)})
                  </span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}

        {answer && answer.trace.length > 0 && (
          <Disclosure summary="How long each step took" badge={`${answer.trace.length} steps`}>
            <Trace answer={answer} />
          </Disclosure>
        )}
      </div>

      <FeedbackBar
        verdict={feedback.verdict}
        onVote={feedback.vote}
        recordedAt={feedback.record?.at}
      />
    </article>
  );
}

interface NarrativeProps {
  answer: Answer;
  onHighlight: (cells: ReadonlySet<string>) => void;
}

/**
 * The answer as a paragraph whose sentences are each traceable.
 *
 * Falls back to the prose at the top of the markdown answer when there are
 * no claims to hang the sentences on -- which is what happens on a server
 * running without the narrator, and for any answer the audit dropped every
 * claim from.
 */
function Narrative({ answer, onHighlight }: NarrativeProps) {
  if (answer.claims.length === 0) {
    const text = plainText(answer.narrative) || leadParagraph(answer.answer);
    return text ? <p className="answer-headline">{text}</p> : null;
  }

  return (
    <p className="answer-headline">
      {answer.claims.map((claim, index) => (
        <span
          key={index}
          className="claim"
          onMouseEnter={() =>
            onHighlight(new Set(claim.cells.map(([row, column]) => cellKey(row, column))))
          }
          onMouseLeave={() => onHighlight(new Set())}
        >
          {plainText(claim.text)}
          {claim.formula && <code className="claim-formula">{claim.formula}</code>}{" "}
        </span>
      ))}
    </p>
  );
}

function Failure({ job }: { job: Job }) {
  return (
    <div className="notice notice-error" role="alert">
      <strong>The run failed.</strong>
      <p>{job.error ?? "The server did not say why."}</p>
      {job.answer?.sql && <pre className="sql">{job.answer.sql}</pre>}
    </div>
  );
}

/** The labels the pipeline's own verdicts get in the interface. */
export const VERDICT_TITLES: Record<string, string> = {
  refused: "The agent declined this question",
  out_of_domain: "That is outside what this database covers",
  ambiguous: "That question has more than one answer",
};

function Refusal({ answer }: { answer: Answer }) {
  return (
    <div className="notice notice-verdict" role="status">
      <strong>{VERDICT_TITLES[answer.verdict] ?? "The agent did not answer"}</strong>
      <p>{plainText(answer.clarification ?? "") || leadParagraph(answer.answer)}</p>
    </div>
  );
}

function Audit({ answer }: { answer: Answer }) {
  const { audit } = answer;
  const problems = [
    ...audit.unsupported_claims.map((claim) => `Unsupported: ${plainText(claim)}`),
    ...audit.drop_reasons.map(plainText),
    ...(audit.semantic_issue ? [plainText(audit.semantic_issue)] : []),
  ];

  if (audit.passed && problems.length === 0) {
    return (
      <p className="audit audit-passed">
        <span className="audit-icon" aria-hidden="true">
          ✓
        </span>
        Every number in this answer was checked against the rows above.
      </p>
    );
  }

  return (
    <div className="audit audit-failed" role="status">
      <span className="audit-icon" aria-hidden="true">
        !
      </span>
      <div>
        <strong>The audit found something.</strong>
        <ul>
          {problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * Per-node cost, as a bar per row.
 *
 * Not a chart: it is a list that happens to be sorted, and drawing it with
 * the chart machinery would put an axis and a legend around eight numbers
 * nobody compares across questions.
 */
function Trace({ answer }: { answer: Answer }) {
  const slowest = Math.max(...answer.trace.map((entry) => entry.ms), 1);
  return (
    <ul className="trace">
      {answer.trace.map((entry, index) => (
        <li key={`${entry.node}-${index}`}>
          <span className="trace-node">{entry.node}</span>
          <span className="trace-bar" style={{ width: `${(entry.ms / slowest) * 100}%` }} />
          <span className="trace-ms">{(entry.ms / 1000).toFixed(2)}s</span>
          {entry.model_calls > 0 && (
            <span className="muted">
              {entry.model_calls} model {entry.model_calls === 1 ? "call" : "calls"}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
