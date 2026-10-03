/**
 * The wire contract, in TypeScript.
 *
 * These are the shapes in `review/nl2sql_review/models.py`, written by hand
 * for the same reason the web GUI's are: a generated `api.d.ts` is not
 * something anyone reads, and this file is the document a GUI author opens
 * first. `tests/review/test_review_gui_contract.py` compares every field
 * here against the pydantic model it mirrors and fails when either side
 * gains something the other did not.
 */

/**
 * Where a submission is in its life. `promoted` and `corrected` are where the
 * queue ends; reopening takes the pair or the fix back out and returns it to
 * `pending`.
 */
export type State = "pending" | "accepted" | "rejected" | "promoted" | "corrected";

/**
 * Was the answer right? What the user actually said: correct (`yes`), wrong
 * (`no`), or correct but incomplete (`incomplete`).
 */
export type Verdict = "yes" | "no" | "incomplete";

/**
 * One captured verdict, with the snapshot that outlives the job.
 *
 * Everything from `question` to `agent_version` is what the agent produced
 * and the user judged. None of it is editable: an interface that let a
 * curator rewrite the question would turn the staging table into a place
 * where evidence changes. Edits live in `draft`.
 */
export interface SubmissionModel {
  id: string;
  job_id: string;
  verdict: Verdict;
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
  state: State;
  reviewer: string;
  review_note: string;
  reviewed_at: string | null;
  draft: Record<string, unknown>;
  promoted_pair_id: string | null;
}

export interface SubmissionList {
  submissions: SubmissionModel[];
  count: number;
  /** Every state present even at zero, so a queue badge reads "0". */
  counts: Record<string, number>;
  /** The same, per verdict: one pane, and one queue, per verdict. */
  counts_by_verdict: Record<string, Record<string, number>>;
}

/**
 * The golden pair being built out of a submission.
 *
 * Four fields arrive from the submission. `keywords`, `reasoning_target`
 * and `result` do not and cannot: they are judgements about what the
 * question *tests*, and no thumbs-up carries them. They are the work.
 */
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

export interface ReviewRequest {
  state?: State | null;
  reviewer?: string | null;
  review_note?: string | null;
  draft?: DraftModel | null;
}

export interface PreviewRequest {
  draft: DraftModel;
}

/** The block that would be written, and every reason it could not be. */
export interface PreviewModel {
  pair_id: string;
  markdown: string;
  valid: boolean;
  problems: string[];
  suite_in_force: string;
}

export interface StepModel {
  name: string;
  ran: boolean;
  ok: boolean;
  detail: string;
}

/**
 * What a promotion did, including the parts that did not work.
 *
 * `reloaded: false` with a `pair_id` set is a real state and not a failure:
 * the pair is in the question document, which is the source of truth, and
 * the stores built from it have not caught up. Nothing is lost.
 */
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

export interface PromotionRecord {
  pair_id: string;
  submission_id: string;
  chunk_id: string;
  suite: string;
  title: string;
  reviewer: string;
  promoted_at: string | null;
  reloaded: boolean;
  reload_detail: string;
}

export interface PromotionList {
  promotions: PromotionRecord[];
  count: number;
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
  /** The submission it was promoted from; null for a pair written by hand. */
  submission_id: string | null;
}

export interface GoldenSet {
  pairs: GoldenPairModel[];
  count: number;
  document: string;
  next_pair_id: string;
  suite_in_force: string;
  /** Set when the document cannot be read or parsed -- not the same as empty. */
  error: string | null;
}

export interface ReviewLimits {
  reload_timeout_seconds: number;
  validate_timeout_ms: number;
  validate_max_rows: number;
}

/**
 * Where a fix goes: `corrections` for an answer marked wrong, `completions`
 * for one marked correct but incomplete. Neither is the golden set.
 */
export type FixKind = "corrections" | "completions";

export interface ValidateRequest {
  sql: string;
}

/**
 * What running a query against the live retail database showed. `valid` is
 * the only field that decides whether it can be stored; the warnings are
 * for the reviewer to judge.
 */
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

export interface FixRequest {
  sql: string;
  review_note: string;
}

/** One stored fix: the question, the incorrect answer and the correct one. */
export interface FixModel {
  fix_id: string;
  /** Null for a fix written in the curation interface (5.6). */
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
  /** `review` for a fix made here, `curated` for one written in the curation interface. */
  source: string;
  created_at: string | null;
  embedded: boolean;
}

export interface FixList {
  kind: FixKind;
  fixes: FixModel[];
  count: number;
}

/**
 * What saving a fix did. `embedded: false` with a `fix` present is stored
 * and not yet retrievable -- the next save that reaches the embedding host
 * catches it up.
 */
export interface FixResultModel {
  kind: FixKind;
  fix: FixModel;
  validation: ValidationModel;
  submission: SubmissionModel;
  embedded: boolean;
  embed_detail: string;
}

/**
 * What reopening or deleting a submission took back out: a pair from the
 * golden set (`golden`, with the counts and the reload a promotion reports)
 * or a fix from its store. `found: false` means it was already gone --
 * removed by hand -- and there was nothing to take out.
 */
export interface WithdrawalModel {
  kind: "golden" | FixKind;
  id: string;
  found: boolean;
  document: string;
  backup: string;
  pairs_before: number;
  pairs_after: number;
  /** For `golden`: whether both stores were rebuilt from the document. */
  reloaded: boolean;
  steps: StepModel[];
}

/** A submission reopened or deleted, and what came out with it. */
export interface UndoModel {
  action: "reopened" | "deleted";
  /** As it is now when reopened; as it was when deleted. */
  submission: SubmissionModel;
  /** Null when it had produced nothing. */
  withdrawn: WithdrawalModel | null;
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
  /** How many fixes each store holds. */
  fixes: Record<string, number>;
  reload_context: boolean;
  reload_vectors: boolean;
  limits: ReviewLimits;
  authentication: "none" | "bearer";
  warnings: string[];
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

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail?: Record<string, unknown>;
  };
}
