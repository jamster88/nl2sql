/**
 * The HTTP client.
 *
 * What is worth pinning down is the envelope and the shape of each request,
 * because every route here is one a curator reaches through a form and a
 * malformed request arrives as an unexplained refusal.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError, createClient } from "../src/api/client";
import { makeDraft, makeGolden, makeMeta, makePreview, makePromotion, makeSubmission } from "./helpers";

function spyFetch(response: Response): { fetch: typeof fetch; calls: [string, RequestInit][] } {
  const calls: [string, RequestInit][] = [];
  const impl = vi.fn(async (input: unknown, init: unknown) => {
    calls.push([String(input), (init ?? {}) as RequestInit]);
    return response.clone();
  });
  return { fetch: impl as unknown as typeof fetch, calls };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("createClient", () => {
  it("reads meta from the same origin by default", async () => {
    const meta = makeMeta();
    const { fetch, calls } = spyFetch(json(meta));
    await expect(createClient({ fetch }).meta()).resolves.toEqual(meta);
    expect(calls[0]?.[0]).toBe("/v1/meta");
  });

  it("honours a base URL", async () => {
    const { fetch, calls } = spyFetch(json(makeMeta()));
    await createClient({ fetch, baseUrl: "https://review.example/" }).meta();
    expect(calls[0]?.[0]).toBe("https://review.example/v1/meta");
  });

  it("carries a token when one is configured", async () => {
    const { fetch, calls } = spyFetch(json(makeMeta()));
    await createClient({ fetch, token: "t0ken" }).meta();
    expect((calls[0]![1].headers as Record<string, string>)["Authorization"]).toBe("Bearer t0ken");
  });

  it("filters the queue by state and verdict", async () => {
    const { fetch, calls } = spyFetch(json({ submissions: [], count: 0, counts: {} }));
    await createClient({ fetch }).submissions({ state: "accepted", verdict: "no", limit: 10, offset: 5 });
    expect(calls[0]?.[0]).toBe("/v1/submissions?state=accepted&verdict=no&limit=10&offset=5");
  });

  it("omits the filters that were not given", async () => {
    const { fetch, calls } = spyFetch(json({ submissions: [], count: 0, counts: {} }));
    await createClient({ fetch }).submissions();
    expect(calls[0]?.[0]).toBe("/v1/submissions");
  });

  it("fetches one submission, escaping its id", async () => {
    const { fetch, calls } = spyFetch(json(makeSubmission()));
    await createClient({ fetch }).submission("a/b");
    expect(calls[0]?.[0]).toBe("/v1/submissions/a%2Fb");
  });

  it("patches a review", async () => {
    const { fetch, calls } = spyFetch(json(makeSubmission()));
    await createClient({ fetch }).review("sub-1", { state: "accepted", reviewer: "me" });

    const [url, init] = calls[0]!;
    expect(url).toBe("/v1/submissions/sub-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ state: "accepted", reviewer: "me" });
  });

  it("previews a draft without writing anything", async () => {
    const { fetch, calls } = spyFetch(json(makePreview()));
    const draft = makeDraft();
    await createClient({ fetch }).preview("sub-1", draft);

    const [url, init] = calls[0]!;
    expect(url).toBe("/v1/submissions/sub-1/preview");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ draft });
  });

  it("promotes, naming the reviewer in a header", async () => {
    const { fetch, calls } = spyFetch(json(makePromotion()));
    await createClient({ fetch }).promote("sub-1", makeDraft(), "curator");

    const [url, init] = calls[0]!;
    expect(url).toBe("/v1/submissions/sub-1/promote");
    expect((init.headers as Record<string, string>)["X-Reviewer"]).toBe("curator");
  });

  it("omits the reviewer header when nobody is named", async () => {
    const { fetch, calls } = spyFetch(json(makePromotion()));
    await createClient({ fetch }).promote("sub-1", makeDraft());
    expect(init(calls)["X-Reviewer"]).toBeUndefined();
  });

  it("reads the golden set and the promotion log", async () => {
    const golden = spyFetch(json(makeGolden()));
    await expect(createClient({ fetch: golden.fetch }).golden()).resolves.toEqual(makeGolden());
    expect(golden.calls[0]?.[0]).toBe("/v1/golden");

    const log = spyFetch(json({ promotions: [], count: 0 }));
    await createClient({ fetch: log.fetch }).promotions(10);
    expect(log.calls[0]?.[0]).toBe("/v1/promotions?limit=10");

    const defaulted = spyFetch(json({ promotions: [], count: 0 }));
    await createClient({ fetch: defaulted.fetch }).promotions();
    expect(defaulted.calls[0]?.[0]).toBe("/v1/promotions?limit=50");
  });

  it("unwraps the error envelope", async () => {
    const { fetch } = spyFetch(
      json({ error: { code: "not_promotable", message: "keywords is empty", detail: { n: 1 } } }, 422),
    );
    const failure = await createClient({ fetch }).promote("sub-1", makeDraft()).catch((e) => e);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.code).toBe("not_promotable");
    expect(failure.status).toBe(422);
    expect(failure.detail).toEqual({ n: 1 });
    expect(failure.retryable).toBe(false);
  });

  it("gives a failure with no envelope the same shape as one", async () => {
    const { fetch } = spyFetch(new Response("<html>502</html>", { status: 502 }));
    const failure = await createClient({ fetch }).meta().catch((e) => e);
    expect(failure.code).toBe("error");
    expect(failure.message).toBe("HTTP 502");
  });

  it("reports an unreachable service as retryable", async () => {
    const failing = vi.fn().mockRejectedValue(new TypeError("network down")) as unknown as typeof fetch;
    const failure = await createClient({ fetch: failing }).meta().catch((e) => e);
    expect(failure.code).toBe("unreachable");
    expect(failure.retryable).toBe(true);
  });

  it("lets an abort through rather than dressing it as a server error", async () => {
    const aborting = vi
      .fn()
      .mockRejectedValue(new DOMException("aborted", "AbortError")) as unknown as typeof fetch;
    await expect(createClient({ fetch: aborting }).meta()).rejects.toThrow("aborted");
  });

  it("returns the readiness document even when it comes with a 503", async () => {
    const body = { ready: false, checks: { staging_database: { ok: false, detail: "down" } }, warnings: [] };
    const { fetch } = spyFetch(json(body, 503));
    await expect(createClient({ fetch }).readiness()).resolves.toEqual(body);
  });

  it("treats 204 as nothing rather than as unparseable JSON", async () => {
    const { fetch } = spyFetch(new Response(null, { status: 204 }));
    await expect(createClient({ fetch }).golden()).resolves.toBeUndefined();
  });

  it("uses the global fetch when none is injected", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue(json(makeMeta())) as unknown as typeof fetch;
    try {
      await expect(createClient().meta()).resolves.toEqual(makeMeta());
    } finally {
      globalThis.fetch = original;
    }
  });
});

function init(calls: [string, RequestInit][]): Record<string, string> {
  return calls[0]![1].headers as Record<string, string>;
}
