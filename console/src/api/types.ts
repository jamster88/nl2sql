/**
 * The wire contract, in TypeScript.
 *
 * These are the shapes in `agent/nl2sql_agent/console/models.py`, written by
 * hand for the same reason the other two interfaces' are: this file is the
 * document an author opens first, and a generated one is not read.
 * `tests/console/test_console_gui_contract.py` compares every field here
 * against the pydantic model it mirrors and fails when either side gains
 * something the other did not.
 */

/** `run` returns rows; `plan` stops at EXPLAIN; `analyze` times a real run. */
export type Mode = "run" | "plan" | "analyze";

/**
 * The agent's three gates, by the names the pipeline gives them: the static
 * validator, the planner gate, and the executor.
 */
export type Stage = "static" | "planner" | "runtime";

export interface ConsoleLimits {
  statement_timeout_ms: number;
  max_plan_cost: number;
  /** How many rows the agent reads (MAX_ROWS). */
  agent_max_rows: number;
  /** How many rows this console is sent per query (CONSOLE_MAX_ROWS). */
  max_rows: number;
  sample_rows: number;
  max_sql_length: number;
}

export interface ConsoleMeta {
  service: string;
  version: string;
  database: string;
  role: string;
  server_version: string;
  /** False when the role could write were it not for the transaction. */
  read_only: boolean;
  db_schema: string;
  tables: number;
  limits: ConsoleLimits;
  authentication: "bearer" | "none" | "session";
  /** The role a query runs as: a signed-in person's own, or `role`. */
  runs_as: string;
  tls: Record<string, unknown>;
  warnings: string[];
}

export interface ColumnModel {
  name: string;
  data_type: string;
  not_null: boolean;
  comment: string | null;
}

export interface TableModel {
  name: string;
  comment: string | null;
  approx_rows: number;
  columns: ColumnModel[];
  constraints: string[];
}

export interface SchemaModel {
  db_schema: string;
  tables: TableModel[];
}

/** The block of the agent's prompt that describes one table. */
export interface PromptModel {
  table: string;
  sample_rows: number;
  text: string;
}

export interface QueryRequest {
  sql: string;
  mode?: Mode;
}

export interface IssueModel {
  stage: Stage;
  message: string;
}

/** What the agent would have done with the query. */
export interface AgentVerdict {
  accepted: boolean;
  /** The first gate that would have refused it. */
  stage: Stage | null;
  issues: IssueModel[];
  notes: string[];
}

export interface ResultColumn {
  name: string;
  data_type: string;
}

export interface QueryResult {
  sql: string;
  mode: Mode;
  /** False when a gate refused it, or when the mode was `plan`. */
  executed: boolean;
  agent: AgentVerdict;
  columns: ResultColumn[];
  rows: unknown[][];
  row_count: number;
  /** More rows than `max_rows` came back; only the first `max_rows` are here. */
  truncated: boolean;
  max_rows: number;
  plan: unknown;
  plan_cost: number | null;
  max_plan_cost: number;
  error: string | null;
  elapsed_ms: number;
}

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

/** The one error shape every failure arrives in. */
export interface ApiErrorBody {
  error: { code: string; message: string; detail?: Record<string, unknown> };
}
