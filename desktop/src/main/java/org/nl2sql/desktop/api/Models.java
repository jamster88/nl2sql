package org.nl2sql.desktop.api;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The wire contract, in Java.
 *
 * <p>These are the shapes in {@code agent/nl2sql_agent/api/models.py}, and
 * they are one file for the same reason {@code gui/src/api/types.ts} is one
 * file: it is the document somebody writing a third client opens first, and a
 * contract spread over twenty-three files is a contract nobody reads end to
 * end. {@code tests/java/test_desktop_contract.py} compares every record
 * component here against the pydantic field it mirrors and fails when either
 * side gains something the other did not.
 *
 * <p>Two rules are carried over from the models these mirror:
 *
 * <ul>
 *   <li><b>Nothing is null where a list would do.</b> The server promises it,
 *       and the compact constructors below hold to it even when the server
 *       that answered is older than the field being read -- a missing list
 *       arrives as null from any JSON parser, and one null is all it takes to
 *       put a stack trace on screen instead of an answer.
 *   <li><b>The pipeline's vocabulary is preserved.</b> The components are
 *       named as the JSON names them, {@code row_count} and all, rather than
 *       being camel-cased into Java's house style. A second spelling of every
 *       field is a second thing to keep in step, and the drift it hides is
 *       exactly what the contract test exists to catch.
 * </ul>
 */
public final class Models {

    private Models() {
    }

    /** Where a job is in its life. A refusal is still {@code succeeded}. */
    public enum JobStatus {
        QUEUED("queued"),
        RUNNING("running"),
        SUCCEEDED("succeeded"),
        FAILED("failed"),
        CANCELLED("cancelled");

        private final String wire;

        JobStatus(String wire) {
            this.wire = wire;
        }

        @JsonValue
        public String wire() {
            return wire;
        }

        /** True once the job will not change again. */
        public boolean terminal() {
            return this == SUCCEEDED || this == FAILED || this == CANCELLED;
        }

        @JsonCreator
        public static JobStatus of(String wire) {
            for (JobStatus status : values()) {
                if (status.wire.equals(wire)) {
                    return status;
                }
            }
            throw new IllegalArgumentException("unknown job status: " + wire);
        }
    }

    /**
     * Was the answer right? The whole of the required input: correct
     * ({@code yes}), wrong ({@code no}), or correct but incomplete
     * ({@code incomplete}). The first two wire values predate the third and
     * are kept, so every verdict already recorded still reads the same.
     */
    public enum Verdict {
        YES("yes"),
        NO("no"),
        INCOMPLETE("incomplete");

        private final String wire;

        Verdict(String wire) {
            this.wire = wire;
        }

        @JsonValue
        public String wire() {
            return wire;
        }

        @JsonCreator
        public static Verdict of(String wire) {
            for (Verdict verdict : values()) {
                if (verdict.wire.equals(wire)) {
                    return verdict;
                }
            }
            throw new IllegalArgumentException("unknown verdict: " + wire);
        }
    }

    /**
     * One step of the pipeline, as it happens. {@code candidate} says which
     * wording's run reported it under the ensemble (7.0): 0 the question as
     * asked, 1 and up a rewording, null for the ensemble's own steps and for
     * a server older than 7.0.
     */
    public record ProgressEvent(int seq, String step, String label, String detail, String at,
                                Integer candidate) {
        public ProgressEvent {
            step = text(step);
            label = text(label);
            detail = text(detail);
            at = text(at);
        }
    }

    /**
     * The rows.
     *
     * <p>A cell is {@code Object} because it genuinely is: a Postgres
     * {@code numeric} crosses JSON as a string so it does not lose precision,
     * while an {@code integer} crosses as a number. Parse against
     * {@code columns} when the difference matters -- {@code chart.Values} does.
     */
    public record ResultTable(List<String> columns, List<List<Object>> rows, int row_count,
                              boolean truncated) {
        public ResultTable {
            columns = list(columns);
            rows = list(rows);
        }
    }

    /**
     * What the Visual Formatter thinks these rows should be drawn as.
     *
     * <p>A suggestion, not a rendering: {@code x}, {@code y} and {@code series}
     * name columns of {@code result}, and this application owns what is drawn
     * from them. {@code kind} stays a string rather than an enum because the
     * pipeline decides it, and a client that threw on a kind it had not been
     * told about would fail on the release that added one.
     */
    public record ChartSpec(String kind, String x, List<String> y, String series) {
        public ChartSpec {
            kind = text(kind);
            y = list(y);
        }
    }

    /** A sentence in the narrative, tied to the cells that support it. */
    public record Claim(String text, Double value, List<List<Object>> cells, String formula) {
        public Claim {
            // Qualified: the record's own `text()` accessor is in scope here
            // and shadows the helper by name, whatever its arity.
            text = Models.text(text);
            cells = list(cells);
        }
    }

    /** What the Audit Checker made of the narrative. */
    public record AuditReport(boolean passed, List<String> unsupported_claims,
                              List<String> drop_reasons, List<String> missing_assumptions,
                              String semantic_issue) {
        public AuditReport {
            unsupported_claims = list(unsupported_claims);
            drop_reasons = list(drop_reasons);
            missing_assumptions = list(missing_assumptions);
        }
    }

    /** Per-node cost, and for a node that called a model, which one answered and why. */
    public record TraceEntry(String node, double ms, int model_calls, String detail,
                             String model, String rung, String route, List<String> hops) {
        public TraceEntry {
            node = text(node);
            detail = text(detail);
            model = text(model);
            rung = text(rung);
            route = text(route);
            hops = list(hops);
        }
    }

    /** A phrase from the question, matched to a value in the database. */
    public record LiteralMatch(String phrase, String table, String column, String value,
                               double score) {
        public LiteralMatch {
            phrase = text(phrase);
            table = text(table);
            column = text(column);
            value = text(value);
        }
    }

    /** How the ensemble's vote went: {@code agreed} of the {@code admissible} runs, of {@code total}. */
    public record EnsembleAgreement(int admissible, int agreed, int total, String level, String why,
                                    int set_aside) {
        public EnsembleAgreement {
            level = text(level);
            why = text(why);
        }
    }

    /** The Judge's verdict on one group's answer. */
    public record EnsembleVerdict(int group, boolean accepted, String why) {
        public EnsembleVerdict {
            why = text(why);
        }
    }

    /** The Judge's verdicts on the answers, given on every question before the vote. */
    public record EnsembleJudgement(List<EnsembleVerdict> verdicts, List<Integer> set_aside, boolean overruled,
                                    Integer instead_of, String model, String error) {
        public EnsembleJudgement {
            verdicts = list(verdicts);
            set_aside = list(set_aside);
            model = text(model);
            error = text(error);
        }
    }

    /** A column another agreeing run carried, joined onto the delivered rows. */
    public record JoinedColumn(String column, int from_candidate, String key, String table) {
        public JoinedColumn {
            column = text(column);
            key = text(key);
            table = text(table);
        }
    }

    /** A column another agreeing run carried that would not join cleanly, and why. */
    public record DeclinedColumn(String column, int from_candidate, String why) {
        public DeclinedColumn {
            column = text(column);
            why = text(why);
        }
    }

    /** A group of runs that lost the vote: its key fact, and how its query differs. */
    public record EnsembleDissent(int group, List<Integer> members, String signature, String differs) {
        public EnsembleDissent {
            members = list(members);
            signature = text(signature);
            differs = text(differs);
        }
    }

    /** One wording's run: what it was asked, what it wrote, how it ended. */
    public record EnsembleCandidate(int index, String wording, String origin, int wave, String changed,
                                    String outcome, boolean admissible, List<String> reasons, String sql,
                                    String signature, int attempts, Integer group, double duration_ms,
                                    List<TraceEntry> trace) {
        public EnsembleCandidate {
            wording = text(wording);
            origin = text(origin);
            changed = text(changed);
            outcome = text(outcome);
            reasons = list(reasons);
            sql = text(sql);
            signature = text(signature);
            trace = list(trace);
        }
    }

    /** A rewording the fidelity gate would not run, and the check it failed. */
    public record DiscardedRewording(int index, String text, String changed, String reason) {
        public DiscardedRewording {
            // Qualified: the component `text` hides the helper of that name.
            text = Models.text(text);
            changed = Models.text(changed);
            reason = Models.text(reason);
        }
    }

    /**
     * The question asked several ways (7.0): the vote, the delivered run and
     * every run's record. Null on an answer from a server with the ensemble
     * off, or older than 7.0.
     */
    public record Ensemble(EnsembleAgreement agreement, Integer chosen, List<Integer> fused_from,
                           boolean columns_fused, List<JoinedColumn> joined_columns,
                           List<DeclinedColumn> declined_columns, int claims_added, int claims_dropped,
                           List<EnsembleDissent> dissent, EnsembleJudgement judged,
                           List<EnsembleCandidate> candidates, List<DiscardedRewording> discarded,
                           int parallel_calls) {
        public Ensemble {
            fused_from = list(fused_from);
            joined_columns = list(joined_columns);
            declined_columns = list(declined_columns);
            dissent = list(dissent);
            candidates = list(candidates);
            discarded = list(discarded);
        }
    }

    /** Everything the pipeline produced for one question. */
    public record Answer(String answer, String narrative, String sql, String verdict, String intent,
                         String clarification, List<String> tables, List<LiteralMatch> literals,
                         ResultTable result, ChartSpec chart, List<Claim> claims, AuditReport audit,
                         Double plan_cost, int attempts, List<TraceEntry> trace,
                         Map<String, String> retrieval_errors, Map<String, String> node_errors,
                         Ensemble ensemble) {
        public Answer {
            answer = text(answer);
            narrative = text(narrative);
            sql = text(sql);
            verdict = text(verdict);
            intent = text(intent);
            tables = list(tables);
            literals = list(literals);
            claims = list(claims);
            audit = audit == null ? EMPTY_AUDIT : audit;
            trace = list(trace);
            retrieval_errors = map(retrieval_errors);
            node_errors = map(node_errors);
        }
    }

    public record JobLinks(String self, String events) {
        public JobLinks {
            self = text(self);
            events = text(events);
        }
    }

    /** A question in flight, or one that has finished. One shape throughout. */
    public record Job(String id, JobStatus status, String question, Map<String, String> metadata,
                      String created_at, String started_at, String finished_at, Double duration_ms,
                      List<ProgressEvent> progress, Answer answer, String error, JobLinks links) {
        public Job {
            id = text(id);
            question = text(question);
            metadata = map(metadata);
            created_at = text(created_at);
            progress = list(progress);
            links = links == null ? NO_LINKS : links;
        }
    }

    public record JobList(List<Job> jobs, int count) {
        public JobList {
            jobs = list(jobs);
        }
    }

    /**
     * What an administrator's {@code POST /v1/admin/reload} dropped, to be
     * read again. Mirrored for completeness: this client does not reload.
     */
    public record Reloaded(List<String> reloaded, int sessions_forgotten) {
        public Reloaded {
            reloaded = list(reloaded);
        }
    }

    /**
     * What this server will not let a client exceed.
     *
     * <p>{@code max_question_length} and {@code max_metadata_entries} are here
     * so a client can refuse an over-long question in the text box rather than
     * discovering the rule from a 422 after the user pressed Enter. They were
     * added to the API when this client was written, which is the first client
     * that could not find them out from a browser's network tab.
     */
    public record Limits(int max_rows, int max_attempts, double max_plan_cost,
                         int statement_timeout_ms, int max_concurrency, double max_wait_seconds,
                         int max_question_length, int max_metadata_entries) {
    }

    /** How this server asks a question several ways (7.0). */
    public record EnsembleSettings(boolean enabled, int paraphrases, int max_paraphrases, int waves,
                                   int parallel_calls, boolean judge, boolean fuse_columns) {
    }

    /** Which optional stages this server is running with. */
    public record Pipeline(boolean supervisor, boolean literals, boolean narrate, boolean audit,
                           String schema_retrieval, List<String> nodes, EnsembleSettings ensemble) {
        public Pipeline {
            schema_retrieval = text(schema_retrieval);
            nodes = list(nodes);
        }
    }

    /** Everything a client needs to configure itself against this server. */
    public record Meta(String service, String version, String model, List<String> intents,
                       List<String> tables, String scope, Limits limits, Pipeline pipeline,
                       Map<String, Object> tls, String authentication, boolean feedback,
                       Map<String, Object> routing) {
        public Meta {
            service = text(service);
            version = text(version);
            model = text(model);
            intents = list(intents);
            tables = list(tables);
            scope = text(scope);
            tls = map(tls);
            authentication = text(authentication);
            routing = map(routing);
        }
    }

    /** What {@code /healthz} answers: the process is up. It checks nothing else. */
    public record Health(String status, String version, double uptime_seconds) {
        public Health {
            status = text(status);
            version = text(version);
        }
    }

    public record Check(boolean ok, String detail) {
        public Check {
            detail = text(detail);
        }
    }

    public record Readiness(boolean ready, Map<String, Check> checks, List<String> warnings) {
        public Readiness {
            checks = map(checks);
            warnings = list(warnings);
        }
    }

    /** What a client may send. */
    public record AskRequest(String question, String principal, Map<String, String> metadata) {
    }

    /**
     * What a user says about an answer.
     *
     * <p>Only these two fields go over the wire. The question, the SQL and the
     * result shape are taken from the job by the server -- it still has it,
     * since a vote happens while the answer is on screen -- so a client cannot
     * submit a snapshot describing an answer the agent never gave.
     */
    public record FeedbackRequest(Verdict verdict, String comment) {
    }

    /** The receipt for a recorded verdict. */
    public record FeedbackModel(String id, String job_id, Verdict verdict, String comment,
                                String state) {
        public FeedbackModel {
            id = text(id);
            job_id = text(job_id);
            comment = text(comment);
            state = text(state);
        }
    }

    // --- the auth service, which is a second server -----------------------
    //
    // Mirrored from auth/nl2sql_auth/models.py rather than the API's models,
    // and checked against that file by the same contract test.

    /**
     * What signing in sends to {@code /auth/token}.
     *
     * <p>Its own {@code toString}, because a record's would print the password
     * into whatever log or exception message the record ever reaches.
     */
    public record SignIn(String username, String password) {
        @Override
        public String toString() {
            return "SignIn[username=" + username + "]";
        }
    }

    /**
     * A session for a client that holds its own token: who signed in, the
     * groups Postgres says they are in, when it ends (seconds since the
     * epoch), and the token to present until then.
     */
    public record Token(String user, String name, List<String> roles, String kind, long expires_at,
                        String token) {
        public Token {
            user = text(user);
            name = text(name);
            roles = list(roles);
            kind = text(kind);
            token = text(token);
        }

        /** Not the token, for the reason {@link SignIn} leaves out the password. */
        @Override
        public String toString() {
            return "Token[user=" + user + ", roles=" + roles + ", expires_at=" + expires_at + "]";
        }

        /** Their name and user name, or the user name alone when there is no other. */
        public String who() {
            return name.isEmpty() || name.equals(user) ? user : name + " (" + user + ")";
        }
    }

    /** One error shape for every failure. Branch on {@code code}. */
    public record ApiErrorBody(Map<String, Object> error) {
        public ApiErrorBody {
            error = map(error);
        }
    }

    private static final AuditReport EMPTY_AUDIT =
            new AuditReport(true, List.of(), List.of(), List.of(), null);

    private static final JobLinks NO_LINKS = new JobLinks("", "");

    /**
     * The three normalisers the compact constructors above are written in
     * terms of, so the decision to fill a null in is made once.
     *
     * <p>Copied into an unmodifiable view rather than through
     * {@code List.copyOf}, which rejects a null element: a cell is allowed to
     * be null -- that is what an empty column reads as -- and so is a value in
     * the {@code tls} map. Refusing them here would turn a perfectly ordinary
     * answer into an exception while it was being parsed.
     */
    static <T> List<T> list(List<T> value) {
        return value == null ? List.of() : Collections.unmodifiableList(new ArrayList<>(value));
    }

    static <K, V> Map<K, V> map(Map<K, V> value) {
        return value == null ? Map.of() : Collections.unmodifiableMap(new LinkedHashMap<>(value));
    }

    static String text(String value) {
        return value == null ? "" : value;
    }
}
