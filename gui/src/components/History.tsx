/**
 * What has been asked this session.
 *
 * Session-scoped rather than a full log: the server forgets a job after
 * `API_JOB_TTL_SECONDS`, so a list that outlived the tab would fill up with
 * entries that 404 when clicked. What does outlive the tab is the verdict,
 * which is why it is shown here -- it is the one place a user sees that
 * their feedback was recorded against something.
 */

import { useFeedbackRecords } from "../feedback/useFeedback";
import type { FeedbackStore } from "../feedback/store";
import type { Job } from "../api/types";

export interface HistoryProps {
  jobs: readonly Job[];
  currentId: string | undefined;
  onSelect: (job: Job) => void;
  store: FeedbackStore;
}

export function History({ jobs, currentId, onSelect, store }: HistoryProps) {
  const records = useFeedbackRecords(store);
  if (jobs.length === 0) return null;

  return (
    <nav className="history" aria-label="Earlier questions">
      <h2 className="history-title">This session</h2>
      <ul>
        {jobs.map((job) => {
          const verdict = records.find((record) => record.jobId === job.id)?.verdict;
          return (
            <li key={job.id}>
              <button
                type="button"
                className="history-item"
                aria-current={job.id === currentId}
                onClick={() => onSelect(job)}
              >
                <span className="history-question">{job.question}</span>
                <span className="history-meta">
                  {verdict && (
                    <span className="history-verdict" data-verdict={verdict}>
                      {verdict === "yes" ? "✓" : "✗"}
                    </span>
                  )}
                  {job.status !== "succeeded" && (
                    <span className="history-status">{job.status}</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
