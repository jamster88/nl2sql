/**
 * The HTTP client. Fourteen calls and an error class.
 *
 * Deliberately thin, like the web GUI's: the API is already a good client
 * interface, and wrapping it in a layer with opinions of its own would only
 * add a second place for a bug to live. The one thing worth centralising is
 * the error envelope -- every failure comes back as
 * `{"error": {"code", "message"}}` -- because code that unwraps that at each
 * call site eventually forgets to somewhere.
 *
 * `promote`, `fix`, `reopen` and `remove` are the only calls here with
 * consequences outside the staging database -- the first writes the golden
 * question set, the second a corrections or completions store, and the last
 * two take back out whatever a submission had put into either -- and none of
 * them is a field on a PATCH. That is not decoration: it is what stops a form
 * that saves as you type from doing any of them. `validate` is a POST too,
 * but it only runs the query.
 */

import type {
  ApiErrorBody,
  DraftModel,
  FixKind,
  FixList,
  FixResultModel,
  GoldenSet,
  PreviewModel,
  PromotionList,
  PromotionModel,
  Readiness,
  ReviewMeta,
  ReviewRequest,
  State,
  SubmissionList,
  SubmissionModel,
  UndoModel,
  ValidationModel,
  Verdict,
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
  /** For a deployment that points a browser straight at the service. */
  token?: string;
  fetch?: typeof fetch;
}

export interface ListOptions {
  state?: State | undefined;
  verdict?: Verdict | undefined;
  limit?: number | undefined;
  offset?: number | undefined;
  signal?: AbortSignal | undefined;
}

export interface Client {
  meta(signal?: AbortSignal): Promise<ReviewMeta>;
  readiness(signal?: AbortSignal): Promise<Readiness>;
  submissions(options?: ListOptions): Promise<SubmissionList>;
  submission(id: string, signal?: AbortSignal): Promise<SubmissionModel>;
  review(id: string, body: ReviewRequest, signal?: AbortSignal): Promise<SubmissionModel>;
  preview(id: string, draft: DraftModel, signal?: AbortSignal): Promise<PreviewModel>;
  /** Writes the question document. The one call that is not undoable here. */
  promote(id: string, draft: DraftModel, reviewer?: string, signal?: AbortSignal): Promise<PromotionModel>;
  golden(signal?: AbortSignal): Promise<GoldenSet>;
  promotions(limit?: number, signal?: AbortSignal): Promise<PromotionList>;
  /** Runs the SQL against the live retail database. Stores nothing. */
  validate(id: string, sql: string, signal?: AbortSignal): Promise<ValidationModel>;
  /** Validates again, server side, then stores the fix. Not undoable here. */
  fix(id: string, sql: string, reviewer?: string, note?: string, signal?: AbortSignal): Promise<FixResultModel>;
  fixes(kind: FixKind, limit?: number, signal?: AbortSignal): Promise<FixList>;
  /** Back to pending, taking a promoted pair or a stored fix back out first. */
  reopen(id: string, signal?: AbortSignal): Promise<UndoModel>;
  /** Gone for good, with whatever it put into the golden set or a fix store. */
  remove(id: string, signal?: AbortSignal): Promise<UndoModel>;
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

  function url(path: string, query?: Record<string, string | number | undefined>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined) search.set(key, String(value));
    }
    const suffix = search.toString();
    return `${base}${path}${suffix ? `?${suffix}` : ""}`;
  }

  async function request<T>(
    path: string,
    init: RequestInit & { query?: Record<string, string | number | undefined> } = {},
  ): Promise<T> {
    const { query, ...rest } = init;
    let response: Response;
    try {
      response = await doFetch(url(path, query), rest);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      throw new ApiError(0, "unreachable", `cannot reach the review service: ${String(cause)}`);
    }

    if (response.status === 204) return undefined as T;

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      // A session that ended while the page was open: the sign-in gate
      // listens for this and asks for a sign-in again.
      if (response.status === 401) notifyUnauthorized();
      if (isErrorBody(body)) {
        throw new ApiError(response.status, body.error.code, body.error.message, body.error.detail);
      }
      // `/readyz` answers 503 with a readiness document rather than an error
      // envelope, because what is wrong is the interesting part.
      if (path === "/readyz" && body !== null) return body as T;
      throw new ApiError(response.status, "error", `HTTP ${response.status}`);
    }
    return body as T;
  }

  function id(value: string): string {
    return encodeURIComponent(value);
  }

  return {
    meta: (signal) => request<ReviewMeta>("/v1/meta", { signal: signal ?? null, headers: headers() }),

    readiness: (signal) =>
      request<Readiness>("/readyz", { signal: signal ?? null, headers: headers() }),

    submissions: (opts = {}) =>
      request<SubmissionList>("/v1/submissions", {
        headers: headers(),
        query: {
          state: opts.state,
          verdict: opts.verdict,
          limit: opts.limit,
          offset: opts.offset,
        },
        signal: opts.signal ?? null,
      }),

    submission: (value, signal) =>
      request<SubmissionModel>(`/v1/submissions/${id(value)}`, {
        headers: headers(),
        signal: signal ?? null,
      }),

    review: (value, body, signal) =>
      request<SubmissionModel>(`/v1/submissions/${id(value)}`, {
        method: "PATCH",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
        signal: signal ?? null,
      }),

    preview: (value, draft, signal) =>
      request<PreviewModel>(`/v1/submissions/${id(value)}/preview`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ draft }),
        signal: signal ?? null,
      }),

    promote: (value, draft, reviewer, signal) =>
      request<PromotionModel>(`/v1/submissions/${id(value)}/promote`, {
        method: "POST",
        headers: headers({
          "Content-Type": "application/json",
          ...(reviewer ? { "X-Reviewer": reviewer } : {}),
        }),
        body: JSON.stringify({ draft }),
        signal: signal ?? null,
      }),

    golden: (signal) =>
      request<GoldenSet>("/v1/golden", { headers: headers(), signal: signal ?? null }),

    promotions: (limit = 50, signal) =>
      request<PromotionList>("/v1/promotions", {
        headers: headers(),
        query: { limit },
        signal: signal ?? null,
      }),

    validate: (value, sql, signal) =>
      request<ValidationModel>(`/v1/submissions/${id(value)}/validate`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ sql }),
        signal: signal ?? null,
      }),

    fix: (value, sql, reviewer, note = "", signal) =>
      request<FixResultModel>(`/v1/submissions/${id(value)}/fix`, {
        method: "POST",
        headers: headers({
          "Content-Type": "application/json",
          ...(reviewer ? { "X-Reviewer": reviewer } : {}),
        }),
        body: JSON.stringify({ sql, review_note: note }),
        signal: signal ?? null,
      }),

    fixes: (kind, limit = 50, signal) =>
      request<FixList>(`/v1/fixes/${kind}`, {
        headers: headers(),
        query: { limit },
        signal: signal ?? null,
      }),

    reopen: (value, signal) =>
      request<UndoModel>(`/v1/submissions/${id(value)}/reopen`, {
        method: "POST",
        headers: headers(),
        signal: signal ?? null,
      }),

    remove: (value, signal) =>
      request<UndoModel>(`/v1/submissions/${id(value)}`, {
        method: "DELETE",
        headers: headers(),
        signal: signal ?? null,
      }),
  };
}
