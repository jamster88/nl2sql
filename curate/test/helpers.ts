/**
 * Fixtures and a fake client.
 *
 * The fixtures are shaped like what the review service sends -- the snippet
 * is the starter document's own S19 -- so a change to the contract shows up
 * here as a failing test rather than as a page that renders empty.
 */

import { vi } from "vitest";

import type { Client } from "../src/api/client";
import type {
  CuratedFixResultModel,
  FixModel,
  FixRemovalModel,
  GoldenPairModel,
  GoldenRemovalModel,
  GoldenResultModel,
  GoldenSet,
  PreviewModel,
  ReviewMeta,
  SnippetModel,
  SnippetPreviewModel,
  SnippetResultModel,
  SnippetSet,
  SnippetValidationModel,
  SubmissionModel,
  ValidationModel,
} from "../src/api/types";

export function makeMeta(overrides: Partial<ReviewMeta> = {}): ReviewMeta {
  return {
    service: "nl2sql-review",
    version: "6.2.0",
    states: ["pending", "accepted", "rejected", "promoted", "corrected"],
    document: "/app/context_questions/translated_questions.md",
    golden_count: 48,
    next_pair_id: "Q49",
    counts: {},
    verdicts: ["yes", "no", "incomplete"],
    counts_by_verdict: {},
    fixes: { corrections: 2, completions: 1 },
    reload_context: true,
    reload_vectors: true,
    limits: { reload_timeout_seconds: 600, validate_timeout_ms: 30000, validate_max_rows: 200 },
    authentication: "bearer",
    warnings: [],
    ...overrides,
  };
}

export function makeSnippet(overrides: Partial<SnippetModel> = {}): SnippetModel {
  return {
    snippet_id: "S19",
    chunk_id: "snippet:s19",
    name: "Net sales",
    kind: "measure",
    tables: ["fact_pos_retail_sales"],
    keywords: ["net sales", "revenue"],
    means: "Revenue actually collected, after register discounts.",
    applies_to: "fact_pos_retail_sales f",
    sql: "SUM(f.net_sales_amt)",
    note: "net = gross - markdown on every row.",
    ...overrides,
  };
}

export function makeSnippetSet(overrides: Partial<SnippetSet> = {}): SnippetSet {
  return {
    snippets: [
      makeSnippet({
        snippet_id: "S01",
        chunk_id: "snippet:s01",
        name: "Sales on the fiscal calendar",
        kind: "join",
        tables: ["fact_pos_retail_sales", "dim_date"],
        keywords: ["sales in fiscal year"],
        means: "Point-of-sale lines on the fiscal calendar.",
        sql: "JOIN dim_date d ON d.date_key = f.sales_date_key",
      }),
      makeSnippet(),
    ],
    count: 2,
    document: "/app/context_questions/sql_snippets.md",
    next_snippet_id: "S33",
    kinds: ["join", "filter", "measure", "dimension"],
    store: { reachable: true, snippets: 2, embedded: 2, current: true, detail: "2 snippets, all embedded, loaded from this document" },
    error: null,
    ...overrides,
  };
}

export function makeSnippetValidation(overrides: Partial<SnippetValidationModel> = {}): SnippetValidationModel {
  return {
    kind: "filter",
    valid: true,
    problems: [],
    warnings: [],
    probe_sql: "SELECT *\nFROM dim_date d\nWHERE d.is_holiday\nLIMIT 20",
    applies_to: "dim_date d",
    sql: "d.is_holiday",
    columns: ["date_key", "is_holiday"],
    rows: [[20240101, true]],
    rows_before: 731,
    rows_after: 32,
    tables: ["dim_date"],
    plan_cost: 12.5,
    elapsed_ms: 4.2,
    ...overrides,
  };
}

export function makeSnippetPreview(overrides: Partial<SnippetPreviewModel> = {}): SnippetPreviewModel {
  return { snippet_id: "S33", markdown: "## S33 - Holidays\n", valid: true, problems: [], ...overrides };
}

export function makeSnippetResult(overrides: Partial<SnippetResultModel> = {}): SnippetResultModel {
  return {
    action: "added",
    snippet_id: "S33",
    chunk_id: "snippet:s33",
    markdown: "## S33 - Holidays\n",
    document: "/app/context_questions/sql_snippets.md",
    backup: "/app/context_questions/sql_snippets.md.bak",
    snippets_before: 32,
    snippets_after: 33,
    reloaded: true,
    steps: [{ name: "load_snippets", ran: true, ok: true, detail: "33 rows written" }],
    validation: makeSnippetValidation(),
    ...overrides,
  };
}

export function makePair(overrides: Partial<GoldenPairModel> = {}): GoldenPairModel {
  return {
    pair_id: "Q01",
    chunk_id: "eval:q01",
    title: "Daily net sales for one store",
    suite: "Suite 1 - Basics",
    question: "What were net sales at STR001 on 2025-03-31?",
    tables: ["fact_pos_retail_sales", "dim_store"],
    keywords: ["net sales", "store"],
    reasoning_target: "The date column is sales_date_key.",
    sql_code: "SELECT 1",
    result: "One row.",
    submission_id: null,
    ...overrides,
  };
}

export function makeGolden(overrides: Partial<GoldenSet> = {}): GoldenSet {
  return {
    pairs: [makePair(), makePair({ pair_id: "Q47", title: "Promoted", question: "How many stores?", submission_id: "sub-1" })],
    count: 2,
    document: "/app/context_questions/translated_questions.md",
    next_pair_id: "Q49",
    suite_in_force: "Suite 26",
    error: null,
    ...overrides,
  };
}

export function makeValidation(overrides: Partial<ValidationModel> = {}): ValidationModel {
  return {
    sql: "SELECT 1",
    valid: true,
    problems: [],
    warnings: [],
    columns: ["n"],
    rows: [[1]],
    row_count: 1,
    truncated: false,
    plan_cost: 1.5,
    elapsed_ms: 2,
    ...overrides,
  };
}

export function makePreview(overrides: Partial<PreviewModel> = {}): PreviewModel {
  return { pair_id: "Q49", markdown: "## Q49 - New\n", valid: true, problems: [], suite_in_force: "Suite 26", ...overrides };
}

export function makeGoldenResult(overrides: Partial<GoldenResultModel["promotion"]> = {}): GoldenResultModel {
  return {
    promotion: {
      pair_id: "Q49",
      chunk_id: "eval:q49",
      suite: "Suite 26",
      title: "New",
      markdown: "## Q49 - New\n",
      document: "/app/context_questions/translated_questions.md",
      backup: "/app/context_questions/translated_questions.md.bak",
      pairs_before: 48,
      pairs_after: 49,
      reloaded: true,
      steps: [{ name: "load_golden_pairs", ran: true, ok: true, detail: "49 rows written" }],
      ...overrides,
    },
    validation: makeValidation(),
  };
}

export function makeSubmission(overrides: Partial<SubmissionModel> = {}): SubmissionModel {
  return {
    id: "sub-1",
    job_id: "job-1",
    verdict: "yes",
    question: "How many stores?",
    sql_code: "SELECT count(*) FROM dim_store",
    answer: "10",
    narrative: "",
    intent: "lookup",
    tables: "dim_store",
    row_count: 1,
    columns: ["count"],
    comment: "",
    agent_version: "6.2.0",
    submitted_at: null,
    state: "pending",
    reviewer: "",
    review_note: "",
    reviewed_at: null,
    draft: {},
    promoted_pair_id: null,
    ...overrides,
  };
}

export function makeGoldenRemoval(overrides: Partial<GoldenRemovalModel> = {}): GoldenRemovalModel {
  return {
    pair_id: "Q01",
    withdrawal: {
      kind: "golden",
      id: "Q01",
      found: true,
      document: "/app/context_questions/translated_questions.md",
      backup: "",
      pairs_before: 48,
      pairs_after: 47,
      reloaded: true,
      steps: [{ name: "load_golden_pairs", ran: true, ok: true, detail: "47 rows written" }],
    },
    submission: null,
    ...overrides,
  };
}

export function makeFix(overrides: Partial<FixModel> = {}): FixModel {
  return {
    fix_id: "W0001",
    submission_id: null,
    job_id: "",
    question: "top 10 SKUs by net sales",
    incorrect_sql: "SELECT sku_id FROM dim_product",
    incorrect_answer: "",
    incorrect_columns: [],
    incorrect_row_count: 0,
    corrected_sql: "SELECT sku_id, product_name FROM dim_product",
    corrected_columns: ["sku_id", "product_name"],
    corrected_rows: [["SKU1", "Whole Milk"]],
    corrected_row_count: 1,
    corrected_truncated: false,
    plan_cost: 4.5,
    user_comment: "",
    reviewer: "ann",
    review_note: "names, not ids",
    agent_version: "",
    source: "curated",
    created_at: null,
    embedded: true,
    ...overrides,
  };
}

export function makeFixResult(overrides: Partial<CuratedFixResultModel> = {}): CuratedFixResultModel {
  return {
    kind: "corrections",
    fix: makeFix({ fix_id: "W0002" }),
    validation: makeValidation(),
    embedded: true,
    embed_detail: "embedded 1; 0 still to embed",
    ...overrides,
  };
}

export function makeFixRemoval(overrides: Partial<FixRemovalModel> = {}): FixRemovalModel {
  return { kind: "corrections", fix_id: "W0001", found: true, fix: makeFix(), submission: null, ...overrides };
}

/** A client whose every method is a spy, resolved with sensible defaults. */
export function fakeClient(overrides: Partial<Client> = {}): Client {
  return {
    meta: vi.fn().mockResolvedValue(makeMeta()),
    schema: vi.fn().mockResolvedValue({
      tables: [
        { name: "dim_date", columns: [{ name: "date_key", type: "integer" }, { name: "is_holiday", type: "boolean" }] },
        { name: "fact_pos_retail_sales", columns: [{ name: "net_sales_amt", type: "numeric" }] },
      ],
      error: null,
    }),
    snippets: vi.fn().mockResolvedValue(makeSnippetSet()),
    validateSnippet: vi.fn().mockResolvedValue(makeSnippetValidation()),
    previewSnippet: vi.fn().mockResolvedValue(makeSnippetPreview()),
    addSnippet: vi.fn().mockResolvedValue(makeSnippetResult()),
    changeSnippet: vi.fn().mockResolvedValue(makeSnippetResult({ action: "changed", snippet_id: "S19", snippets_after: 32 })),
    removeSnippet: vi.fn().mockResolvedValue(makeSnippetResult({ action: "removed", snippet_id: "S19", snippets_after: 31, validation: null })),
    golden: vi.fn().mockResolvedValue(makeGolden()),
    validateGolden: vi.fn().mockResolvedValue(makeValidation()),
    previewGolden: vi.fn().mockResolvedValue(makePreview()),
    addGolden: vi.fn().mockResolvedValue(makeGoldenResult()),
    removeGolden: vi.fn().mockResolvedValue(makeGoldenRemoval()),
    fixes: vi.fn().mockResolvedValue({ kind: "corrections", fixes: [makeFix()], count: 1 }),
    validateFix: vi.fn().mockResolvedValue(makeValidation()),
    addFix: vi.fn().mockResolvedValue(makeFixResult()),
    removeFix: vi.fn().mockResolvedValue(makeFixRemoval()),
    ...overrides,
  };
}
