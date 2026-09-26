package org.nl2sql.desktop.api;

import java.io.IOException;
import java.net.URI;

/**
 * What this application needs from the server, and nothing else.
 *
 * <p>An interface rather than a class so the whole of the interface above it
 * can be driven against a fake with no server, no container and no network --
 * which is the only way those paths get exercised on every run. It is also the
 * shortest honest description of the API's surface: eight calls.
 */
public interface ApiClient {

    Models.Meta meta();

    Models.Readiness readiness();

    /**
     * Ask a question.
     *
     * <p>Returns as soon as the server has accepted it, or -- when
     * {@code waitSeconds} is positive -- once the answer is ready or the wait
     * runs out, whichever comes first. Both return the same document, so
     * waiting is an optimisation rather than a second way of asking.
     */
    Models.Job ask(String question, int waitSeconds);

    Models.Job job(String id);

    Models.JobList jobs(int limit);

    void cancel(String id);

    /**
     * Record a verdict on an answer.
     *
     * <p>The job id is the whole address: what the answer <em>was</em> is read
     * from the job by the server, so this call carries an opinion and nothing
     * else.
     */
    Models.FeedbackModel submitFeedback(String id, Models.Verdict verdict, String comment);

    /** Take a verdict back, for the misclick. */
    void withdrawFeedback(String id);

    /** Where this job's progress stream lives, resuming after {@code fromSeq}. */
    URI eventsUrl(Models.Job job, int fromSeq);

    /** Open that stream. The caller closes it. */
    Lines events(Models.Job job, int fromSeq) throws IOException;

    /**
     * A stream of lines that is still arriving.
     *
     * <p>Not a {@code Stream<String>}: the point of this one is that it
     * blocks between lines, for a minute at a time, and is closed from
     * another thread when the user gives up. That is a reader, and pretending
     * otherwise would only mean unwrapping it again at the one place that has
     * to close it.
     */
    interface Lines extends AutoCloseable {

        /** The next line, or null once the server has finished. */
        String readLine() throws IOException;

        @Override
        void close();
    }
}
