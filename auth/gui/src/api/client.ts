/**
 * The HTTP client for the directory's routes. Thin, like the other pages':
 * the one thing centralised is the error envelope, so every failure reaches
 * the page as an `ApiError` with the service's code and words.
 *
 * Same origin: this page's nginx sends `/directory/` and `/auth/` to the
 * auth service, and the session cookie goes with every call.
 */

import { ApiError } from "@nl2sql/web/api/errors";
import { notifyUnauthorized } from "@nl2sql/web/auth/session";
import type {
  ApiErrorBody,
  DirectoryMeta,
  GroupList,
  ImportResult,
  NewPerson,
  Person,
  PersonChange,
  PersonList,
  SyncReport,
} from "./types";

/**
 * A failure the service described -- the error every page throws for one
 * (`web/src/api/errors.ts`): branch on `code`, show `message`.
 */
export { ApiError };

export interface ClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
}

export interface Client {
  meta(): Promise<DirectoryMeta>;
  people(): Promise<PersonList>;
  person(uid: string): Promise<Person>;
  add(person: NewPerson): Promise<Person>;
  change(uid: string, change: PersonChange): Promise<Person>;
  remove(uid: string): Promise<void>;
  setPassword(uid: string, password: string): Promise<void>;
  unlock(uid: string): Promise<void>;
  groups(): Promise<GroupList>;
  importFile(filename: string, content: string): Promise<ImportResult>;
  sync(): Promise<SyncReport>;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  return typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string";
}

export function createClient(options: ClientOptions = {}): Client {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);

  async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await doFetch(`${base}/directory/v1${path}`, {
        method,
        credentials: "same-origin",
        headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (cause) {
      throw new ApiError(0, "unreachable", `cannot reach the directory service: ${String(cause)}`);
    }
    if (response.status === 204) return undefined as T;
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

  const person = (uid: string) => `/people/${encodeURIComponent(uid)}`;

  return {
    meta: () => request<DirectoryMeta>("/meta"),
    people: () => request<PersonList>("/people"),
    person: (uid) => request<Person>(person(uid)),
    add: (body) => request<Person>("/people", "POST", body),
    change: (uid, body) => request<Person>(person(uid), "PATCH", body),
    remove: (uid) => request<void>(person(uid), "DELETE"),
    setPassword: (uid, password) => request<void>(`${person(uid)}/password`, "POST", { password }),
    unlock: (uid) => request<void>(`${person(uid)}/unlock`, "POST"),
    groups: () => request<GroupList>("/groups"),
    importFile: (filename, content) => request<ImportResult>("/import", "POST", { filename, content }),
    sync: () => request<SyncReport>("/sync", "POST"),
  };
}
