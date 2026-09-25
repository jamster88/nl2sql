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

    /** Was the answer right? The whole of the required input. */
    public enum Verdict {
        YES("yes"),
        NO("no");

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

    /** One step of the pipeline, as it happens. */
    public record ProgressEvent(int seq, String step, String label, String detail, String at) {
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
                              List<String> drop_reasons, List<String> redactions,
                              String semantic_issue) {
        public AuditReport {
            unsupported_claims = list(unsupported_claims);
            drop_reasons = list(drop_reasons);
            redactions = list(redactions);
        }
    }

    /** Per-node cost. */
    public record TraceEntry(String node, double ms, int model_calls, String detail) {
        public TraceEntry {
            node = text(node);
            detail = text(detail);
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

    /** Everything the pipeline produced for one question. */
    public record Answer(String answer, String narrative, String sql, String verdict, String intent,
                         String clarification, List<String> tables, List<LiteralMatch> literals,
                         ResultTable result, ChartSpec chart, List<Claim> claims, AuditReport audit,
                         Double plan_cost, int attempts, List<TraceEntry> trace,
                         Map<String, String> retrieval_errors) {
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

    /** Which optional stages this server is running with. */
    public record Pipeline(boolean supervisor, boolean literals, boolean narrate, boolean audit,
                           String schema_retrieval, List<String> nodes) {
        public Pipeline {
            schema_retrieval = text(schema_retrieval);
            nodes = list(nodes);
        }
    }

    /** Everything a client needs to configure itself against this server. */
    public record Meta(String service, String version, String model, List<String> intents,
                       List<String> tables, String scope, Limits limits, Pipeline pipeline,
                       Map<String, Object> tls, String authentication, boolean feedback) {
        public Meta {
            service = text(service);
            version = text(version);
            model = text(model);
            intents = list(intents);
            tables = list(tables);
            scope = text(scope);
            tls = map(tls);
            authentication = text(authentication);
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
