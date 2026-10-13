/**
 * The question, asked several ways (7.0).
 *
 * An answer under the ensemble is not one run's: the question was reworded,
 * every wording was run through the whole pipeline, a Judge read the
 * answers, and the runs it left standing voted. The sentence at the top of
 * the page is what came out of that. This is what went into it, for the
 * reader who wants to know why they should believe "3 of 4 runs agreed" --
 * or why they should not.
 *
 * Three parts, in the order they happened:
 *
 * * **The agreement line**, shown beside the sentence: the count, the level
 *   in a word, and the agent's own sentence about it. The sentence comes
 *   from the server (`Ensemble.line`) rather than being composed here, so
 *   this page, the desktop client and the terminal say the same thing.
 * * **The notes**: each answer that lost, with how its query differs; each
 *   column that was joined on from another run that agreed, since the query
 *   shown does not return it; and each column that would not join, and why.
 * * **The runs**, folded away: every wording, what was changed to make it,
 *   how its run ended, which answer it gave and its SQL; what the Judge said
 *   of each answer; and the rewordings that were written and never run.
 *
 * Everything here that a model wrote -- a rewording, what it says it
 * changed, the Judge's reason -- is rendered as text. React does that by
 * default, and nothing in this file opts out of it. None of it is unescaped
 * either: unlike `answer`, these fields were never escaped for markdown.
 */

import { Disclosure } from "./Disclosure";
import type { Ensemble, EnsembleAgreement, EnsembleCandidate } from "../api/types";

/** The vote's level, in a word or two, for the badge. */
export const LEVEL_LABELS: Record<EnsembleAgreement["level"], string> = {
  unanimous: "All agree",
  majority: "Most agree",
  judged: "The Judge's choice",
  contested: "Contested",
  single: "One answer",
  none: "No answer",
};

/** How a run ended, as the runs list says it. */
export const OUTCOME_LABELS: Record<EnsembleCandidate["outcome"], string> = {
  answered: "answered",
  gave_up: "gave up",
  refused: "refused",
};

/** An answer's letter: the first group is A, as the Judge was shown them. */
export function answerLetter(group: number): string {
  return String.fromCharCode(65 + group);
}

/** "Run 2", "Runs 1 and 3", "Runs 1, 2 and 3". */
export function runsNamed(members: readonly number[]): string {
  if (members.length === 1) return `Run ${members[0]}`;
  return `Runs ${members.slice(0, -1).join(", ")} and ${members[members.length - 1]}`;
}

export interface AgreementProps {
  ensemble: Ensemble;
}

/** The agreement line, as a badge beside the sentence. */
export function AgreementBadge({ ensemble }: AgreementProps) {
  const { agreement } = ensemble;
  const line = ensemble.line || agreement.why;
  return (
    <p className="agreement" data-level={agreement.level}>
      <span className="agreement-badge">
        {LEVEL_LABELS[agreement.level]} · {agreement.agreed} of {agreement.total}
      </span>
      {line && <span className="agreement-line">{line}</span>}
    </p>
  );
}

/** What the vote left out or brought in, among the answer's notes. */
export function EnsembleNotes({ ensemble }: AgreementProps) {
  const notes = [
    ...ensemble.joined_columns.map(
      (joined) =>
        `${joined.column} is from run ${joined.from_candidate}, which agreed, joined on ${joined.key}; ` +
        "this answer's query does not return it.",
    ),
    ...ensemble.declined_columns.map(
      (declined) =>
        `${declined.column}, which run ${declined.from_candidate} also returned, was left out: ${declined.why}.`,
    ),
    ...ensemble.dissent.map(
      (dissent) =>
        `${runsNamed(dissent.members)} answered differently (${dissent.signature})` +
        `${dissent.differs ? `: ${dissent.differs}` : ""}.`,
    ),
  ];
  if (notes.length === 0) return null;
  return (
    <ul className="ensemble-notes" aria-label="What the other runs said">
      {notes.map((note, index) => (
        <li key={index}>{note}</li>
      ))}
    </ul>
  );
}

/** Where a run stands in the vote, as the tags beside its wording. */
function standing(candidate: EnsembleCandidate, ensemble: Ensemble): string[] {
  const tags = [OUTCOME_LABELS[candidate.outcome]];
  if (candidate.group !== null) tags.push(`answer ${answerLetter(candidate.group)}`);
  if (candidate.index === ensemble.chosen) tags.push("delivered");
  else if (ensemble.fused_from.includes(candidate.index)) tags.push("agreed");
  if (ensemble.judged?.set_aside.includes(candidate.index)) tags.push("set aside by the Judge");
  if (candidate.wave > 1) tags.push(`wave ${candidate.wave}`);
  return tags;
}

/** The disclosure: every wording and its run, the Judge, the rewordings not run. */
export function Candidates({ ensemble }: AgreementProps) {
  const ways = ensemble.candidates.length;
  if (ways === 0) return null;
  const { judged } = ensemble;

  return (
    <Disclosure summary={`The question, asked ${ways} ${ways === 1 ? "way" : "ways"}`}>
      <ol className="candidates">
        {ensemble.candidates.map((candidate) => (
          <li
            key={candidate.index}
            className="candidate"
            data-chosen={candidate.index === ensemble.chosen}
            data-admissible={candidate.admissible}
          >
            <p className="candidate-wording">
              <span className="candidate-index">{candidate.index}</span>
              {candidate.wording}
            </p>
            <p className="candidate-facts muted">
              {candidate.origin === "original"
                ? "As asked"
                : `Reworded${candidate.changed ? `: ${candidate.changed}` : ""}`}
              {" · "}
              {standing(candidate, ensemble).join(" · ")}
              {candidate.signature && ` · says ${candidate.signature}`}
              {candidate.attempts > 1 && ` · ${candidate.attempts} attempts`}
              {` · ${(candidate.duration_ms / 1000).toFixed(1)}s`}
            </p>
            {candidate.reasons.length > 0 && (
              <ul className="candidate-reasons">
                {candidate.reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            )}
            {candidate.sql && <pre className="sql">{candidate.sql}</pre>}
          </li>
        ))}
      </ol>

      {judged && (
        <section className="judge" aria-label="The Judge">
          <h3>The Judge{judged.model && <span className="muted"> · {judged.model}</span>}</h3>
          {judged.error ? (
            <p>It could not be asked, so the runs voted alone: {judged.error}</p>
          ) : (
            <ul>
              {judged.verdicts.map((verdict) => (
                <li key={verdict.group} data-accepted={verdict.accepted}>
                  Answer {answerLetter(verdict.group)}: {verdict.accepted ? "accepted" : "set aside"}
                  {verdict.why && ` — ${verdict.why}`}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {ensemble.discarded.length > 0 && (
        <section className="discarded" aria-label="Rewordings that were not run">
          <h3>Rewordings that were not run</h3>
          <ul>
            {ensemble.discarded.map((rewording) => (
              <li key={rewording.index}>
                {rewording.text}
                <span className="muted">
                  {rewording.changed && ` · changed: ${rewording.changed}`} · {rewording.reason}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Disclosure>
  );
}
