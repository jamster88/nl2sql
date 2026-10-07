/**
 * The HTTP client: every call's method, path and body, and the error envelope.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError, createClient } from "../src/api/client";
import { EMPTY_SNIPPET } from "../src/api/snippet";
import { EMPTY_PAIR } from "../src/components/GoldenPane";

function respond(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function sent(fetch: ReturnType<typeof vi.fn>) {
  const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
  return {
    url,
    method: init.method,
    headers: init.headers as Record<string, string>,
    body: init.body === undefined ? undefined : JSON.parse(String(init.body)),
  };
}

describe("each call", () => {
  const cases: [string, (c: ReturnType<typeof createClient>) => Promise<unknown>, string, string, unknown][] = [
    ["meta", (c) => c.meta(), "GET", "/v1/meta", undefined],
    ["schema", (c) => c.schema(), "GET", "/v1/schema", undefined],
    ["snippets", (c) => c.snippets(), "GET", "/v1/snippets", undefined],
    ["validateSnippet", (c) => c.validateSnippet(EMPTY_SNIPPET), "POST", "/v1/snippets/validate", { draft: EMPTY_SNIPPET }],
    ["previewSnippet new", (c) => c.previewSnippet(EMPTY_SNIPPET), "POST", "/v1/snippets/preview", { draft: EMPTY_SNIPPET, snippet_id: null }],
    ["previewSnippet change", (c) => c.previewSnippet(EMPTY_SNIPPET, "S01"), "POST", "/v1/snippets/preview", { draft: EMPTY_SNIPPET, snippet_id: "S01" }],
    ["addSnippet", (c) => c.addSnippet(EMPTY_SNIPPET), "POST", "/v1/snippets", { draft: EMPTY_SNIPPET }],
    ["changeSnippet", (c) => c.changeSnippet("S 1", EMPTY_SNIPPET), "PUT", "/v1/snippets/S%201", { draft: EMPTY_SNIPPET }],
    ["removeSnippet", (c) => c.removeSnippet("S01"), "DELETE", "/v1/snippets/S01", undefined],
    ["golden", (c) => c.golden(), "GET", "/v1/golden", undefined],
    ["validateGolden", (c) => c.validateGolden("SELECT 1"), "POST", "/v1/golden/validate", { sql: "SELECT 1" }],
    ["previewGolden", (c) => c.previewGolden(EMPTY_PAIR), "POST", "/v1/golden/preview", { draft: EMPTY_PAIR }],
    ["addGolden", (c) => c.addGolden(EMPTY_PAIR), "POST", "/v1/golden", { draft: EMPTY_PAIR }],
    ["removeGolden", (c) => c.removeGolden("Q01"), "DELETE", "/v1/golden/Q01", undefined],
    ["fixes", (c) => c.fixes("completions"), "GET", "/v1/fixes/completions", undefined],
    ["validateFix", (c) => c.validateFix("corrections", "SELECT 2", "SELECT 1"), "POST", "/v1/fixes/corrections/validate", { sql: "SELECT 2", incorrect_sql: "SELECT 1" }],
    ["removeFix", (c) => c.removeFix("corrections", "W0001"), "DELETE", "/v1/fixes/corrections/W0001", undefined],
  ];

  it.each(cases)("%s sends %s to the right path", async (_name, call, method, path, body) => {
    const fetch = respond({ ok: true });
    const result = await call(createClient({ fetch, baseUrl: "https://review.example/" }));
    expect(result).toEqual({ ok: true });
    const request = sent(fetch);
    expect(request.method).toBe(method);
    expect(request.url).toBe(`https://review.example${path}`);
    expect(request.body).toEqual(body);
    expect(request.headers["Content-Type"]).toBe(body === undefined ? undefined : "application/json");
  });

  it("names the curator on a fix, and only when there is one", async () => {
    const body = { question: "q", sql: "SELECT 1", incorrect_sql: "", review_note: "" };
    const named = respond({});
    await createClient({ fetch: named }).addFix("corrections", body, "ann");
    expect(sent(named).headers["X-Reviewer"]).toBe("ann");
    expect(sent(named).url).toBe("/v1/fixes/corrections");
    const anonymous = respond({});
    await createClient({ fetch: anonymous }).addFix("completions", body);
    expect(sent(anonymous).headers["X-Reviewer"]).toBeUndefined();
  });

  it("sends a token when it is given one", async () => {
    const fetch = respond({});
    await createClient({ fetch, token: "secret" }).meta();
    expect(sent(fetch).headers["Authorization"]).toBe("Bearer secret");
    const without = respond({});
    await createClient({ fetch: without }).meta();
    expect(sent(without).headers["Authorization"]).toBeUndefined();
  });

  it("uses the browser's fetch when it is not given one", async () => {
    const fetch = respond({ service: "nl2sql-review" });
    vi.stubGlobal("fetch", fetch);
    try {
      expect(await createClient().meta()).toEqual({ service: "nl2sql-review" });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("failures", () => {
  it("unwraps the error envelope into an ApiError", async () => {
    const fetch = respond({ error: { code: "not_valid", message: "it does not run" } }, 422);
    const failure = await createClient({ fetch }).addSnippet(EMPTY_SNIPPET).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 422, code: "not_valid", message: "it does not run", name: "ApiError" });
  });

  it("names the status when there is no envelope", async () => {
    for (const body of [{ detail: "nope" }, { error: "a string" }, null]) {
      const failure = await createClient({ fetch: respond(body, 502) }).meta().catch((e: unknown) => e);
      expect(failure).toMatchObject({ status: 502, code: "error", message: "HTTP 502" });
    }
    const unparsable = vi.fn().mockResolvedValue(new Response("<html>", { status: 500 }));
    expect(await createClient({ fetch: unparsable }).meta().catch((e: unknown) => e)).toMatchObject({ code: "error" });
  });

  it("says when the service cannot be reached at all", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const failure = await createClient({ fetch }).meta().catch((e: unknown) => e);
    expect(failure).toMatchObject({ status: 0, code: "unreachable" });
    expect((failure as Error).message).toBe("cannot reach the review service: TypeError: Failed to fetch");
  });
});

describe("a session that ends", () => {
  it("raises the unauthorized event on a 401, so the sign-in gate asks again", async () => {
    const heard = vi.fn();
    globalThis.addEventListener("nl2sql:unauthorized", heard);
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "expired", message: "the session has expired" } }), { status: 401 }),
    );
    await expect(createClient({ fetch }).meta()).rejects.toMatchObject({ status: 401, code: "expired" });
    globalThis.removeEventListener("nl2sql:unauthorized", heard);
    expect(heard).toHaveBeenCalledOnce();
  });
});
