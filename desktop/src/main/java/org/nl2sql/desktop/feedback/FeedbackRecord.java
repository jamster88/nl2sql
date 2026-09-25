package org.nl2sql.desktop.feedback;

import org.nl2sql.desktop.api.Models;

/**
 * One verdict, as this application remembers it.
 *
 * <p>It carries the question as well as the job id on purpose. A job is
 * forgotten after {@code API_JOB_TTL_SECONDS}, so a verdict that only held an
 * id would be an orphan pointing at nothing an hour later -- and this is the
 * user's own record of what they thought, which should outlive the server's
 * memory of what it said.
 *
 * <p>{@code at} is an ISO-8601 string rather than an {@code Instant} so the
 * record survives JSON without a revival step, and so the file it is written
 * to can be read by a person.
 */
public record FeedbackRecord(String jobId, String question, Models.Verdict verdict, String at,
                             SyncState sync, String error) {

    public FeedbackRecord {
        sync = sync == null ? SyncState.LOCAL : sync;
        error = error == null ? "" : error;
    }

    /** The same record, moved to another state. */
    public FeedbackRecord in(SyncState state, String reason) {
        return new FeedbackRecord(jobId, question, verdict, at, state, reason);
    }
}
