/**
 * The HTTP client. Four calls and an error class.
 *
 * Thin for the same reason the other two interfaces' clients are: the
 * console's routes are already a good client interface, and the one thing
 * worth centralising is the error envelope -- `{"error": {"code",
 * "message"}}` -- because unwrapping it at each call site is how one of them
 * eventually forgets to.
 *
 * `query` is a POST but changes nothing: every statement runs inside a
 * read-only transaction that is rolled back. It is a POST because a query
 * does not belong in a URL, where it would be kept in every log and history
 * it passed through.
 */

import type {
  ApiErrorBody,
  ConsoleMeta,
  Mode,
  PromptModel,
  QueryResult,
  SchemaModel,
} from "./types";

import { ApiError } from "@nl2sql/web/api/errors";
import { notifyUnauthorized } from "@nl2sql/web/auth/session";

/**
 * A failure the service described -- the error every page throws for one
 * (`web/src/api/errors.ts`): branch on `code`, show `message`.
 */
export { ApiError };

export interface ClientOptions {
  /** Empty means same origin, which is the normal case: nginx proxies /v1. */
  baseUrl?: string;
  /** For a deployment that points a browser straight at the console. */
  token?: string;
  fetch?: typeof fetch;
}

export interface Client {
  meta(signal?: AbortSignal): Promise<ConsoleMeta>;
  schema(signal?: AbortSignal): Promise<SchemaModel>;
  prompt(table: string, signal?: AbortSignal): Promise<PromptModel>;
  query(sql: string, mode?: Mode, signal?: AbortSignal): Promise<QueryResult>;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  return typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string";
}

export function createClient(options: ClientOptions = {}): Client {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);

  function headers(extra?: Record<string, string>): Record<string, string> {
    const result: Record<string, string> = { Accept: "application/json", ...extra };
    if (options.token) result["Authorization"] = `Bearer ${options.token}`;
    return result;
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await doFetch(`${base}${path}`, init);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      throw new ApiError(0, "unreachable", `cannot reach the console: ${String(cause)}`);
    }

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      // A session that ended while the page was open: the sign-in gate
      // listens for this and asks for a sign-in again.
      if (response.status === 401) notifyUnauthorized();
      if (isErrorBody(body)) throw new ApiError(response.status, body.error.code, body.error.message);
      throw new ApiError(response.status, "error", `HTTP ${response.status}`);
    }
    return body as T;
  }

  return {
    meta: (signal) => request<ConsoleMeta>("/v1/meta", { headers: headers(), signal: signal ?? null }),

    schema: (signal) => request<SchemaModel>("/v1/schema", { headers: headers(), signal: signal ?? null }),

    prompt: (table, signal) =>
      request<PromptModel>(`/v1/schema/${encodeURIComponent(table)}/prompt`, {
        headers: headers(),
        signal: signal ?? null,
      }),

    query: (sql, mode = "run", signal) =>
      request<QueryResult>("/v1/query", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ sql, mode }),
        signal: signal ?? null,
      }),
  };
}
