package org.nl2sql.desktop.api;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.Fakes;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The watcher, driven on the test's own thread.
 *
 * <p>{@code run()} is called directly rather than submitted anywhere, so the
 * whole state machine happens in order with nothing to wait for. Every test
 * below would be a sleep and a poll if the watcher started its own thread.
 */
class JobWatcherTest {

    /** Everything the watcher said, in the order it said it. */
    private static class Heard implements JobWatcher.Handlers {
        final List<Integer> progress = new ArrayList<>();
        final List<Models.JobStatus> statuses = new ArrayList<>();
        final List<String> errors = new ArrayList<>();
        final List<String> fallbacks = new ArrayList<>();
        Models.Job done;
        int doneCalls;

        @Override
        public void onProgress(Models.ProgressEvent event) {
            progress.add(event.seq());
        }

        @Override
        public void onStatus(Models.JobStatus status) {
            statuses.add(status);
        }

        @Override
        public void onDone(Models.Job job) {
            done = job;
            doneCalls++;
        }

        @Override
        public void onError(Exception error) {
            errors.add(error.getMessage());
        }

        @Override
        public void onFallback(String reason) {
            fallbacks.add(reason);
        }
    }

    private Fakes.FakeClient client;
    private Heard heard;

    @BeforeEach
    void setUp() {
        client = new Fakes.FakeClient();
        heard = new Heard();
    }

    private JobWatcher watching(Models.Job job) {
        return new JobWatcher(client, job, heard, 1, millis -> {
            // The pause is what a test would otherwise wait out. Nothing
            // here depends on time passing, only on it having passed.
        });
    }

    private static String event(String name, String data) {
        return "event: " + name + "\ndata: " + data;
    }

    private static List<String> stream(String... events) {
        List<String> lines = new ArrayList<>();
        for (String event : events) {
            lines.addAll(List.of(event.split("\n")));
            lines.add("");
        }
        return lines;
    }

    @Test
    void a_job_that_has_already_finished_is_not_streamed_at_all() {
        // A question answered inside the POST's `wait`, or one picked out of
        // the history. Opening a stream for it would work and would be one
        // more connection and one more way for it to go wrong.
        watching(Fakes.answered()).run();

        assertEquals(1, heard.doneCalls);
        assertEquals(List.of(), client.resumedFrom);
    }

    @Test
    void progress_is_delivered_then_the_finished_job() {
        client.streams.add(stream(
                event("progress", Json.write(Fakes.step(1, "screen", "screening"))),
                event("status", "{\"status\": \"running\"}"),
                event("progress", Json.write(Fakes.step(2, "generate_sql", "sql"))),
                event("done", Json.write(Fakes.answered()))));

        watching(Fakes.queued()).run();

        assertEquals(List.of(1, 2), heard.progress);
        assertEquals(List.of(Models.JobStatus.RUNNING), heard.statuses);
        assertEquals("job-1", heard.done.id());
    }

    @Test
    void an_event_the_server_resent_is_delivered_once() {
        // The server numbers events and a reconnect can resend one, so the
        // seq is the identity rather than the arrival order.
        client.streams.add(stream(
                event("progress", Json.write(Fakes.step(1, "screen", "screening"))),
                event("progress", Json.write(Fakes.step(1, "screen", "screening"))),
                event("done", Json.write(Fakes.answered()))));

        watching(Fakes.queued()).run();

        assertEquals(List.of(1), heard.progress);
    }

    @Test
    void the_progress_on_the_finished_job_is_delivered_too() {
        // A client that connected late has seen none of it, and the job
        // carries the backlog.
        Models.Job finished = Fakes.answered();
        Models.Job withProgress = new Models.Job(finished.id(), finished.status(),
                finished.question(), finished.metadata(), finished.created_at(),
                finished.started_at(), finished.finished_at(), finished.duration_ms(),
                List.of(Fakes.step(1, "screen", "screening"), Fakes.step(2, "run_sql", "rows")),
                finished.answer(), null, finished.links());
        client.streams.add(stream(event("done", Json.write(withProgress))));

        watching(Fakes.queued()).run();

        assertEquals(List.of(1, 2), heard.progress);
    }

    @Test
    void a_timeout_event_is_an_invitation_to_reconnect_rather_than_a_failure() {
        // The server closes an idle stream on purpose. Counting that as a
        // break would give up on a working stream every few minutes.
        client.streams.add(stream(
                event("progress", Json.write(Fakes.step(3, "screen", "screening"))),
                event("timeout", "{}")));
        client.streams.add(stream(event("done", Json.write(Fakes.answered()))));

        watching(Fakes.queued()).run();

        assertEquals(List.of(), heard.fallbacks);
        assertEquals(1, heard.doneCalls);
        // Resumed from the last seq it saw, so the server sends what is new
        // rather than replaying what was drawn.
        assertEquals(List.of(0, 3), client.resumedFrom);
    }

    @Test
    void one_broken_stream_is_reconnected_and_two_fall_back_to_polling() {
        client.streams.add(stream(event("progress", Json.write(Fakes.step(1, "screen", "s")))));
        client.streams.add(stream(event("progress", Json.write(Fakes.step(2, "sql", "sql")))));
        client.polled = Fakes.answered();

        watching(Fakes.queued()).run();

        assertEquals(1, heard.fallbacks.size());
        assertTrue(heard.fallbacks.get(0).contains("being polled instead"));
        assertEquals(1, client.polls);
        assertEquals(1, heard.doneCalls);
    }

    @Test
    void a_stream_that_cannot_be_opened_is_reported_and_then_polled() {
        client.failEvents = new IOException("the proxy said 502");

        watching(Fakes.queued()).run();

        assertEquals(List.of("the proxy said 502", "the proxy said 502"), heard.errors);
        assertEquals(1, heard.fallbacks.size());
        assertEquals(1, heard.doneCalls);
    }

    @Test
    void polling_keeps_going_when_a_poll_fails() {
        // A database that has not finished starting answers 503, and the
        // right response to that is to ask again rather than to give up.
        client.failEvents = new IOException("no stream");
        client.pollAnswers.add(new ApiException(503, "unavailable", "still starting"));
        client.pollAnswers.add(Fakes.answered());

        watching(Fakes.queued()).run();

        assertTrue(heard.errors.contains("still starting"));
        assertEquals(1, heard.doneCalls);
    }

    @Test
    void a_job_that_is_still_running_when_polled_is_polled_again() {
        client.failEvents = new IOException("no stream");
        client.pollAnswers.add(Fakes.job("job-1", Models.JobStatus.RUNNING, null));
        client.pollAnswers.add(Fakes.answered());

        watching(Fakes.queued()).run();

        assertEquals(List.of(Models.JobStatus.RUNNING, Models.JobStatus.SUCCEEDED), heard.statuses);
        assertEquals(1, heard.doneCalls);
    }

    @Test
    void closing_stops_it_delivering_anything_else() {
        JobWatcher[] holder = new JobWatcher[1];
        Heard closing = new Heard() {
            @Override
            public void onProgress(Models.ProgressEvent event) {
                super.onProgress(event);
                holder[0].close();
            }
        };
        client.streams.add(stream(
                event("progress", Json.write(Fakes.step(1, "screen", "s"))),
                event("progress", Json.write(Fakes.step(2, "sql", "sql"))),
                event("done", Json.write(Fakes.answered()))));
        holder[0] = new JobWatcher(client, Fakes.queued(), closing, 1, millis -> {
        });

        holder[0].run();

        assertEquals(List.of(1), closing.progress);
        assertEquals(0, closing.doneCalls);
    }

    @Test
    void closing_twice_is_allowed_and_closing_before_it_starts_is_too() {
        JobWatcher watcher = watching(Fakes.queued());

        watcher.close();
        watcher.close();
        watcher.run();

        assertEquals(0, heard.doneCalls);
        assertEquals(List.of(), heard.errors);
    }

    @Test
    void an_interrupted_pause_stops_the_watch_rather_than_spinning() {
        client.failEvents = new IOException("no stream");
        client.polled = Fakes.job("job-1", Models.JobStatus.RUNNING, null);
        JobWatcher watcher = new JobWatcher(client, Fakes.queued(), heard, 1, millis -> {
            throw new InterruptedException("shutting down");
        });

        watcher.run();

        assertEquals(0, heard.doneCalls);
        assertTrue(Thread.interrupted(), "the interrupt should be left for the caller to see");
    }

    @Test
    void an_event_this_client_has_no_use_for_is_ignored() {
        client.streams.add(stream(
                event("message", "hello"),
                event("done", Json.write(Fakes.answered()))));

        watching(Fakes.queued()).run();

        assertEquals(1, heard.doneCalls);
    }


    @Test
    void anything_after_done_is_ignored() {
        // The server has no reason to send it, and drawing progress under an
        // answer that is already on screen would be worse than dropping it.
        client.streams.add(stream(
                event("done", Json.write(Fakes.answered())),
                event("progress", Json.write(Fakes.step(9, "late", "late"))),
                event("done", Json.write(Fakes.answered()))));

        watching(Fakes.queued()).run();

        assertEquals(1, heard.doneCalls);
        assertEquals(List.of(), heard.progress);
    }

    @Test
    void polling_delivers_the_progress_the_job_carries() {
        // A client that fell back to polling has seen none of the stream.
        client.failEvents = new IOException("no stream");
        Models.Job finished = Fakes.answered();
        client.pollAnswers.add(new Models.Job(finished.id(), Models.JobStatus.RUNNING,
                finished.question(), finished.metadata(), finished.created_at(),
                finished.started_at(), null, null,
                List.of(Fakes.step(1, "screen", "screening")), null, null, finished.links()));
        client.pollAnswers.add(Fakes.answered());

        watching(Fakes.queued()).run();

        assertEquals(List.of(1), heard.progress);
        assertEquals(1, heard.doneCalls);
    }


    @Test
    void a_watch_closed_while_it_was_polling_stops_polling() {
        client.failEvents = new IOException("no stream");
        client.pollAnswers.add(Fakes.job("job-1", Models.JobStatus.RUNNING, null));
        JobWatcher[] holder = new JobWatcher[1];
        Heard closing = new Heard() {
            @Override
            public void onStatus(Models.JobStatus status) {
                super.onStatus(status);
                holder[0].close();
            }
        };
        holder[0] = new JobWatcher(client, Fakes.queued(), closing, 1, millis -> {
            throw new AssertionError("a closed watch should not wait to poll again");
        });

        holder[0].run();

        assertEquals(0, closing.doneCalls);
    }

    /**
     * A stream that does not stop when it is closed.
     *
     * <p>Closing a reader from another thread does not un-deliver what it has
     * already buffered, and it does not stop the read that is in flight from
     * finishing or from failing. Both are what these two tests are about.
     */
    private static final class Stubborn implements ApiClient.Lines {

        private final java.util.Deque<String> lines;
        private final boolean failAfterClose;
        private boolean closed;

        Stubborn(List<String> lines, boolean failAfterClose) {
            this.lines = new java.util.ArrayDeque<>(lines);
            this.failAfterClose = failAfterClose;
        }

        @Override
        public String readLine() throws IOException {
            if (closed && failAfterClose) {
                throw new IOException("stream closed");
            }
            return lines.poll();
        }

        @Override
        public void close() {
            closed = true;
        }
    }

    @Test
    void an_event_still_in_the_buffer_when_the_watch_closed_is_dropped() {
        List<String> lines = stream(
                event("progress", Json.write(Fakes.step(1, "screen", "s"))),
                event("progress", Json.write(Fakes.step(2, "sql", "sql"))),
                event("done", Json.write(Fakes.answered())));
        client.customStream = () -> new Stubborn(lines, false);
        JobWatcher[] holder = new JobWatcher[1];
        Heard closing = new Heard() {
            @Override
            public void onProgress(Models.ProgressEvent event) {
                super.onProgress(event);
                holder[0].close();
            }
        };
        holder[0] = new JobWatcher(client, Fakes.queued(), closing, 1, millis -> {
        });

        holder[0].run();

        assertEquals(List.of(1), closing.progress);
        assertEquals(0, closing.doneCalls);
    }

    @Test
    void a_read_that_failed_because_the_watch_was_closed_is_not_called_a_break() {
        // Closing the reader is how `close()` unblocks a watch parked on a
        // stream, so the failure it causes is the tidying up rather than a
        // problem to report and fall back over.
        List<String> lines = stream(event("progress", Json.write(Fakes.step(1, "screen", "s"))));
        client.customStream = () -> new Stubborn(lines, true);
        JobWatcher[] holder = new JobWatcher[1];
        Heard closing = new Heard() {
            @Override
            public void onProgress(Models.ProgressEvent event) {
                super.onProgress(event);
                holder[0].close();
            }
        };
        holder[0] = new JobWatcher(client, Fakes.queued(), closing, 1, millis -> {
        });

        holder[0].run();

        assertEquals(List.of(), closing.errors);
        assertEquals(List.of(), closing.fallbacks);
    }

    @Test
    void a_stream_that_broke_after_the_answer_arrived_is_not_reported_either() {
        // The answer is on screen. Whatever the connection did on its way
        // out is not news, and reporting it would put "the progress stream
        // keeps dropping" under a question that was answered correctly.
        List<String> lines = stream(event("done", Json.write(Fakes.answered())));
        client.customStream = () -> new ApiClient.Lines() {
            private final java.util.Deque<String> remaining = new java.util.ArrayDeque<>(lines);

            @Override
            public String readLine() throws IOException {
                String next = remaining.poll();
                if (next == null) {
                    throw new IOException("connection reset");
                }
                return next;
            }

            @Override
            public void close() {
            }
        };

        watching(Fakes.queued()).run();

        assertEquals(1, heard.doneCalls);
        assertEquals(List.of(), heard.errors);
        assertEquals(List.of(), heard.fallbacks);
    }
}
