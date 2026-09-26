/**
 * The wire contract, in TypeScript.
 *
 * These are the shapes in `agent/nl2sql_agent/api/models.py`. They are
 * written by hand rather than generated for one reason: a generated
 * `api.d.ts` cannot be read, and this file is the document a GUI author
 * reaches for first. It is kept honest by `tests/gui/test_gui_contract.py`,
 * which compares every field here against the pydantic model it mirrors and
 * fails when either side gains something the other did not.
 *
 * Generating them instead is a supported alternative and takes one command:
 *
 *     python -m nl2sql_agent.api --print-openapi > openapi.json
 *     npx openapi-typescript openapi.json -o src/api/generated.d.ts
 */

/** Where a job is in its life. A refusal is still `succeeded`. */
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

/** One step of the pipeline, as it happens. */
export interface ProgressEvent {
  seq: number;
  /** Graph node name, e.g. `generate_sql`. Key the UI off this. */
  step: string;
  /** Short human label for the step, e.g. `sql`. */
  label: string;
  detail: string;
  at: string;
}

/**
 * The rows.
 *
 * Cells are `unknown` rather than `string | number` because they genuinely
 * are: a Postgres `numeric` arrives as a string so it does not lose
 * precision in JSON, while an `integer` arrives as a number. Parse against
 * `columns` when the difference matters -- `charts/values.ts` does.
 */
export interface ResultTable {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  /** True when the query returned more rows than the server's MAX_ROWS. */
  truncated: boolean;
}

/** The pipeline's own chart kinds. `choose_chart` produces exactly these. */
export type ChartKind = "scalar" | "bar" | "grouped_bar" | "line" | "scatter" | "table";

/**
 * What the Visual Formatter thinks these rows should be drawn as.
 *
 * A suggestion, not a rendering: `x`, `y` and `series` name columns of
 * `result`, and this application owns what is drawn from them.
 */
export interface ChartSpec {
  kind: ChartKind;
  x: string | null;
  y: string[];
  series: string | null;
}

/** A sentence in the narrative, tied to the cells that support it. */
export interface Claim {
  text: string;
  value: number | null;
  /** `[row_index, column_name]` pairs the claim was read from. */
  cells: [number, string][];
  formula: string | null;
}

/** What the Audit Checker made of the narrative. */
export interface AuditReport {
  passed: boolean;
  unsupported_claims: string[];
  drop_reasons: string[];
  redactions: string[];
  semantic_issue: string | null;
}

/** Per-node cost. */
export interface TraceEntry {
  node: string;
  ms: number;
  model_calls: number;
  detail: string;
}

/** A phrase from the question, matched to a value in the database. */
export interface LiteralMatch {
  phrase: string;
  table: string;
  column: string;
  value: string;
  score: number;
}

/** Everything the pipeline produced for one question. */
export interface Answer {
  answer: string;
  narrative: string;
  sql: string;
  /** `proceed`, `refused`, `out_of_domain` or `ambiguous`. */
  verdict: string;
  intent: string;
  clarification: string | null;
  tables: string[];
  literals: LiteralMatch[];
  /** `null` for a refusal -- no query ran. An empty table means none matched. */
  result: ResultTable | null;
  chart: ChartSpec | null;
  claims: Claim[];
  audit: AuditReport;
  plan_cost: number | null;
  attempts: number;
  trace: TraceEntry[];
  retrieval_errors: Record<string, string>;
}

export interface JobLinks {
  self: string;
  events: string;
}

/** A question in flight, or one that has finished. One shape throughout. */
export interface Job {
  id: string;
  status: JobStatus;
  question: string;
  metadata: Record<string, string>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  progress: ProgressEvent[];
  answer: Answer | null;
  error: string | null;
  links: JobLinks;
}

export interface JobList {
  jobs: Job[];
  count: number;
}

/**
 * What a client may not exceed, so it can stop before the server does.
 *
 * The last two are for the clients that cannot find them out any other way:
 * a browser sees a 422 in its network tab, while the desktop client
 * (`desktop/`) shows the user whatever it was given, and "422 Unprocessable
 * Entity" is not an explanation of a text box forty characters too long.
 */
export interface Limits {
  max_rows: number;
  max_attempts: number;
  max_plan_cost: number;
  statement_timeout_ms: number;
  max_concurrency: number;
  max_wait_seconds: number;
  max_question_length: number;
  max_metadata_entries: number;
}

/**
 * Which optional stages this server is running with.
 *
 * Read on start-up and used to decide what to render: `narrate: false` means
 * there is no paragraph to show, `audit: false` means no verification badge.
 */
export interface Pipeline {
  supervisor: boolean;
  literals: boolean;
  narrate: boolean;
  audit: boolean;
  schema_retrieval: string;
  nodes: string[];
}

/** Everything a client needs to configure itself against this server. */
export interface Meta {
  service: string;
  version: string;
  model: string;
  intents: string[];
  tables: string[];
  /** One sentence naming what the database covers. */
  scope: string;
  limits: Limits;
  pipeline: Pipeline;
  tls: Record<string, unknown>;
  authentication: "none" | "bearer";
  /**
   * Whether this server accepts feedback. False when it has no staging
   * database configured, in which case the verdict buttons are not drawn --
   * a button whose every click fails is worse than no button.
   */
  feedback: boolean;
}

/** What `/healthz` answers: the process is up. It checks nothing else. */
export interface Health {
  status: "ok";
  version: string;
  uptime_seconds: number;
}

export interface Check {
  ok: boolean;
  detail: string;
}

export interface Readiness {
  ready: boolean;
  checks: Record<string, Check>;
  warnings: string[];
}

/** One error shape for every failure. Branch on `code`. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail?: Record<string, unknown>;
  };
}

/** What a client may send. */
export interface AskRequest {
  question: string;
  principal?: string;
  metadata?: Record<string, string>;
}

/**
 * Was the answer right? The whole of the required input: correct (`yes`),
 * wrong (`no`), or correct but incomplete (`incomplete`).
 */
export type Verdict = "yes" | "no" | "incomplete";

/**
 * What a user says about an answer.
 *
 * Only these two fields go over the wire. The question, the SQL and the
 * result shape are taken from the job by the server -- it still has it,
 * since a vote happens while the answer is on screen -- so a client cannot
 * submit a snapshot describing an answer the agent never gave.
 */
export interface FeedbackRequest {
  verdict: Verdict;
  comment: string;
}

/**
 * The receipt for a recorded verdict.
 *
 * `state` is what the review service has made of it, and is always
 * `pending` at capture time: the role the API writes as cannot see any
 * other state, let alone create one.
 */
export interface FeedbackModel {
  id: string;
  job_id: string;
  verdict: Verdict;
  comment: string;
  state: "pending";
}
