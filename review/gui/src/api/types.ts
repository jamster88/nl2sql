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

/** Where a submission is in its life. `promoted` is terminal. */
export type State = "pending" | "accepted" | "rejected" | "promoted";

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
  max_pair_number: number;
  reload_timeout_seconds: number;
}

export interface ReviewMeta {
  service: string;
  version: string;
  states: string[];
  document: string;
  golden_count: number;
  next_pair_id: string;
  counts: Record<string, number>;
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
