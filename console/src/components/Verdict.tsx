/**
 * What the agent would have done with this query.
 *
 * The console's reason to exist. The rows say what the query returns; this
 * says whether the agent would ever have got that far -- which gate would
 * have stopped it, in that gate's own words -- and the estimate the planner
 * gate judged it by. A query the agent refused is often a perfectly good
 * query that tripped a rule, and which rule is the diagnosis.
 */

import { formatNumber, formatShare } from "../api/format";
import type { QueryResult, Stage } from "../api/types";

/** The gates, as the architecture names them. */
export const GATES: Record<Stage, string> = {
  static: "static validator",
  planner: "planner gate",
  runtime: "executor",
};

export interface VerdictProps {
  result: QueryResult;
}

function headline(result: QueryResult): string {
  const { agent } = result;
  if (agent.stage) return `The agent would refuse this at the ${GATES[agent.stage]}`;
  if (result.mode === "plan") return "Passes the static validator and the planner gate";
  return "The agent would accept this query";
}

export function Verdict({ result }: VerdictProps) {
  const { agent } = result;
  // Nothing planned and nothing failed: the validator stopped it before the
  // database was asked anything, and the console does not run it either.
  const refusedUnrun = !result.executed && result.plan === null && result.error === null;
  const cost = result.plan_cost;

  return (
    <section className={`verdict ${agent.accepted ? "verdict-good" : "verdict-bad"}`} aria-label="Agent verdict">
      <h2 className="verdict-title">{headline(result)}</h2>

      {agent.issues.length > 0 && (
        <ul className="issues">
          {agent.issues.map((issue, index) => (
            <li key={`${issue.stage}-${index}`}>
              <span className={`gate gate-${issue.stage}`}>{GATES[issue.stage]}</span>
              <pre className="issue-message">{issue.message}</pre>
            </li>
          ))}
        </ul>
      )}

      {refusedUnrun && (
        <p className="small">
          Not run here either. The console runs only what the agent's validator lets through, except
          that it will look outside the agent's schema -- the catalogue is part of troubleshooting.
        </p>
      )}

      {agent.notes.map((note) => (
        <p className="small warn" key={note}>
          {note}
        </p>
      ))}

      <p className="small muted verdict-facts">
        {cost !== null && (
          <span className={cost > result.max_plan_cost ? "bad" : undefined}>
            estimated cost {formatNumber(cost)} of the {formatNumber(result.max_plan_cost, 0)} ceiling (
            {formatShare(cost, result.max_plan_cost)}) ·{" "}
          </span>
        )}
        {result.mode === "plan" ? "planned, not run" : result.executed ? "ran" : "did not run"} in{" "}
        {formatNumber(result.elapsed_ms, 1)} ms
      </p>
    </section>
  );
}
