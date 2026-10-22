package org.nl2sql.desktop;

import org.nl2sql.desktop.api.ApiClient;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.api.SignIn;

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
                List.of(new Models.TraceEntry("generate_sql", 8123.4, 1, "3 tables, 2 examples", "", "", "", List.of()),
                        new Models.TraceEntry("run_sql", 210.0, 0, "", "", "", "", List.of())),
                Map.of(), Map.of(), null, "v7_0_0", null);
    }

    public static Models.Job job(String id, Models.JobStatus status, Models.Answer answer) {
        return new Models.Job(id, status, "What was total net sales for Produce in FY2025?",
                Map.of(), "2026-09-25T10:00:00Z", "2026-09-25T10:00:01Z",
                status.terminal() ? "2026-09-25T10:01:02Z" : null,
                status.terminal() ? 61432.5 : null,
                List.of(), answer, null,
                new Models.JobLinks("/v1/questions/" + id, "/v1/questions/" + id + "/events"), null);
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
        return new Models.ProgressEvent(seq, node, label, detail, "2026-09-25T10:00:0" + seq + "Z", null);
    }

    /** A step of the ensemble's own (7.0): no run reported it. */
    public static Models.ProgressEvent own(int seq, String node) {
        return own(seq, node, "");
    }

    public static Models.ProgressEvent own(int seq, String node, String detail) {
        return new Models.ProgressEvent(seq, node, node, detail, "", null);
    }

    /** A step of one wording's run: 0 the question as asked, 1 and up a rewording. */
    public static Models.ProgressEvent run(int seq, int candidate, String node) {
        return run(seq, candidate, node, "");
    }

    public static Models.ProgressEvent run(int seq, int candidate, String node, String detail) {
        return new Models.ProgressEvent(seq, node, node, detail, "", candidate);
    }

    /** The ensemble's eleven nodes, as `/v1/meta` names them by themselves. */
    public static final List<String> OUTER = List.of("screen", "refuse", "paraphrase",
            "screen_paraphrase", "plan_wave", "answer", "validate", "judge", "vote", "fuse",
            "deliver");

    /** The steps of one run, as short as a fixture can make them. */
    public static final List<String> INNER = List.of("supervise", "generate_sql", "finish");

    /** How a 7.0 server says it asks a question several ways. */
    public static Models.EnsembleSettings ensembleSettings(boolean enabled, int paraphrases) {
        return new Models.EnsembleSettings(enabled, paraphrases, 10, 2, 1, true, true,
                enabled ? OUTER : List.of());
    }

    /** Every node a server with the ensemble on lists: its own, then each run's. */
    public static List<String> ensembleNodes() {
        List<String> nodes = new java.util.ArrayList<>(OUTER);
        nodes.addAll(INNER);
        return nodes;
    }

    /** One wording's run that answered. */
    public static Models.EnsembleCandidate candidate(int index, String wording, String changed,
                                                     int group, String signature) {
        return new Models.EnsembleCandidate(index, wording, index == 0 ? "original" : "paraphrase",
                1, changed, "answered", true, List.of(), "SELECT " + index, signature, 1, group,
                31250, List.of());
    }

    /**
     * A question asked four ways: three runs agree, one answers differently,
     * and a fifth rewording was never run.
     */
    public static Models.Ensemble ensemble() {
        return new Models.Ensemble(
                new Models.EnsembleAgreement(4, 3, 4, "majority", "3 of 4 agree", 0),
                "3 of 4 runs agreed; 1 answered differently.", 0, List.of(0, 1, 3), true,
                List.of(), List.of(), 0, 0,
                List.of(new Models.EnsembleDissent(1, List.of(2), "Dairy 512004.10",
                        "filters on banner_name")),
                new Models.EnsembleJudgement(
                        List.of(new Models.EnsembleVerdict(0, true, ""),
                                new Models.EnsembleVerdict(1, true, "")),
                        List.of(), false, null, "judge-model", ""),
                List.of(candidate(0, "What was total net sales for Produce in FY2025?", "", 0,
                                "Produce 719279.97"),
                        candidate(1, "In FY2025, what did Produce sell, net?",
                                "the order of the clauses", 0, "Produce 719279.97"),
                        candidate(2, "Produce net sales, fiscal 2025?", "shortened", 1,
                                "Dairy 512004.10"),
                        candidate(3, "How much did Produce take, net, in FY2025?", "", 0,
                                "Produce 719279.97")),
                List.of(new Models.DiscardedRewording(4, "What did Dairy sell?",
                        "the department", "F2 literals")),
                1);
    }

    /** The sample answer, given by the ensemble above. */
    public static Models.Answer answer(Models.Ensemble ensemble) {
        Models.Answer base = answer();
        return new Models.Answer(base.answer(), base.narrative(), base.sql(), base.verdict(),
                base.intent(), base.clarification(), base.tables(), base.literals(),
                base.result(), base.chart(), base.claims(), base.audit(), base.plan_cost(),
                base.attempts(), base.trace(), base.retrieval_errors(), base.node_errors(),
                ensemble, "", null);
    }

    /**
     * What the server actually says about itself, at the length it says it.
     *
     * <p>The scope sentence is the real one, all three hundred and ninety
     * characters of it. A tidier fixture is why three rounds of layout
     * measurement reported a window that fits: a twenty-six character scope
     * has no opinion about how wide the window has to be, and the real one
     * demanded two thousand pixels.
     */
    public static final String SCOPE = "this database holds retail data in these tables: "
            + "dim_ad_channel, dim_ad_placement, dim_allowance_type, dim_competitor, dim_date, "
            + "dim_geography, dim_product, dim_promo_calendar, dim_promotion, dim_store, "
            + "dim_vendor, fact_ad_performance, fact_competitor_pricing, fact_item_cogs, "
            + "fact_item_prices, fact_pos_retail_sales, fact_promotion_lift, "
            + "fact_vendor_allowances";

    /** The two `/readyz` warns about on a development deployment, verbatim. */
    public static final List<String> WARNINGS = List.of(
            "No API_TOKEN is set, so every caller that can reach the port can ask questions.",
            "API_FEEDBACK_DB_URL is set while no API_TOKEN is, so anyone who can reach the port "
                    + "can write rows into the feedback staging database.");

    /** The nineteen tables the retail database actually holds. */
    public static final List<String> TABLES = List.of("dim_ad_channel", "dim_ad_placement",
            "dim_allowance_type", "dim_competitor", "dim_date", "dim_geography", "dim_product",
            "dim_promo_calendar", "dim_promotion", "dim_store", "dim_vendor",
            "fact_ad_performance", "fact_competitor_pricing", "fact_item_cogs",
            "fact_item_prices", "fact_pos_retail_sales", "fact_promotion_lift",
            "fact_vendor_allowances", "fact_store_traffic");

    public static Models.Meta meta(boolean feedback) {
        return new Models.Meta("nl2sql-agent", "8.0.0", "qwen3.8-256k",
                List.of("aggregate"), TABLES, SCOPE,
                new Models.Limits(500, 3, 2_000_000, 30_000, 2, 900, 2000, 20, 64),
                new Models.Pipeline(true, true, true, true, "hybrid",
                        List.of("screen", "generate_sql", "run_sql"), null, "", null),
                Map.of("enabled", true), "bearer", feedback, Map.of());
    }

    /** The same server, naming its pipeline and remembering {@code turns} turns of a conversation (8.0). */
    public static Models.Meta remembering(int turns, String version) {
        Models.Meta meta = meta(true);
        Models.Pipeline pipeline = meta.pipeline();
        return new Models.Meta(meta.service(), meta.version(), meta.model(), meta.intents(),
                meta.tables(), meta.scope(), meta.limits(),
                new Models.Pipeline(pipeline.supervisor(), pipeline.literals(), pipeline.narrate(),
                        pipeline.audit(), pipeline.schema_retrieval(), pipeline.nodes(), null, version,
                        new Models.MemorySettings(turns, 3, 200, 3600)),
                meta.tls(), meta.authentication(), meta.feedback(), meta.routing());
    }

    /** The sample answer, given as a step of a conversation (8.0). */
    public static Models.Answer answer(Models.Conversation conversation) {
        Models.Answer base = answer();
        return new Models.Answer(base.answer(), base.narrative(), base.sql(), base.verdict(),
                base.intent(), base.clarification(), base.tables(), base.literals(),
                base.result(), base.chart(), base.claims(), base.audit(), base.plan_cost(),
                base.attempts(), base.trace(), base.retrieval_errors(), base.node_errors(),
                null, "v7_0_0", conversation);
    }

    /** The same server, saying how it wants to be told who is asking. */
    public static Models.Meta meta(boolean feedback, String authentication) {
        Models.Meta meta = meta(feedback);
        return new Models.Meta(meta.service(), meta.version(), meta.model(), meta.intents(),
                meta.tables(), meta.scope(), meta.limits(), meta.pipeline(), meta.tls(),
                authentication, meta.feedback(), meta.routing());
    }

    /** What the auth service hands a client that signed in. */
    public static Models.Token token(String user, String name) {
        return new Models.Token(user, name, List.of("nl2sql_users"), "session", 1_790_000_000L,
                "eyJ.session." + user);
    }

    public static Models.Readiness ready() {
        return new Models.Readiness(true, Map.of("database", new Models.Check(true, "")),
                WARNINGS);
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
        /** The conversation each question was asked in, null for one asked alone. */
        public final List<String> conversations = new ArrayList<>();
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
        public Models.Job ask(String question, int waitSeconds, String conversation) {
            asked.add(question);
            conversations.add(conversation);
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

    /**
     * An auth service that answers from a script: the session to hand back,
     * or the refusal to throw, and what happens on the server once it has
     * worked -- usually that the API stops refusing.
     */
    public static class FakeSignIn implements SignIn {

        public Models.Token answer = token("ada", "Ada Lovelace");
        public ApiException fail;
        public Runnable then = () -> {
        };
        public final List<String> attempts = new ArrayList<>();
        public int signOuts;

        @Override
        public Models.Token signIn(String username, String password) {
            attempts.add(username + ":" + password);
            if (fail != null) {
                throw fail;
            }
            then.run();
            return answer;
        }

        @Override
        public void signOut() {
            signOuts++;
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
