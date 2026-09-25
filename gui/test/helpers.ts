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
import type { Answer, Job, Meta, ResultTable } from "../src/api/types";

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
      redactions: [],
      semantic_issue: null,
    },
    plan_cost: 125767.4,
    attempts: 1,
    trace: [
      { node: "generate_sql", ms: 8123.4, model_calls: 1, detail: "3 tables" },
      { node: "execute", ms: 412, model_calls: 0, detail: "" },
    ],
    retrieval_errors: {},
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

/** A `fetch` that answers one canned response. */
export function fakeFetch(
  status: number,
  body: unknown,
  init: { json?: boolean } = {},
): typeof fetch {
  return vi.fn(async () =>
    new Response(init.json === false ? "not json" : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}
