/**
 * Fixtures and fakes.
 *
 * The shapes are the console's real ones, taken from what it returned
 * against the retail database -- so a change to the wire contract shows up
 * here as a failing test rather than as a panel that renders empty.
 */

import { vi } from "vitest";

import type { Client } from "../src/api/client";
import type { ConsoleMeta, PromptModel, QueryResult, SchemaModel, TableModel } from "../src/api/types";

export function makeMeta(overrides: Partial<ConsoleMeta> = {}): ConsoleMeta {
  return {
    service: "nl2sql-console",
    version: "5.3.0",
    database: "nl2sql_retail",
    role: "nl2sql_reader",
    server_version: "18.6 (Debian 18.6-1.pgdg13+2)",
    read_only: true,
    db_schema: "public",
    tables: 19,
    limits: {
      statement_timeout_ms: 30000,
      max_plan_cost: 1000000,
      agent_max_rows: 50,
      max_rows: 1000,
      sample_rows: 3,
      max_sql_length: 20000,
    },
    authentication: "none",
    tls: { enabled: true, self_signed: true },
    warnings: [],
    ...overrides,
  };
}

export function makeTable(overrides: Partial<TableModel> = {}): TableModel {
  return {
    name: "dim_store",
    comment: "One row per store.",
    approx_rows: 40,
    columns: [
      { name: "store_key", data_type: "integer", not_null: true, comment: "Surrogate key." },
      { name: "store_name", data_type: "character varying(150)", not_null: true, comment: null },
      { name: "city", data_type: "character varying(100)", not_null: false, comment: null },
    ],
    constraints: ["PRIMARY KEY (store_key)"],
    ...overrides,
  };
}

export function makeSchema(tables: TableModel[] = [makeTable()]): SchemaModel {
  return { db_schema: "public", tables };
}

export function makeResult(overrides: Partial<QueryResult> = {}): QueryResult {
  return {
    sql: "SELECT store_key, store_name, city FROM dim_store",
    mode: "run",
    executed: true,
    agent: { accepted: true, stage: null, issues: [], notes: [] },
    columns: [
      { name: "store_key", data_type: "integer" },
      { name: "store_name", data_type: "character varying" },
      { name: "city", data_type: "character varying" },
    ],
    rows: [
      [1, "Thrift & Table - Ashland", "Ashland"],
      [2, "Corner Fresh Grocers - Salem", null],
    ],
    row_count: 2,
    truncated: false,
    max_rows: 1000,
    plan: [{ Plan: { "Node Type": "Seq Scan", "Relation Name": "dim_store", "Startup Cost": 0, "Total Cost": 1.4, "Plan Rows": 40 } }],
    plan_cost: 1.4,
    max_plan_cost: 1000000,
    error: null,
    elapsed_ms: 4.2,
    ...overrides,
  };
}

export function makePrompt(overrides: Partial<PromptModel> = {}): PromptModel {
  return {
    table: "dim_store",
    sample_rows: 3,
    text: "=== dim_store ===\ncolumns:\n  store_key (integer, NOT NULL)",
    ...overrides,
  };
}

export type FakeClient = { [K in keyof Client]: ReturnType<typeof vi.fn> } & Client;

export function fakeClient(overrides: Partial<Client> = {}): FakeClient {
  return {
    meta: vi.fn(async () => makeMeta()),
    schema: vi.fn(async () => makeSchema()),
    prompt: vi.fn(async () => makePrompt()),
    query: vi.fn(async () => makeResult()),
    ...overrides,
  } as FakeClient;
}

/** A Response the client can read, as fetch would hand it back. */
export function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
