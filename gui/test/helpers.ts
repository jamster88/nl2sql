/**
 * Fixtures and fakes.
 *
 * The job fixture is a real one: the shape was taken from the worked example
 * in `agent/API.md`, so a change to the wire contract that the documentation
 * follows shows up here as a failing test rather than as a GUI that renders
 * an empty panel against a live server.
 */

import { vi } from "vitest";

import type { Client } from "../src/api/client";
import type {
  Answer,
  Ensemble,
  EnsembleCandidate,
  EnsembleSettings,
  Job,
  Meta,
  ResultTable,
} from "../src/api/types";

export function makeResult(overrides: Partial<ResultTable> = {}): ResultTable {
  return {
    columns: ["department", "net_sales"],
    rows: [
      ["Produce", "719279.97"],
      ["Dairy & Eggs", "512004.10"],
      ["Bakery", "318220.55"],
    ],
    row_count: 3,
    truncated: false,
    ...overrides,
  };
}

export function makeAnswer(overrides: Partial<Answer> = {}): Answer {
  return {
    answer: "Produce led with $719,279.97 in net sales.",
    narrative: "Produce led with $719,279.97 in net sales, ahead of Dairy & Eggs.",
    sql: "SELECT department, sum(net_sales_amt) FROM fact_pos_retail_sales GROUP BY 1",
    verdict: "proceed",
    intent: "rank",
    clarification: null,
    tables: ["dim_product", "fact_pos_retail_sales"],
    literals: [
      { phrase: "produse", table: "dim_product", column: "department", value: "Produce", score: 0.81 },
    ],
    result: makeResult(),
    chart: { kind: "bar", x: "department", y: ["net_sales"], series: null },
    claims: [
      { text: "Produce led with $719,279.97.", value: 719279.97, cells: [[0, "net_sales"]], formula: null },
    ],
    audit: {
      passed: true,
      unsupported_claims: [],
      drop_reasons: [],
      missing_assumptions: [],
      semantic_issue: null,
    },
    plan_cost: 125767.4,
    attempts: 1,
    trace: [
      { node: "generate_sql", ms: 8123.4, model_calls: 1, detail: "3 tables", model: "", rung: "", route: "", hops: [] },
      { node: "execute", ms: 412, model_calls: 0, detail: "", model: "", rung: "", route: "", hops: [] },
    ],
    retrieval_errors: {},
    node_errors: {},
    ...overrides,
  };
}

/** One wording's run, as the wire carries it under the ensemble (7.0). */
export function makeCandidate(overrides: Partial<EnsembleCandidate> = {}): EnsembleCandidate {
  return {
    index: 0,
    wording: "Which department had the highest net sales in fiscal year 2025?",
    origin: "original",
    wave: 1,
    changed: "",
    outcome: "answered",
    admissible: true,
    reasons: [],
    sql: "SELECT department, sum(net_sales_amt) FROM fact_pos_retail_sales GROUP BY 1",
    signature: "Produce 719279.97",
    attempts: 1,
    group: 0,
    duration_ms: 31250,
    trace: [],
    ...overrides,
  };
}

/**
 * A question asked four ways: three runs agree, one answers differently.
 *
 * The shape is the one `agent/API.md` documents for `Answer.ensemble`, and
 * `tests/gui/test_gui_contract.py` holds every field of it to the server's.
 */
export function makeEnsemble(overrides: Partial<Ensemble> = {}): Ensemble {
  return {
    agreement: { admissible: 4, agreed: 3, total: 4, level: "majority", why: "3 of 4 agree", set_aside: 0 },
    line: "3 of 4 runs agreed; 1 answered differently.",
    chosen: 0,
    fused_from: [0, 1, 3],
    columns_fused: true,
    joined_columns: [],
    declined_columns: [],
    claims_added: 0,
    claims_dropped: 0,
    dissent: [{ group: 1, members: [2], signature: "Dairy & Eggs 512004.10", differs: "filters on banner_name" }],
    judged: {
      verdicts: [
        { group: 0, accepted: true, why: "" },
        { group: 1, accepted: true, why: "" },
      ],
      set_aside: [],
      overruled: false,
      instead_of: null,
      model: "judge-model",
      error: "",
    },
    candidates: [
      makeCandidate(),
      makeCandidate({
        index: 1,
        origin: "paraphrase",
        wording: "In fiscal 2025, which department sold the most, net?",
        changed: "the order of the clauses",
      }),
      makeCandidate({
        index: 2,
        origin: "paraphrase",
        wording: "Top department by net sales, FY2025?",
        changed: "shortened",
        sql: "SELECT department FROM fact_pos_retail_sales WHERE banner_name = 'x'",
        signature: "Dairy & Eggs 512004.10",
        group: 1,
        attempts: 3,
      }),
      makeCandidate({
        index: 3,
        origin: "paraphrase",
        wording: "Which department's net sales were highest in FY2025?",
        changed: "",
      }),
    ],
    discarded: [
      { index: 4, text: "Which store had the highest sales?", changed: "department to store", reason: "F2 literals" },
    ],
    parallel_calls: 1,
    ...overrides,
  };
}

/** The ensemble's eleven nodes, as `/v1/meta` names them by themselves. */
export const OUTER_NODES = [
  "screen",
  "refuse",
  "paraphrase",
  "screen_paraphrase",
  "plan_wave",
  "answer",
  "validate",
  "judge",
  "vote",
  "fuse",
  "deliver",
];

export function makeEnsembleSettings(overrides: Partial<EnsembleSettings> = {}): EnsembleSettings {
  return {
    enabled: true,
    paraphrases: 3,
    max_paraphrases: 10,
    waves: 2,
    parallel_calls: 1,
    judge: true,
    fuse_columns: true,
    nodes: OUTER_NODES,
    ...overrides,
  };
}

export function makeJob(overrides: Partial<Job> = {}): Job {
  const id = overrides.id ?? "job-1";
  return {
    id,
    status: "succeeded",
    question: "Which department had the highest net sales in fiscal year 2025?",
    metadata: {},
    created_at: "2026-09-22T10:00:00Z",
    started_at: "2026-09-22T10:00:00Z",
    finished_at: "2026-09-22T10:01:01Z",
    duration_ms: 61432.5,
    progress: [
      { seq: 1, step: "screen", label: "screening", detail: "", at: "2026-09-22T10:00:01Z" },
      { seq: 2, step: "generate_sql", label: "sql", detail: "3 tables", at: "2026-09-22T10:00:20Z" },
    ],
    answer: makeAnswer(),
    error: null,
    links: { self: `/v1/questions/${id}`, events: `/v1/questions/${id}/events` },
    ...overrides,
  };
}

export function makeMeta(overrides: Partial<Meta> = {}): Meta {
  return {
    service: "nl2sql-agent",
    version: "4.1.0",
    model: "qwen2.5-coder:32b",
    intents: ["aggregate", "compare", "rank", "trend"],
    tables: ["dim_product", "dim_store", "fact_pos_retail_sales"],
    scope: "Retail point-of-sale data for fiscal years 2024 and 2025.",
    limits: {
      max_rows: 1000,
      max_attempts: 3,
      max_plan_cost: 1e7,
      statement_timeout_ms: 30000,
      max_concurrency: 2,
      max_wait_seconds: 900,
      max_question_length: 2000,
      max_metadata_entries: 20,
    },
    pipeline: {
      supervisor: true,
      literals: true,
      narrate: true,
      audit: true,
      schema_retrieval: "hybrid",
      nodes: ["screen", "retrieve_schema", "generate_sql", "execute", "narrate"],
    },
    tls: { enabled: true, self_signed: true },
    authentication: "none",
    feedback: true,
    routing: {},
    ...overrides,
  };
}

/** A client whose every method is a spy, resolved with sensible defaults. */
export function fakeClient(overrides: Partial<Client> = {}): Client {
  const job = makeJob();
  return {
    meta: vi.fn().mockResolvedValue(makeMeta()),
    readiness: vi.fn().mockResolvedValue({ ready: true, checks: {}, warnings: [] }),
    ask: vi.fn().mockResolvedValue(makeJob({ id: job.id, status: "queued", answer: null, progress: [] })),
    job: vi.fn().mockResolvedValue(job),
    jobs: vi.fn().mockResolvedValue({ jobs: [job], count: 1 }),
    cancel: vi.fn().mockResolvedValue(undefined),
    submitFeedback: vi
      .fn()
      .mockResolvedValue({ id: "sub-1", job_id: job.id, verdict: "yes", comment: "", state: "pending" }),
    withdrawFeedback: vi.fn().mockResolvedValue(undefined),
    eventsUrl: vi.fn((target) => target.links.events),
    ...overrides,
  };
}

type Listener = (event: MessageEvent<string>) => void;

/**
 * A controllable `EventSource`.
 *
 * jsdom has none, and a fake that merely returns canned events would not
 * exercise the two paths that actually matter: resuming after a deliberate
 * close, and giving up on a stream that keeps failing. So this one records
 * every instance and every URL, and the tests drive it.
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private readonly listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  static last(): FakeEventSource {
    const found = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!found) throw new Error("no EventSource was opened");
    return found;
  }

  addEventListener(type: string, listener: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {
    this.closed = true;
  }

  /** Deliver one server event. */
  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }

  /** The transport dropped. */
  fail(): void {
    this.onerror?.(new Event("error"));
  }
}

/** The fake, typed as the real thing. Tests inject it; nothing ships it. */
export const fakeEventSource = FakeEventSource as unknown as typeof EventSource;

/** An in-memory `Storage`, for the feedback store. */
export function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

/** A `Storage` that throws on everything, like a locked-down browser. */
export function hostileStorage(): Storage {
  const boom = (): never => {
    throw new Error("site data is blocked");
  };
  return {
    length: 0,
    clear: boom,
    getItem: boom,
    key: boom,
    removeItem: boom,
    setItem: boom,
  };
}
