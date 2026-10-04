/**
 * Signing in, through the auth service this page's proxy sends `/auth/` to.
 *
 * The session itself is a cookie the auth service sets -- HttpOnly, so no
 * script here can read it, and host-wide, so one sign-in covers every page of
 * this stack served from the same host. What this module does is ask who is
 * signed in, sign someone in or out, and change their password.
 *
 * Every page of the stack carries its own copy of this file: each is its own
 * npm project, built into its own image, by design.
 */

/** Who is signed in. `expires_at` is seconds since the epoch. */
export interface Session {
  user: string;
  name: string;
  roles: string[];
  kind: string;
  expires_at: number;
}

/** What the sign-in form needs to know before anyone has signed in. */
export interface AuthMeta {
  version: string;
  mode: string;
  /** False for a replica, whose people (and passwords) live elsewhere. */
  directory_editable: boolean;
  session_hours: number;
  min_password_length: number;
}

/** A sign-in, sign-out or password change the auth service refused. */
export class SignInError extends Error {
  readonly code: string;
  readonly status: number;
  /** Seconds until another try is allowed, after too many wrong passwords. */
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "SignInError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

/**
 * What asking "who is signed in?" can answer.
 *
 * `off` is a page whose stack has no sign-in: the auth service is not
 * there, so there is nobody to ask and nothing to show but the page.
 */
export type SessionAnswer = { kind: "signed-in"; session: Session } | { kind: "signed-out" } | { kind: "off" };

export interface SessionClient {
  session(): Promise<SessionAnswer>;
  meta(): Promise<AuthMeta | null>;
  signIn(username: string, password: string): Promise<Session>;
  signOut(): Promise<void>;
  changePassword(current: string, next: string): Promise<void>;
}

export interface SessionClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
}

/** The event a page's API client raises on a 401, so the gate can ask again. */
export const UNAUTHORIZED_EVENT = "nl2sql:unauthorized";

export function notifyUnauthorized(): void {
  globalThis.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

async function refusal(response: Response): Promise<SignInError> {
  const body: unknown = await response.json().catch(() => null);
  const error = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  const retry = Number(response.headers.get("Retry-After"));
  return new SignInError(
    response.status,
    typeof error?.code === "string" ? error.code : "error",
    typeof error?.message === "string" ? error.message : `HTTP ${response.status}`,
    Number.isFinite(retry) && retry > 0 ? retry : null,
  );
}

export function createSessionClient(options: SessionClientOptions = {}): SessionClient {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);

  async function call(path: string, init: RequestInit = {}): Promise<Response> {
    try {
      return await doFetch(`${base}${path}`, {
        credentials: "same-origin",
        ...init,
        headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
      });
    } catch (cause) {
      throw new SignInError(0, "unreachable", `cannot reach the sign-in service: ${String(cause)}`);
    }
  }

  return {
    async session() {
      let response: Response;
      try {
        response = await call("/auth/session");
      } catch {
        return { kind: "off" };
      }
      if (response.status === 401) return { kind: "signed-out" };
      if (!response.ok) return { kind: "off" };
      return { kind: "signed-in", session: (await response.json()) as Session };
    },

    async meta() {
      try {
        const response = await call("/auth/meta");
        return response.ok ? ((await response.json()) as AuthMeta) : null;
      } catch {
        return null;
      }
    },

    async signIn(username, password) {
      const response = await call("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      if (!response.ok) throw await refusal(response);
      return (await response.json()) as Session;
    },

    async signOut() {
      const response = await call("/auth/logout", { method: "POST" });
      if (!response.ok) throw await refusal(response);
    },

    async changePassword(current, next) {
      const response = await call("/auth/password", {
        method: "POST",
        body: JSON.stringify({ current, new: next }),
      });
      if (!response.ok) throw await refusal(response);
    },
  };
}
