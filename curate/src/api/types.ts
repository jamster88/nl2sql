/**
 * The review service's wire contract, as this page reads it.
 *
 * Written by hand, like the other interfaces' types, and held to
 * `review/nl2sql_review/models.py` by `tests/curate/test_curate_gui_contract.py`,
 * which compares every interface here with the model it mirrors, field by
 * field. Only the models this page uses are here; the review interface
 * mirrors the queue's.
 */

export type FixKind = "corrections" | "completions";
export type SnippetKind = "join" | "filter" | "measure" | "dimension";

export interface ApiErrorBody {
  error: { code: string; message: string; detail?: Record<string, unknown> };
}

// --- the service -------------------------------------------------------------

export interface ReviewLimits {
  reload_timeout_seconds: number;
  validate_timeout_ms: number;
  validate_max_rows: number;
}

export interface ReviewMeta {
  service: string;
  version: string;
  states: string[];
  document: string;
  golden_count: number;
  next_pair_id: string;
  counts: Record<string, number>;
  verdicts: string[];
  counts_by_verdict: Record<string, Record<string, number>>;
  fixes: Record<string, number>;
  reload_context: boolean;
  reload_vectors: boolean;
  limits: ReviewLimits;
  authentication: "none" | "bearer";
  warnings: string[];
}

export interface StepModel {
  name: string;
  ran: boolean;
  ok: boolean;
  detail: string;
}

/** What running SQL against the live retail database showed. */
export interface ValidationModel {
  sql: string;
  valid: boolean;
  problems: string[];
  warnings: string[];
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  plan_cost: number | null;
  elapsed_ms: number;
}

/** A submission, as a removal hands one back to the review queue. */
export interface SubmissionModel {
  id: string;
  job_id: string;
  verdict: string;
  question: string;
  sql_code: string;
  answer: string;
  narrative: string;
  intent: string;
  tables: string;
  row_count: number;
  columns: string[];
  comment: string;
  agent_version: string;
  submitted_at: string | null;
  state: string;
  reviewer: string;
  review_note: string;
  reviewed_at: string | null;
  draft: Record<string, unknown>;
  promoted_pair_id: string | null;
}

// --- golden pairs --------------------------------------------------------------

export interface DraftModel {
  title: string;
  question: string;
  tables: string;
  keywords: string;
  reasoning_target: string;
  sql_code: string;
  result: string;
  translation_note: string;
  suite: string;
  extra_meta: Record<string, string>;
}

export interface PreviewModel {
  pair_id: string;
  markdown: string;
  valid: boolean;
  problems: string[];
  suite_in_force: string;
}

export interface PromotionModel {
  pair_id: string;
  chunk_id: string;
  suite: string;
  title: string;
  markdown: string;
  document: string;
  backup: string;
  pairs_before: number;
  pairs_after: number;
  reloaded: boolean;
  steps: StepModel[];
}

export interface GoldenPairModel {
  pair_id: string;
  chunk_id: string;
  title: string;
  suite: string;
  question: string;
  tables: string[];
  keywords: string[];
  reasoning_target: string;
  sql_code: string;
  result: string;
  /** The submission it came from; taking it out reopens that submission. */
  submission_id: string | null;
}

export interface GoldenSet {
  pairs: GoldenPairModel[];
  count: number;
  document: string;
  next_pair_id: string;
  suite_in_force: string;
  error: string | null;
}

export interface GoldenResultModel {
  promotion: PromotionModel;
  validation: ValidationModel;
}

export interface WithdrawalModel {
  kind: "golden" | FixKind;
  id: string;
  found: boolean;
  document: string;
  backup: string;
  pairs_before: number;
  pairs_after: number;
  reloaded: boolean;
  steps: StepModel[];
}

export interface GoldenRemovalModel {
  pair_id: string;
  withdrawal: WithdrawalModel;
  submission: SubmissionModel | null;
}

// --- corrections and completions -------------------------------------------------

export interface FixModel {
  fix_id: string;
  submission_id: string | null;
  job_id: string;
  question: string;
  incorrect_sql: string;
  incorrect_answer: string;
  incorrect_columns: string[];
  incorrect_row_count: number;
  corrected_sql: string;
  corrected_columns: string[];
  corrected_rows: unknown[][];
  corrected_row_count: number;
  corrected_truncated: boolean;
  plan_cost: number | null;
  user_comment: string;
  reviewer: string;
  review_note: string;
  agent_version: string;
  source: string;
  created_at: string | null;
  embedded: boolean;
}

export interface FixList {
  kind: FixKind;
  fixes: FixModel[];
  count: number;
}

export interface CuratedFixValidateRequest {
  sql: string;
  incorrect_sql: string;
}

export interface CuratedFixRequest {
  question: string;
  sql: string;
  incorrect_sql: string;
  review_note: string;
}

export interface CuratedFixResultModel {
  kind: FixKind;
  fix: FixModel;
  validation: ValidationModel;
  embedded: boolean;
  embed_detail: string;
}

export interface FixRemovalModel {
  kind: FixKind;
  fix_id: string;
  found: boolean;
  fix: FixModel | null;
  submission: SubmissionModel | null;
}

// --- SQL snippets ------------------------------------------------------------------

export interface SnippetDraftModel {
  name: string;
  kind: string;
  tables: string;
  keywords: string;
  means: string;
  applies_to: string;
  sql: string;
  note: string;
}

export interface SnippetRequest {
  draft: SnippetDraftModel;
}

export interface SnippetPreviewRequest {
  draft: SnippetDraftModel;
  snippet_id: string | null;
}

export interface SnippetPreviewModel {
  snippet_id: string;
  markdown: string;
  valid: boolean;
  problems: string[];
}

export interface SnippetValidationModel {
  kind: string;
  valid: boolean;
  problems: string[];
  warnings: string[];
  probe_sql: string;
  applies_to: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  rows_before: number | null;
  rows_after: number | null;
  tables: string[];
  plan_cost: number | null;
  elapsed_ms: number;
}

export interface SnippetModel {
  snippet_id: string;
  chunk_id: string;
  name: string;
  kind: string;
  tables: string[];
  keywords: string[];
  means: string;
  applies_to: string;
  sql: string;
  note: string;
}

export interface SnippetStoreModel {
  reachable: boolean;
  snippets: number;
  embedded: number;
  current: boolean;
  detail: string;
}

export interface SnippetSet {
  snippets: SnippetModel[];
  count: number;
  document: string;
  next_snippet_id: string;
  kinds: string[];
  store: SnippetStoreModel;
  error: string | null;
}

export interface SnippetResultModel {
  action: "added" | "changed" | "removed";
  snippet_id: string;
  chunk_id: string;
  markdown: string;
  document: string;
  backup: string;
  snippets_before: number;
  snippets_after: number;
  reloaded: boolean;
  steps: StepModel[];
  validation: SnippetValidationModel | null;
}

export interface SchemaColumn {
  name: string;
  type: string;
}

export interface SchemaTable {
  name: string;
  columns: SchemaColumn[];
}

export interface SchemaModel {
  tables: SchemaTable[];
  error: string | null;
}
