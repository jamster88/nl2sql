import { describe, expect, it, vi } from "vitest";

import { ApiError, createClient } from "../src/api/client";
import { makeJob, makeMeta } from "./helpers";

/** A fetch spy that records what it was called with. */
function spyFetch(response: Response): { fetch: typeof fetch; calls: [string, RequestInit][] } {
  const calls: [string, RequestInit][] = [];
  const fetchImpl = vi.fn(async (input: unknown, init: unknown) => {
    calls.push([String(input), (init ?? {}) as RequestInit]);
    return response.clone();
  });
  return { fetch: fetchImpl as unknown as typeof fetch, calls };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("createClient", () => {
  it("fetches meta from the same origin by default", async () => {
    const meta = makeMeta();
    const { fetch, calls } = spyFetch(json(meta));
    const client = createClient({ fetch });

    await expect(client.meta()).resolves.toEqual(meta);
    expect(calls[0]?.[0]).toBe("/v1/meta");
  });

  it("prefixes a base URL and strips its trailing slash", async () => {
    const { fetch, calls } = spyFetch(json(makeMeta()));
    await createClient({ fetch, baseUrl: "https://api.example.com/" }).meta();
    expect(calls[0]?.[0]).toBe("https://api.example.com/v1/meta");
  });

  it("sends the token as a bearer header when one is configured", async () => {
    const { fetch, calls } = spyFetch(json(makeMeta()));
    await createClient({ fetch, token: "s3cret" }).meta();
    const headers = calls[0]?.[1].headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer s3cret");
  });

  it("omits the header entirely when there is no token", async () => {
    const { fetch, calls } = spyFetch(json(makeMeta()));
    await createClient({ fetch }).meta();
    expect(calls[0]?.[1].headers).not.toHaveProperty("Authorization");
  });

  it("posts a question and passes ?wait through", async () => {
    const job = makeJob({ status: "queued" });
    const { fetch, calls } = spyFetch(json(job, 202));
    const client = createClient({ fetch });

    await expect(client.ask("how many stores?", { wait: 30 })).resolves.toEqual(job);
    const [url, init] = calls[0] ?? ["", {}];
    expect(url).toBe("/v1/questions?wait=30");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ question: "how many stores?" });
  });

  it("includes metadata only when given", async () => {
    const { fetch, calls } = spyFetch(json(makeJob()));
    const client = createClient({ fetch });

    await client.ask("a", { metadata: { turn: "7" } });
    expect(JSON.parse(String(calls[0]?.[1].body))).toEqual({ question: "a", metadata: { turn: "7" } });

    await client.ask("b");
    expect(JSON.parse(String(calls[1]?.[1].body))).toEqual({ question: "b" });
  });

  it("escapes a job id into the path", async () => {
    const { fetch, calls } = spyFetch(json(makeJob()));
    await createClient({ fetch }).job("a/b?c");
    expect(calls[0]?.[0]).toBe("/v1/questions/a%2Fb%3Fc");
  });

  it("lists jobs with a limit", async () => {
    const { fetch, calls } = spyFetch(json({ jobs: [], count: 0 }));
    await createClient({ fetch }).jobs(5);
    expect(calls[0]?.[0]).toBe("/v1/questions?limit=5");
  });

  it("defaults the list limit", async () => {
    const { fetch, calls } = spyFetch(json({ jobs: [], count: 0 }));
    await createClient({ fetch }).jobs();
    expect(calls[0]?.[0]).toBe("/v1/questions?limit=50");
  });

  it("treats 204 from a cancel as success with no body", async () => {
    const { fetch, calls } = spyFetch(new Response(null, { status: 204 }));
    await expect(createClient({ fetch }).cancel("job-1")).resolves.toBeUndefined();
    expect(calls[0]?.[1].method).toBe("DELETE");
  });

  it("waits on a job when asked", async () => {
    const { fetch, calls } = spyFetch(json(makeJob()));
    await createClient({ fetch }).job("job-1", { wait: 120 });
    expect(calls[0]?.[0]).toBe("/v1/questions/job-1?wait=120");
  });

  describe("errors", () => {
    it("unwraps the error envelope into an ApiError", async () => {
      const { fetch } = spyFetch(
        json({ error: { code: "job_running", message: "already running", detail: { id: "x" } } }, 409),
      );
      const error = await createClient({ fetch }).job("job-1").catch((caught: unknown) => caught);

      expect(error).toBeInstanceOf(ApiError);
      const api = error as ApiError;
      expect(api.code).toBe("job_running");
      expect(api.status).toBe(409);
      expect(api.message).toBe("already running");
      expect(api.detail).toEqual({ id: "x" });
      expect(api.retryable).toBe(false);
    });

    it("falls back to a generic code when the body is not an envelope", async () => {
      const { fetch } = spyFetch(new Response("<html>502</html>", { status: 502 }));
      const error = (await createClient({ fetch }).meta().catch((caught: unknown) => caught)) as ApiError;
      expect(error.code).toBe("error");
      expect(error.message).toBe("HTTP 502");
    });

    it("reports a network failure as an unreachable ApiError", async () => {
      const fetchImpl = vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      });
      const error = (await createClient({ fetch: fetchImpl as unknown as typeof fetch })
        .meta()
        .catch((caught: unknown) => caught)) as ApiError;

      expect(error.code).toBe("unreachable");
      expect(error.status).toBe(0);
      expect(error.retryable).toBe(true);
    });

    it("lets an abort through as an abort rather than an ApiError", async () => {
      const fetchImpl = vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      });
      const error = await createClient({ fetch: fetchImpl as unknown as typeof fetch })
        .meta()
        .catch((caught: unknown) => caught);
      expect(error).toBeInstanceOf(DOMException);
    });

    it("returns the readiness document even when it comes back 503", async () => {
      const document = { ready: false, checks: { database: { ok: false, detail: "starting" } }, warnings: [] };
      const { fetch } = spyFetch(json(document, 503));
      await expect(createClient({ fetch }).readiness()).resolves.toEqual(document);
    });

    it("still raises when a 503 readiness has no body at all", async () => {
      const { fetch } = spyFetch(new Response("", { status: 503 }));
      await expect(createClient({ fetch }).readiness()).rejects.toBeInstanceOf(ApiError);
    });

    it("marks a 503 as retryable", () => {
      expect(new ApiError(503, "unavailable", "shutting down").retryable).toBe(true);
    });
  });

  describe("eventsUrl", () => {
    it("uses the link the server supplied", () => {
      const client = createClient({ baseUrl: "https://api.example.com" });
      expect(client.eventsUrl(makeJob({ id: "abc" }))).toBe(
        "https://api.example.com/v1/questions/abc/events",
      );
    });

    it("appends the token as a query parameter, because EventSource cannot set headers", () => {
      const client = createClient({ token: "s3cret" });
      expect(client.eventsUrl(makeJob({ id: "abc" }))).toBe(
        "/v1/questions/abc/events?access_token=s3cret",
      );
    });
  });
});
