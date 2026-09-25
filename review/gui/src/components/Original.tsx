/**
 * What was submitted, exactly as it was submitted.
 *
 * Read-only, and visibly so. This half of the screen is evidence: the
 * question a user asked, the SQL the agent wrote, what the user thought of
 * it and anything they added. The editable half sits beside it and is
 * plainly a *derivative* of this, which is the distinction that keeps a
 * curator honest -- it is very easy to fix a question until it matches the
 * SQL, and a golden pair built that way tests nothing.
 */

import type { SubmissionModel } from "../api/types";

export interface OriginalProps {
  submission: SubmissionModel;
}

export function Original({ submission }: OriginalProps) {
  return (
    <section className="original" aria-label="What was submitted">
      <header className="original-head">
        <span className={`verdict verdict-${submission.verdict}`}>
          {submission.verdict === "yes" ? "Marked correct" : "Marked wrong"}
        </span>
        <span className="muted">
          {submission.submitted_at
            ? new Date(submission.submitted_at).toLocaleString()
            : "no timestamp"}
          {submission.agent_version ? ` · agent ${submission.agent_version}` : ""}
        </span>
      </header>

      <h3 className="original-question">{submission.question}</h3>

      {submission.comment && (
        <blockquote className="original-comment">
          <span className="muted">They said:</span> {submission.comment}
        </blockquote>
      )}

      {submission.narrative && <p className="original-narrative">{submission.narrative}</p>}

      {submission.sql_code ? (
        <pre className="code">
          <code>{submission.sql_code}</code>
        </pre>
      ) : (
        <p className="muted">
          No SQL was captured. The run refused the question or failed before writing any, so
          there is nothing here to build a pair from.
        </p>
      )}

      <dl className="original-facts">
        <div>
          <dt>Tables</dt>
          <dd>{submission.tables || "—"}</dd>
        </div>
        <div>
          <dt>Intent</dt>
          <dd>{submission.intent || "—"}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>
            {submission.row_count}
            {submission.columns.length > 0 ? ` (${submission.columns.join(", ")})` : ""}
          </dd>
        </div>
        <div>
          <dt>Job</dt>
          <dd className="mono">{submission.job_id}</dd>
        </div>
      </dl>

      {submission.review_note && (
        <p className="original-note">
          <span className="muted">Review note:</span> {submission.review_note}
          {submission.reviewer ? ` — ${submission.reviewer}` : ""}
        </p>
      )}
    </section>
  );
}
