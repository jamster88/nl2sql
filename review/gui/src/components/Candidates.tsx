/**
 * The other runs behind an answer (7.0).
 *
 * Since 7.0 the agent asks a question several ways, runs each wording
 * through the whole pipeline, has a Judge read the answers and lets the runs
 * it left standing vote. The submission's SQL is the one that was delivered.
 * A reviewer fixing a wrong answer wants the rest: the queries that agreed
 * with it, the ones that did not and how they differ, and what the Judge
 * made of each -- very often the right query is already among them, written
 * by a run that lost the vote.
 *
 * Read-only, like everything on this side of the screen. Promotion is
 * unchanged by any of it: a golden pair is the question as the person asked
 * it and the SQL the reviewer settles on, whichever run first wrote it.
 *
 * The record is whatever the agent staged, so it is read without trusting
 * its shape. A submission from before 7.0 holds `{}`, and that is said in
 * words rather than drawn as an empty list -- a reviewer should not have to
 * wonder whether the runs failed to load.
 */

import type { EnsembleRecord, EnsembleRun } from "../api/types";

export interface CandidatesProps {
  ensemble: EnsembleRecord;
}

/** An answer's letter: the first is A, as the Judge was shown them. */
function letter(group: number): string {
  return String.fromCharCode(65 + group);
}

/** "Run 2", "Runs 1 and 3", "Runs 1, 2 and 3". */
function runsNamed(members: readonly number[]): string {
  if (members.length === 1) return `Run ${members[0]}`;
  return `Runs ${members.slice(0, -1).join(", ")} and ${members[members.length - 1]}`;
}

/** Where a run stands, as the words beside its wording. */
function standing(run: EnsembleRun, ensemble: EnsembleRecord): string {
  const tags = [
    run.origin === "original" ? "as asked" : `reworded${run.changed ? `: ${run.changed}` : ""}`,
    run.outcome.replace("_", " "),
  ];
  if (run.group !== null) tags.push(`answer ${letter(run.group)}`);
  if (run.index === ensemble.chosen) tags.push("delivered");
  else if (ensemble.fused_from?.includes(run.index)) tags.push("agreed");
  if (ensemble.judged?.set_aside.includes(run.index)) tags.push("set aside by the Judge");
  if (run.signature) tags.push(`says ${run.signature}`);
  return tags.join(" · ");
}

export function Candidates({ ensemble }: CandidatesProps) {
  const runs = ensemble.candidates ?? [];
  if (runs.length === 0) {
    return <p className="candidates-none muted">A single run; no ensemble record.</p>;
  }
  const { agreement, judged } = ensemble;
  const dissent = ensemble.dissent ?? [];
  const discarded = ensemble.discarded ?? [];

  return (
    <details className="candidates">
      <summary>
        Asked {runs.length} {runs.length === 1 ? "way" : "ways"}
        {agreement && (
          <span className="muted">
            {" "}
            · {agreement.level}, {agreement.agreed} of {agreement.total}
          </span>
        )}
      </summary>

      {(ensemble.line || agreement?.why) && (
        <p className="candidates-line">{ensemble.line || agreement?.why}</p>
      )}

      <ol className="candidates-runs">
        {runs.map((run) => (
          <li key={run.index} data-chosen={run.index === ensemble.chosen}>
            <p className="candidates-wording">
              <span className="mono">{run.index}</span> {run.wording}
            </p>
            <p className="muted candidates-standing">{standing(run, ensemble)}</p>
            {run.reasons.length > 0 && <p className="candidates-reasons">{run.reasons.join("; ")}</p>}
            {run.sql && (
              <pre className="code">
                <code>{run.sql}</code>
              </pre>
            )}
          </li>
        ))}
      </ol>

      {dissent.length > 0 && (
        <ul className="candidates-notes" aria-label="Answers that lost the vote">
          {dissent.map((lost) => (
            <li key={lost.group}>
              {runsNamed(lost.members)} answered differently ({lost.signature})
              {lost.differs ? `: ${lost.differs}` : ""}.
            </li>
          ))}
        </ul>
      )}

      {judged && (
        <ul className="candidates-notes" aria-label="The Judge">
          {judged.error ? (
            <li>The Judge could not be asked, so the runs voted alone: {judged.error}</li>
          ) : (
            judged.verdicts.map((verdict) => (
              <li key={verdict.group}>
                The Judge {verdict.accepted ? "accepted" : "set aside"} answer {letter(verdict.group)}
                {verdict.why ? `: ${verdict.why}` : ""}.
              </li>
            ))
          )}
        </ul>
      )}

      {discarded.length > 0 && (
        <ul className="candidates-notes" aria-label="Rewordings that were not run">
          {discarded.map((rewording) => (
            <li key={rewording.index}>
              Not run: {rewording.text} <span className="muted">· {rewording.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
