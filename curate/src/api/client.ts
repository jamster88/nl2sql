/**
 * The HTTP client: the review service's curation routes, and an error class.
 *
 * Thin, like the other interfaces' clients, with one thing centralised: the
 * error envelope -- every failure comes back as `{"error": {"code",
 * "message"}}` -- because code that unwraps that at each call site
 * eventually forgets to somewhere.
 *
 * The calls that write -- `addSnippet`, `changeSnippet`, `removeSnippet`,
 * `addGolden`, `removeGolden`, `addFix`, `removeFix` -- each run the loaders
 * or write a store, and none is a side effect of another. The `validate*`
 * calls run SQL against the live retail database and store nothing; the
 * service runs it again on every write regardless of what they said.
 */

import type {
  ApiErrorBody,
  CuratedFixRequest,
  CuratedFixResultModel,
  DraftModel,
  FixKind,
  FixList,
  FixRemovalModel,
  GoldenRemovalModel,
  GoldenResultModel,
  GoldenSet,
  PreviewModel,
  ReviewMeta,
  SchemaModel,
  SnippetDraftModel,
  SnippetPreviewModel,
  SnippetResultModel,
  SnippetSet,
  SnippetValidationModel,
  ValidationModel,
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

export interface Client {
  meta(): Promise<ReviewMeta>;
  schema(): Promise<SchemaModel>;

  snippets(): Promise<SnippetSet>;
  validateSnippet(draft: SnippetDraftModel): Promise<SnippetValidationModel>;
  previewSnippet(draft: SnippetDraftModel, snippetId?: string): Promise<SnippetPreviewModel>;
  addSnippet(draft: SnippetDraftModel): Promise<SnippetResultModel>;
  changeSnippet(snippetId: string, draft: SnippetDraftModel): Promise<SnippetResultModel>;
  removeSnippet(snippetId: string): Promise<SnippetResultModel>;

  golden(): Promise<GoldenSet>;
  validateGolden(sql: string): Promise<ValidationModel>;
  previewGolden(draft: DraftModel): Promise<PreviewModel>;
  addGolden(draft: DraftModel): Promise<GoldenResultModel>;
  removeGolden(pairId: string): Promise<GoldenRemovalModel>;

  fixes(kind: FixKind): Promise<FixList>;
  validateFix(kind: FixKind, sql: string, incorrectSql: string): Promise<ValidationModel>;
  addFix(kind: FixKind, body: CuratedFixRequest, reviewer?: string): Promise<CuratedFixResultModel>;
  removeFix(kind: FixKind, fixId: string): Promise<FixRemovalModel>;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  return typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string";
}

export function createClient(options: ClientOptions = {}): Client {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);

  function headers(extra: Record<string, string> = {}): Record<string, string> {
    const result: Record<string, string> = { Accept: "application/json", ...extra };
    if (options.token) result["Authorization"] = `Bearer ${options.token}`;
    return result;
  }

  async function request<T>(path: string, method = "GET", body?: unknown, extra?: Record<string, string>): Promise<T> {
    let response: Response;
    try {
      response = await doFetch(`${base}${path}`, {
        method,
        headers: headers(body === undefined ? extra : { "Content-Type": "application/json", ...extra }),
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (cause) {
      throw new ApiError(0, "unreachable", `cannot reach the review service: ${String(cause)}`);
    }
    const parsed: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      // A session that ended while the page was open: the sign-in gate
      // listens for this and asks for a sign-in again.
      if (response.status === 401) notifyUnauthorized();
      if (isErrorBody(parsed)) throw new ApiError(response.status, parsed.error.code, parsed.error.message);
      throw new ApiError(response.status, "error", `HTTP ${response.status}`);
    }
    return parsed as T;
  }

  const id = encodeURIComponent;

  return {
    meta: () => request("/v1/meta"),
    schema: () => request("/v1/schema"),

    snippets: () => request("/v1/snippets"),
    validateSnippet: (draft) => request("/v1/snippets/validate", "POST", { draft }),
    previewSnippet: (draft, snippetId) =>
      request("/v1/snippets/preview", "POST", { draft, snippet_id: snippetId ?? null }),
    addSnippet: (draft) => request("/v1/snippets", "POST", { draft }),
    changeSnippet: (snippetId, draft) => request(`/v1/snippets/${id(snippetId)}`, "PUT", { draft }),
    removeSnippet: (snippetId) => request(`/v1/snippets/${id(snippetId)}`, "DELETE"),

    golden: () => request("/v1/golden"),
    validateGolden: (sql) => request("/v1/golden/validate", "POST", { sql }),
    previewGolden: (draft) => request("/v1/golden/preview", "POST", { draft }),
    addGolden: (draft) => request("/v1/golden", "POST", { draft }),
    removeGolden: (pairId) => request(`/v1/golden/${id(pairId)}`, "DELETE"),

    fixes: (kind) => request(`/v1/fixes/${kind}`),
    validateFix: (kind, sql, incorrectSql) =>
      request(`/v1/fixes/${kind}/validate`, "POST", { sql, incorrect_sql: incorrectSql }),
    addFix: (kind, body, reviewer) =>
      request(`/v1/fixes/${kind}`, "POST", body, reviewer ? { "X-Reviewer": reviewer } : undefined),
    removeFix: (kind, fixId) => request(`/v1/fixes/${kind}/${id(fixId)}`, "DELETE"),
  };
}
