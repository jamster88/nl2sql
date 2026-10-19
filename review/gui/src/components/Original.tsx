/**
 * What was submitted, exactly as it was submitted.
 *
 * Read-only, and visibly so. This half of the screen is evidence: the
 * question a user asked, the SQL the agent wrote, what the user thought of
 * it and anything they added. The editable half sits beside it and is
 * plainly a *derivative* of this, which is the distinction that keeps a
 * curator honest -- it is very easy to fix a question until it matches the
 * SQL, and a golden pair built that way tests nothing.
 *
 * Since 7.0 the evidence includes the other runs: the agent asks a question
 * several ways, and the SQL above is the one it delivered. What the others
 * wrote, and which of them agreed, is in the candidates pane beneath it.
 *
 * Since 8.0 a question asked in a conversation is staged as the question
 * answered: a follow-up such as "and by state?" is recorded as the complete
 * question it was read as, because that is what a golden pair is built from
 * and a pair must stand alone. What the person typed is shown under it,
 * "Asked as", with the turn it was and how many turns it was read with.
 */

import { Candidates } from "./Candidates";
import { plainText } from "../api/text";
import type { ConversationRecord, SubmissionModel, Verdict } from "../api/types";

export interface OriginalProps {
  submission: SubmissionModel;
}

const MARKED: Record<Verdict, string> = {
  yes: "Marked correct",
  no: "Marked wrong",
  incomplete: "Marked correct but incomplete",
};

export function Original({ submission }: OriginalProps) {
  return (
    <section className="original" aria-label="What was submitted">
      <header className="original-head">
        <span className={`verdict verdict-${submission.verdict}`}>
          {MARKED[submission.verdict]}
        </span>
        <span className="muted">
          {submission.submitted_at
            ? new Date(submission.submitted_at).toLocaleString()
            : "no timestamp"}
          {submission.agent_version
            ? ` · agent ${submission.agent_version}`
            : ""}
        </span>
      </header>

      <h3 className="original-question">{submission.question}</h3>
      <AskedAs conversation={submission.conversation} question={submission.question} />

      {submission.comment && (
        <blockquote className="original-comment">
          <span className="muted">They said:</span> {submission.comment}
        </blockquote>
      )}

      {submission.narrative && (
        <p className="original-narrative">{plainText(submission.narrative)}</p>
      )}

      {submission.sql_code ? (
        <pre className="code">
          <code>{submission.sql_code}</code>
        </pre>
      ) : (
        <p className="muted">
          No SQL was captured. The run refused the question or failed before
          writing any, so there is nothing here to build a pair from.
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
            {submission.columns.length > 0
              ? ` (${submission.columns.join(", ")})`
              : ""}
          </dd>
        </div>
        <div>
          <dt>Job</dt>
          <dd className="mono">{submission.job_id}</dd>
        </div>
      </dl>

      <Candidates ensemble={submission.ensemble} />

      {submission.review_note && (
        <p className="original-note">
          <span className="muted">Review note:</span> {submission.review_note}
          {submission.reviewer ? ` — ${submission.reviewer}` : ""}
        </p>
      )}
    </section>
  );
}

/**
 * The words typed, when they are not the question above: a follow-up the
 * agent restated. Nothing for a question asked alone, whose record is `{}`,
 * or for one asked in a conversation that stood alone already.
 */
function AskedAs({ conversation, question }: { conversation: ConversationRecord; question: string }) {
  const asked = conversation.asked ?? "";
  if (!asked || asked === question) return null;
  return (
    <p className="original-asked">
      <span className="muted">Asked as:</span> <q>{asked}</q>
      {conversation.turn ? (
        <span className="muted">
          {" "}
          · turn {conversation.turn}, {conversation.remembered ?? 0} remembered
        </span>
      ) : null}
    </p>
  );
}
