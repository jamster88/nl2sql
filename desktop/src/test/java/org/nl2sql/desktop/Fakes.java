package org.nl2sql.desktop;

import org.nl2sql.desktop.api.ApiClient;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Models;

import java.io.IOException;
import java.net.URI;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

/**
 * The sample answers and the stand-in server every test here is written
 * against.
 *
 * <p>One place rather than a builder in each test file: the shapes come from
 * the API's own documentation, and a second hand-written {@code Job} is a
 * second opportunity to get the contract subtly wrong in a way that makes a
 * test pass.
 */
public final class Fakes {

    private Fakes() {
    }

    public static Models.ResultTable table() {
        return new Models.ResultTable(
                List.of("department", "net_sales"),
                List.of(List.of("Produce", "719279.97"), List.of("Bakery", 412000.5)),
                2, false);
    }

    public static Models.Answer answer() {
        return new Models.Answer(
                "Produce net sales were $719,279.97 in FY2025.\n\n| a |\n| - |",
                "Produce net sales were $719,279.97 in FY2025.",
                "SELECT department, sum(net_sales)\nFROM fact_pos_retail_sales\nGROUP BY 1",
                "proceed", "aggregate", null,
                List.of("dim_product", "fact_pos_retail_sales"),
                List.of(new Models.LiteralMatch("produse", "dim_product", "department",
                        "Produce", 0.81)),
                table(),
                new Models.ChartSpec("bar", "department", List.of("net_sales"), null),
                List.of(new Models.Claim("Produce net sales were $719,279.97.", 719279.97,
                        List.of(List.of(0, "net_sales")), null)),
                new Models.AuditReport(true, List.of(), List.of(), List.of(), null),
                125767.4, 1,
                List.of(new Models.TraceEntry("generate_sql", 8123.4, 1, "3 tables, 2 examples"),
                        new Models.TraceEntry("run_sql", 210.0, 0, "")),
                Map.of());
    }

    public static Models.Job job(String id, Models.JobStatus status, Models.Answer answer) {
        return new Models.Job(id, status, "What was total net sales for Produce in FY2025?",
                Map.of(), "2026-09-25T10:00:00Z", "2026-09-25T10:00:01Z",
                status.terminal() ? "2026-09-25T10:01:02Z" : null,
                status.terminal() ? 61432.5 : null,
                List.of(), answer, null,
                new Models.JobLinks("/v1/questions/" + id, "/v1/questions/" + id + "/events"));
    }

    /** A finished, successful job with the sample answer on it. */
    public static Models.Job answered() {
        return job("job-1", Models.JobStatus.SUCCEEDED, answer());
    }

    public static Models.Job queued() {
        return job("job-1", Models.JobStatus.QUEUED, null);
    }

    public static Models.ProgressEvent step(int seq, String node, String label) {
        return step(seq, node, label, "");
    }

    public static Models.ProgressEvent step(int seq, String node, String label, String detail) {
        return new Models.ProgressEvent(seq, node, label, detail, "2026-09-25T10:00:0" + seq + "Z");
    }

    public static Models.Meta meta(boolean feedback) {
        return new Models.Meta("nl2sql-agent", "4.4.0", "qwen2.5-coder:32b",
                List.of("aggregate"), List.of("dim_product", "fact_pos_retail_sales"),
                "Retail point-of-sale data.",
                new Models.Limits(500, 3, 2_000_000, 30_000, 2, 900, 2000, 20),
                new Models.Pipeline(true, true, true, true, "hybrid",
                        List.of("screen", "generate_sql", "run_sql")),
                Map.of("enabled", true), "bearer", feedback);
    }

    public static Models.Readiness ready() {
        return new Models.Readiness(true, Map.of("database", new Models.Check(true, "")), List.of());
    }

    /**
     * A server that answers from a script.
     *
     * <p>Everything it was asked is recorded, and everything it should answer
     * is set beforehand, so a test reads as "given this, when that, then
     * this" rather than as a sequence of callbacks.
     */
    public static class FakeClient implements ApiClient {

        // Qualified: the fields below are also the names of this class's own
        // methods, and a method name in scope shadows the outer one whatever
        // its arity.
        public Models.Meta meta = Fakes.meta(true);
        public Models.Readiness readiness = Fakes.ready();
        public Models.Job accepted = Fakes.queued();
        public Models.Job polled = Fakes.answered();
        public ApiException failAsk;
        public ApiException failMeta;
        public ApiException failReadiness;
        public ApiException failFeedback;
        public ApiException failCancel;
        public IOException failEvents;

        public final List<String> asked = new ArrayList<>();
        public final List<String> cancelled = new ArrayList<>();
        public final List<String> withdrawn = new ArrayList<>();
        public final List<String> votes = new ArrayList<>();
        public final List<Integer> resumedFrom = new ArrayList<>();
        /** Each entry is one connection's worth of event-stream lines. */
        public final Deque<List<String>> streams = new ArrayDeque<>();
        /** A stream of the test's own making, for the awkward close cases. */
        public java.util.function.Supplier<Lines> customStream;
        /**
         * What successive polls answer: a {@link Models.Job} to return or an
         * {@link ApiException} to throw. When it runs out, {@link #polled}
         * is returned for ever after.
         */
        public final Deque<Object> pollAnswers = new ArrayDeque<>();
        public int polls;

        @Override
        public Models.Meta meta() {
            if (failMeta != null) {
                throw failMeta;
            }
            return meta;
        }

        @Override
        public Models.Readiness readiness() {
            if (failReadiness != null) {
                throw failReadiness;
            }
            return readiness;
        }

        @Override
        public Models.Job ask(String question, int waitSeconds) {
            asked.add(question);
            if (failAsk != null) {
                throw failAsk;
            }
            return accepted;
        }

        @Override
        public Models.Job job(String id) {
            polls++;
            Object next = pollAnswers.poll();
            if (next instanceof ApiException failure) {
                throw failure;
            }
            return next instanceof Models.Job job ? job : polled;
        }

        @Override
        public Models.JobList jobs(int limit) {
            return new Models.JobList(List.of(polled), 1);
        }

        @Override
        public void cancel(String id) {
            cancelled.add(id);
            if (failCancel != null) {
                throw failCancel;
            }
        }

        @Override
        public Models.FeedbackModel submitFeedback(String id, Models.Verdict verdict, String comment) {
            votes.add(id + ":" + verdict.wire() + ":" + comment);
            if (failFeedback != null) {
                throw failFeedback;
            }
            return new Models.FeedbackModel("f-1", id, verdict, comment, "pending");
        }

        @Override
        public void withdrawFeedback(String id) {
            withdrawn.add(id);
            if (failFeedback != null) {
                throw failFeedback;
            }
        }

        @Override
        public URI eventsUrl(Models.Job job, int fromSeq) {
            return URI.create("https://example.invalid" + job.links().events() + "?from_seq=" + fromSeq);
        }

        @Override
        public Lines events(Models.Job job, int fromSeq) throws IOException {
            resumedFrom.add(fromSeq);
            if (failEvents != null) {
                throw failEvents;
            }
            if (customStream != null) {
                return customStream.get();
            }
            List<String> next = streams.poll();
            if (next == null) {
                throw new IOException("no stream left in this fake");
            }
            return new ListLines(new ArrayDeque<>(next));
        }
    }

    /** Lines from a list, closed like a reader. */
    public static final class ListLines implements ApiClient.Lines {

        private final Deque<String> lines;
        public boolean closed;

        public ListLines(Deque<String> lines) {
            this.lines = lines;
        }

        @Override
        public String readLine() {
            return closed ? null : lines.poll();
        }

        @Override
        public void close() {
            closed = true;
        }
    }
}
