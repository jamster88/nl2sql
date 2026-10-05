/**
 * One error for every page's client: what a service refused, and why.
 *
 * Every nl2sql service answers a failure with the same envelope --
 * `{"error": {"code", "message", "detail"?}}` -- so every page throws the same
 * error for it, and a component branches on `code` rather than on wording
 * that may change. Shared since 6.2 (V6-25), where five pages each carried a
 * copy and two of them had grown a `detail` and a `retryable` the other
 * three had not.
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
