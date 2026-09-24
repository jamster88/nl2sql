/**
 * The HTTP client. Four calls and an error class.
 *
 * Deliberately thin: the API is already a good client interface, so wrapping
 * it in a layer with opinions of its own would only add a second place for a
 * bug to live. The one thing worth centralising is the error envelope --
 * every failure comes back as `{"error": {"code", "message"}}`, and code that
 * has to unwrap that at each call site eventually forgets to somewhere.
 */

import type {
  ApiErrorBody,
  AskRequest,
  FeedbackModel,
  FeedbackRequest,
  Job,
  JobList,
  Meta,
  Readiness,
  Verdict,
} from "./types";

/**
 * A failure the server described.
 *
 * `code` is the stable, machine-readable half of the envelope and is what to
 * branch on; `message` is written for a person and may be reworded.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** True when retrying the same request might work. */
  get retryable(): boolean {
    return this.status === 503 || this.status === 0;
  }
}

export interface ClientOptions {
  /**
   * Where the API is. Empty means same origin, which is the normal case:
   * both the dev server and the production image proxy `/v1` to it, so the
   * browser never learns the API's real address or its token.
   */
  baseUrl?: string;
  /**
   * A token, for the deployment that points a browser straight at the API
   * instead. Prefer the proxy -- a token in a browser is a token in a
   * bookmark, a screenshot and a support ticket.
   */
  token?: string;
  /** Injectable for tests; defaults to the global. */
  fetch?: typeof fetch;
}

/** What a caller does with a job that is still running. */
export interface AskOptions {
  /** Seconds to let the server hold the connection. Omit for the async flow. */
  wait?: number;
  /** Opaque pairs echoed back on the job, for correlating with a UI turn. */
  metadata?: Record<string, string>;
  signal?: AbortSignal;
}

export interface Client {
  meta(signal?: AbortSignal): Promise<Meta>;
  readiness(signal?: AbortSignal): Promise<Readiness>;
  ask(question: string, options?: AskOptions): Promise<Job>;
  job(id: string, options?: { wait?: number; signal?: AbortSignal }): Promise<Job>;
  jobs(limit?: number, signal?: AbortSignal): Promise<JobList>;
  cancel(id: string, signal?: AbortSignal): Promise<void>;
  /**
   * Record a verdict on an answer.
   *
   * The job id is the whole address: what the answer *was* is read from the
   * job by the server, so this call carries an opinion and nothing else.
   */
  submitFeedback(id: string, verdict: Verdict, comment?: string, signal?: AbortSignal): Promise<FeedbackModel>;
  /** Take a verdict back, for the misclick. */
  withdrawFeedback(id: string, signal?: AbortSignal): Promise<void>;
  /** The URL an `EventSource` should open for this job's progress. */
  eventsUrl(job: Job): string;
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

  async function request<T>(path: string, init: RequestInit & { query?: Record<string, string | number | undefined> } = {}): Promise<T> {
    const { query, ...rest } = init;
    let response: Response;
    try {
      response = await doFetch(url(path, query), rest);
    } catch (cause) {
      // A network-level failure has no envelope to read, so it is given the
      // same shape as one: the UI shows every failure the same way.
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      throw new ApiError(0, "unreachable", `cannot reach the API: ${String(cause)}`);
    }

    if (response.status === 204) return undefined as T;

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
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

  return {
    meta: (signal) => request<Meta>("/v1/meta", { signal: signal ?? null, headers: headers() }),

    readiness: (signal) => request<Readiness>("/readyz", { signal: signal ?? null, headers: headers() }),

    ask(question, opts = {}) {
      const payload: AskRequest = { question };
      if (opts.metadata) payload.metadata = opts.metadata;
      return request<Job>("/v1/questions", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
        query: { wait: opts.wait },
        signal: opts.signal ?? null,
      });
    },

    job: (id, opts = {}) =>
      request<Job>(`/v1/questions/${encodeURIComponent(id)}`, {
        headers: headers(),
        query: { wait: opts.wait },
        signal: opts.signal ?? null,
      }),

    jobs: (limit = 50, signal) =>
      request<JobList>("/v1/questions", { headers: headers(), query: { limit }, signal: signal ?? null }),

    cancel: (id, signal) =>
      request<void>(`/v1/questions/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: headers(),
        signal: signal ?? null,
      }),

    submitFeedback(id, verdict, comment = "", signal) {
      const payload: FeedbackRequest = { verdict, comment };
      return request<FeedbackModel>(`/v1/questions/${encodeURIComponent(id)}/feedback`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
        signal: signal ?? null,
      });
    },

    withdrawFeedback: (id, signal) =>
      request<void>(`/v1/questions/${encodeURIComponent(id)}/feedback`, {
        method: "DELETE",
        headers: headers(),
        signal: signal ?? null,
      }),

    eventsUrl(job) {
      // `EventSource` cannot set headers, which is why the API accepts the
      // token in the query string for this one route -- and why a query
      // string is the wrong place for a token everywhere else, since it is
      // what ends up in an access log. With the proxy in front there is no
      // token here at all.
      return url(job.links.events, options.token ? { access_token: options.token } : {});
    },
  };
}
