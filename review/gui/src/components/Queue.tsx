/**
 * The review queue.
 *
 * Filtered by state, because the four states are four different jobs:
 * `pending` is the inbox, `accepted` is the shortlist waiting to be turned
 * into pairs, `rejected` is the record of what was looked at and passed
 * over, and `promoted` is history. Showing them mixed would make the first
 * two indistinguishable at a glance, which is the only glance a queue gets.
 */

import type { State, SubmissionModel } from "../api/types";

export interface QueueProps {
  submissions: SubmissionModel[];
  counts: Record<string, number>;
  states: string[];
  state: State | "all";
  currentId: string | undefined;
  onState: (state: State | "all") => void;
  onSelect: (submission: SubmissionModel) => void;
  busy?: boolean;
}

export function Queue({
  submissions,
  counts,
  states,
  state,
  currentId,
  onState,
  onSelect,
  busy = false,
}: QueueProps) {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);

  return (
    <nav className="queue" aria-label="Review queue">
      <div className="queue-filters" role="group" aria-label="Filter by state">
        <button
          type="button"
          className="chip"
          aria-pressed={state === "all"}
          onClick={() => onState("all")}
        >
          all <span className="chip-count">{total}</span>
        </button>
        {states.map((name) => (
          <button
            key={name}
            type="button"
            className="chip"
            aria-pressed={state === name}
            onClick={() => onState(name as State)}
          >
            {name} <span className="chip-count">{counts[name] ?? 0}</span>
          </button>
        ))}
      </div>

      {busy && <p className="muted">Loading…</p>}

      {!busy && submissions.length === 0 && (
        <p className="muted queue-empty">
          Nothing here. Feedback arrives when someone votes on an answer in the web GUI.
        </p>
      )}

      <ul className="queue-list">
        {submissions.map((submission) => (
          <li key={submission.id}>
            <button
              type="button"
              className="queue-item"
              aria-current={submission.id === currentId}
              onClick={() => onSelect(submission)}
            >
              <span className={`verdict verdict-${submission.verdict}`}>
                {submission.verdict === "yes" ? "Yes" : "No"}
              </span>
              <span className="queue-question">{submission.question}</span>
              <span className="queue-meta muted">
                {submission.state}
                {submission.promoted_pair_id ? ` · ${submission.promoted_pair_id}` : ""}
                {submission.comment ? " · commented" : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
