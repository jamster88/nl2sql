package org.nl2sql.desktop.api;

import com.fasterxml.jackson.databind.JsonNode;

import java.io.IOException;
import java.util.HashSet;
import java.util.Set;

/**
 * Watching one question happen.
 *
 * <p>The API offers three ways to find out how a job is going -- poll it,
 * stream it, or let the server hold the connection -- and a graphical client
 * wants the stream, because a minute of "working..." is the difference
 * between an application that looks broken and one that looks busy.
 *
 * <p>Streams break, though, and they break in a way that is easy to miss: a
 * proxy that buffers {@code text/event-stream} (several do, by default)
 * produces a progress bar that never moves and no error anywhere. So this
 * watches the stream watching the job, and when the stream has failed enough
 * times to be called broken it falls back to polling and says so. Polling is
 * slower and duller and always works; that is the right thing to degrade to.
 *
 * <p>It is a {@link Runnable} rather than something that starts its own
 * thread. The application submits it to an executor; a test calls
 * {@code run()} directly and gets the whole state machine, in order, on its
 * own thread, with no waiting and nothing to flake.
 */
public final class JobWatcher implements Runnable, AutoCloseable {

    /** What the watcher tells whoever is drawing. */
    public interface Handlers {

        /** One pipeline node finished. Called once per {@code seq}, in order. */
        void onProgress(Models.ProgressEvent event);

        void onStatus(Models.JobStatus status);

        /** The job reached a terminal state. Called exactly once. */
        void onDone(Models.Job job);

        /** Something went wrong that the watcher is still recovering from. */
        void onError(Exception error);

        /** The stream was abandoned and polling took over. */
        void onFallback(String reason);
    }

    /** A pause that a test can make instant. */
    @FunctionalInterface
    public interface Pause {
        void sleep(long millis) throws InterruptedException;
    }

    /**
     * How many stream failures before falling back.
     *
     * <p>Two rather than one: a single reconnect is normal -- the server
     * closes an idle stream on purpose -- and giving up on the first would
     * abandon a working stream every {@code API_EVENT_STREAM_TIMEOUT_SECONDS}.
     */
    public static final int MAX_STREAM_FAILURES = 2;

    private final ApiClient client;
    private final Models.Job job;
    private final Handlers handlers;
    private final long pollIntervalMs;
    private final Pause pause;

    private final Set<Integer> delivered = new HashSet<>();
    private volatile boolean closed;
    private volatile ApiClient.Lines open;
    private boolean finished;
    private int lastSeq;
    private int failures;
    /** Set when the server said it was closing an idle stream on purpose. */
    private boolean invited;

    public JobWatcher(ApiClient client, Models.Job job, Handlers handlers, long pollIntervalMs) {
        this(client, job, handlers, pollIntervalMs, Thread::sleep);
    }

    public JobWatcher(ApiClient client, Models.Job job, Handlers handlers, long pollIntervalMs,
                      Pause pause) {
        this.client = client;
        this.job = job;
        this.handlers = handlers;
        this.pollIntervalMs = pollIntervalMs;
        this.pause = pause;
    }

    @Override
    public void run() {
        // The job may already be finished -- a question answered inside the
        // POST's `wait`, or one picked out of the history. Opening a stream
        // for it would work (the server replays the backlog and says done),
        // but going straight to the answer is one fewer connection and one
        // fewer way for it to go wrong.
        if (job.status().terminal()) {
            finish(job);
            return;
        }
        while (!closed && !finished) {
            if (failures >= MAX_STREAM_FAILURES) {
                poll();
            } else {
                stream();
            }
        }
    }

    /** Stop watching. Safe to call more than once, and after the job has finished. */
    @Override
    public void close() {
        closed = true;
        ApiClient.Lines lines = open;
        if (lines != null) {
            // Closing the reader is what unblocks `run()` when it is parked
            // on a stream the server has nothing to say on.
            lines.close();
        }
    }

    // --- streaming ---------------------------------------------------------

    private void stream() {
        invited = false;
        try (ApiClient.Lines lines = client.events(job, lastSeq)) {
            open = lines;
            EventStream.read(lines, this::handle);
            // The server closed the stream without saying `done`. If it said
            // `timeout` first that was an invitation to come back, and the
            // loop does exactly that; if it said nothing, the stream broke.
            if (!closed && !finished && !invited) {
                note("the progress stream closed before the answer arrived");
            }
        } catch (IOException | ApiException cause) {
            if (!closed && !finished) {
                handlers.onError(cause);
                note(cause.getMessage());
            }
        } finally {
            open = null;
        }
    }

    private void note(String reason) {
        failures++;
        if (failures >= MAX_STREAM_FAILURES) {
            handlers.onFallback(
                    "the progress stream keeps dropping (" + reason
                            + "), so this question is being polled instead");
        }
    }

    private void handle(EventStream.Event event) {
        if (closed || finished) {
            return;
        }
        switch (event.name()) {
            case "progress" -> {
                // A stream that is delivering is a stream that is working,
                // whatever it did before.
                failures = 0;
                emit(Json.read(event.data(), Models.ProgressEvent.class));
            }
            case "status" -> {
                JsonNode payload = Json.tree(event.data());
                handlers.onStatus(Models.JobStatus.of(payload.path("status").asText()));
            }
            case "done" -> finish(Json.read(event.data(), Models.Job.class));
            case "timeout" -> {
                // The server said it was closing an idle stream and to come
                // back. That is not a failure, so the count is left alone --
                // and `stream()` must not call it one either, which is what
                // this flag is for.
                invited = true;
            }
            default -> {
                // `message`, or something a later server added. The contract
                // names four kinds and this client draws three of them.
            }
        }
    }

    private void emit(Models.ProgressEvent event) {
        // The server numbers events and a reconnect can resend one, so the
        // seq is the identity rather than the arrival order.
        if (!delivered.add(event.seq())) {
            return;
        }
        lastSeq = Math.max(lastSeq, event.seq());
        handlers.onProgress(event);
    }

    /**
     * The one place that ends a watch.
     *
     * <p>No guard of its own: both callers have already checked
     * {@code finished} -- {@link #handle} before it reads an event and
     * {@link #poll} before it asks again -- and a second guard here would be
     * a branch nothing can reach and nobody could be sure of.
     */
    private void finish(Models.Job answered) {
        finished = true;
        for (Models.ProgressEvent event : answered.progress()) {
            emit(event);
        }
        handlers.onDone(answered);
    }

    // --- polling, which always works ---------------------------------------

    private void poll() {
        try {
            Models.Job latest = client.job(job.id());
            for (Models.ProgressEvent event : latest.progress()) {
                emit(event);
            }
            handlers.onStatus(latest.status());
            if (latest.status().terminal()) {
                finish(latest);
                return;
            }
        } catch (ApiException cause) {
            handlers.onError(cause);
        }
        waitToPollAgain();
    }

    private void waitToPollAgain() {
        // Only `closed`: a poll that finished returned before calling this.
        if (closed) {
            return;
        }
        try {
            pause.sleep(pollIntervalMs);
        } catch (InterruptedException cause) {
            Thread.currentThread().interrupt();
            closed = true;
        }
    }
}
