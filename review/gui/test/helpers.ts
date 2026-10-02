/**
 * Fixtures and fakes.
 *
 * The submission fixture is a real one: the shape was taken from what the
 * agent API actually captures, so a change to the staging contract shows up
 * here as a failing test rather than as a review screen that renders empty.
 */

import { vi } from "vitest";

import type { Client } from "../src/api/client";
import type {
  DraftModel,
  FixResultModel,
  GoldenSet,
  PreviewModel,
  PromotionModel,
  ReviewMeta,
  SubmissionModel,
  UndoModel,
  ValidationModel,
  WithdrawalModel,
} from "../src/api/types";

export function makeSubmission(overrides: Partial<SubmissionModel> = {}): SubmissionModel {
  return {
    id: "sub-1",
    job_id: "job-abc",
    verdict: "yes",
    question: "What were total net sales for Produce in FY2025?",
    sql_code: "SELECT sum(net_sales_amt) FROM fact_pos_retail_sales;",
    answer: "Produce took $719,279.97.",
    narrative: "Produce took $719,279.97 in net sales across FY2025.",
    intent: "aggregate",
    tables: "fact_pos_retail_sales, dim_product",
    row_count: 1,
    columns: ["net_sales"],
    comment: "",
    agent_version: "4.2.0",
    submitted_at: "2026-09-24T10:00:00Z",
    state: "pending",
    reviewer: "",
    review_note: "",
    reviewed_at: null,
    draft: {},
    promoted_pair_id: null,
    ...overrides,
  };
}

export function makeDraft(overrides: Partial<DraftModel> = {}): DraftModel {
  return {
    title: "Total net sales for a department in one fiscal year",
    question: "What were total net sales for Produce in FY2025?",
    tables: "fact_pos_retail_sales, dim_product",
    keywords: "net sales, produce, department, fiscal year",
    reasoning_target: "Filtering a fact table by a dimension attribute across a fiscal year.",
    sql_code: "SELECT sum(net_sales_amt) FROM fact_pos_retail_sales;",
    result: "One row, one column.",
    translation_note: "",
    suite: "",
    extra_meta: {},
    ...overrides,
  };
}

export function makeMeta(overrides: Partial<ReviewMeta> = {}): ReviewMeta {
  return {
    service: "nl2sql-review",
    version: "4.3.0",
    states: ["pending", "accepted", "rejected", "promoted", "corrected"],
    document: "/app/context_questions/translated_questions.md",
    golden_count: 45,
    next_pair_id: "Q46",
    counts: { pending: 1, accepted: 0, rejected: 0, promoted: 0, corrected: 0 },
    verdicts: ["yes", "no", "incomplete"],
    counts_by_verdict: { yes: { pending: 1 }, no: { pending: 2 }, incomplete: { pending: 3 } },
    fixes: { corrections: 4, completions: 5 },
    reload_context: true,
    reload_vectors: true,
    limits: {
      reload_timeout_seconds: 600,
      validate_timeout_ms: 30000,
      validate_max_rows: 200,
    },
    authentication: "bearer",
    warnings: [],
    ...overrides,
  };
}

export function makePreview(overrides: Partial<PreviewModel> = {}): PreviewModel {
  return {
    pair_id: "Q46",
    markdown: "## Q46 - Total net sales\n\n```meta\nchunk_id: eval:q46\n```\n",
    valid: true,
    problems: [],
    suite_in_force: "Suite 25 - Brand loyalty and market penetration",
    ...overrides,
  };
}

export function makePromotion(overrides: Partial<PromotionModel> = {}): PromotionModel {
  return {
    pair_id: "Q46",
    chunk_id: "eval:q46",
    suite: "Suite 25 - Brand loyalty and market penetration",
    title: "Total net sales",
    markdown: "## Q46 - Total net sales\n",
    document: "/app/context_questions/translated_questions.md",
    backup: "/app/context_questions/translated_questions.md.bak",
    pairs_before: 45,
    pairs_after: 46,
    reloaded: true,
    steps: [
      { name: "load_golden_pairs", ran: true, ok: true, detail: "46 rows written" },
      { name: "embed_golden_pairs", ran: true, ok: true, detail: "1 pair embedded" },
    ],
    ...overrides,
  };
}

export function makeGolden(overrides: Partial<GoldenSet> = {}): GoldenSet {
  return {
    pairs: [],
    count: 45,
    document: "/app/context_questions/translated_questions.md",
    next_pair_id: "Q46",
    suite_in_force: "Suite 25 - Brand loyalty and market penetration",
    error: null,
    ...overrides,
  };
}

export function makeValidation(overrides: Partial<ValidationModel> = {}): ValidationModel {
  return {
    sql: "SELECT sku_id, product_name FROM dim_product",
    valid: true,
    problems: [],
    warnings: [],
    columns: ["sku_id", "product_name"],
    rows: [["SKU1", "Whole Milk"], ["SKU2", null]],
    row_count: 2,
    truncated: false,
    plan_cost: 4.5,
    elapsed_ms: 12.5,
    ...overrides,
  };
}

export function makeFixResult(overrides: Partial<FixResultModel> = {}): FixResultModel {
  const submission = makeSubmission({ verdict: "no", state: "corrected", promoted_pair_id: "W0001" });
  return {
    kind: "corrections",
    fix: {
      fix_id: "W0001",
      submission_id: submission.id,
      job_id: submission.job_id,
      question: submission.question,
      incorrect_sql: submission.sql_code,
      incorrect_answer: submission.answer,
      incorrect_columns: submission.columns,
      incorrect_row_count: submission.row_count,
      corrected_sql: "SELECT sku_id, product_name FROM dim_product",
      corrected_columns: ["sku_id", "product_name"],
      corrected_rows: [["SKU1", "Whole Milk"]],
      corrected_row_count: 1,
      corrected_truncated: false,
      plan_cost: 4.5,
      user_comment: "",
      reviewer: "ada",
      review_note: "",
      agent_version: "5.0.0",
      created_at: "2026-09-26T10:00:00Z",
      embedded: true,
    },
    validation: makeValidation(),
    submission,
    embedded: true,
    embed_detail: "embedded 1; 0 still to embed",
    ...overrides,
  };
}

/** A client whose every method is a spy, resolved with sensible defaults. */
export function fakeClient(overrides: Partial<Client> = {}): Client {
  const submission = makeSubmission();
  return {
    meta: vi.fn().mockResolvedValue(makeMeta()),
    readiness: vi.fn().mockResolvedValue({ ready: true, checks: {}, warnings: [] }),
    submissions: vi.fn().mockResolvedValue({
      submissions: [submission],
      count: 1,
      counts: { pending: 1, accepted: 0, rejected: 0, promoted: 0, corrected: 0 },
      counts_by_verdict: {
        yes: { pending: 1, accepted: 0, rejected: 0, promoted: 0, corrected: 0 },
      },
    }),
    submission: vi.fn().mockResolvedValue({ ...submission, draft: makeDraft() }),
    review: vi.fn().mockResolvedValue({ ...submission, state: "accepted" }),
    preview: vi.fn().mockResolvedValue(makePreview()),
    promote: vi.fn().mockResolvedValue(makePromotion()),
    golden: vi.fn().mockResolvedValue(makeGolden()),
    promotions: vi.fn().mockResolvedValue({ promotions: [], count: 0 }),
    validate: vi.fn().mockResolvedValue(makeValidation()),
    fix: vi.fn().mockResolvedValue(makeFixResult()),
    fixes: vi.fn().mockResolvedValue({ kind: "corrections", fixes: [], count: 0 }),
    reopen: vi.fn().mockResolvedValue(makeUndo()),
    remove: vi.fn().mockResolvedValue(makeUndo({ action: "deleted" })),
    ...overrides,
  };
}

/** A promoted pair taken back out, as the service reports it. */
export function makeWithdrawal(overrides: Partial<WithdrawalModel> = {}): WithdrawalModel {
  return {
    kind: "golden",
    id: "Q46",
    found: true,
    document: "/app/context_questions/translated_questions.md",
    backup: "/app/context_questions/translated_questions.md.bak",
    pairs_before: 46,
    pairs_after: 45,
    reloaded: true,
    steps: [
      { name: "load_golden_pairs", ran: true, ok: true, detail: "45 rows written, 1 removed" },
      { name: "embed_golden_pairs", ran: true, ok: true, detail: "1 removed" },
    ],
    ...overrides,
  };
}

export function makeUndo(overrides: Partial<UndoModel> = {}): UndoModel {
  return {
    action: "reopened",
    submission: makeSubmission(),
    withdrawn: makeWithdrawal(),
    ...overrides,
  };
}
