/**
 * The client against a fake `fetch`: the paths it asks for, the headers it
 * sends, and the one error shape every failure is turned into.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError, createClient } from "../src/api/client";
import { jsonResponse, makeMeta, makePrompt, makeResult, makeSchema } from "./helpers";

function fetchReturning(status: number, body: unknown) {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => jsonResponse(status, body));
}

describe("the routes", () => {
  it("asks for the meta document", async () => {
    const fetch = fetchReturning(200, makeMeta());
    const client = createClient({ fetch });

    await expect(client.meta()).resolves.toEqual(makeMeta());
    expect(fetch.mock.calls[0]![0]).toBe("/v1/meta");
  });

  it("asks for the schema", async () => {
    const fetch = fetchReturning(200, makeSchema());
    await expect(createClient({ fetch }).schema()).resolves.toEqual(makeSchema());
    expect(fetch.mock.calls[0]![0]).toBe("/v1/schema");
  });

  it("asks for a table's prompt with the name escaped", async () => {
    const fetch = fetchReturning(200, makePrompt());
    await createClient({ fetch }).prompt("odd name/x");
    expect(fetch.mock.calls[0]![0]).toBe("/v1/schema/odd%20name%2Fx/prompt");
  });

  it("posts a query as JSON, in the body and never the URL", async () => {
    const fetch = fetchReturning(200, makeResult());
    await createClient({ fetch }).query("SELECT 1", "plan");

    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/v1/query");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ sql: "SELECT 1", mode: "plan" });
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("runs a query when no mode is given", async () => {
    const fetch = fetchReturning(200, makeResult());
    await createClient({ fetch }).query("SELECT 1");
    expect(JSON.parse(String(fetch.mock.calls[0]![1]?.body)).mode).toBe("run");
  });

  it("passes an abort signal through", async () => {
    const fetch = fetchReturning(200, makeMeta());
    const controller = new AbortController();
    const client = createClient({ fetch });

    await client.meta(controller.signal);
    await client.schema(controller.signal);
    await client.prompt("dim_store", controller.signal);
    await client.query("SELECT 1", "run", controller.signal);
    for (const call of fetch.mock.calls) expect(call[1]?.signal).toBe(controller.signal);
  });
});

describe("where it sends them", () => {
  it("prefixes a base URL, without doubling its slash", async () => {
    const fetch = fetchReturning(200, makeMeta());
    await createClient({ fetch, baseUrl: "https://console.example/" }).meta();
    expect(fetch.mock.calls[0]![0]).toBe("https://console.example/v1/meta");
  });

  it("adds a bearer token only when it is given one", async () => {
    const fetch = fetchReturning(200, makeMeta());
    await createClient({ fetch, token: "s3cret" }).meta();
    await createClient({ fetch }).meta();

    expect((fetch.mock.calls[0]![1]?.headers as Record<string, string>)["Authorization"]).toBe("Bearer s3cret");
    expect((fetch.mock.calls[1]![1]?.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });

  it("uses the browser's fetch when none is given", async () => {
    const fetch = fetchReturning(200, makeMeta());
    vi.stubGlobal("fetch", fetch);
    try {
      await createClient().meta();
      expect(fetch).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("failures", () => {
  it("turns the error envelope into an ApiError with its code", async () => {
    const fetch = fetchReturning(503, {
      error: { code: "database_unavailable", message: "cannot reach the retail database" },
    });
    const failure = await createClient({ fetch }).meta().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      status: 503,
      code: "database_unavailable",
      message: "cannot reach the retail database",
      name: "ApiError",
    });
  });

  it("names the status when the body is not the envelope", async () => {
    const fetch = fetchReturning(502, { detail: "bad gateway" });
    await expect(createClient({ fetch }).schema()).rejects.toMatchObject({ status: 502, code: "error", message: "HTTP 502" });
  });

  it("does not mistake an error whose code is not a string for the envelope", async () => {
    const fetch = fetchReturning(500, { error: { code: 5 } });
    await expect(createClient({ fetch }).schema()).rejects.toMatchObject({ code: "error" });
  });

  it("does not mistake a null error for the envelope", async () => {
    const fetch = fetchReturning(500, { error: null });
    await expect(createClient({ fetch }).schema()).rejects.toMatchObject({ code: "error" });
  });

  it("survives a body that is not JSON at all", async () => {
    const fetch = vi.fn(async () => new Response("<html>gateway timeout</html>", { status: 504 }));
    await expect(createClient({ fetch }).schema()).rejects.toMatchObject({ status: 504, message: "HTTP 504" });
  });

  it("says the console is unreachable when the request never lands", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(createClient({ fetch }).meta()).rejects.toMatchObject({
      status: 0,
      code: "unreachable",
      message: "cannot reach the console: TypeError: Failed to fetch",
    });
  });

  it("lets an abort through as itself", async () => {
    const abort = new DOMException("aborted", "AbortError");
    const fetch = vi.fn(async () => {
      throw abort;
    });
    await expect(createClient({ fetch }).meta()).rejects.toBe(abort);
  });

  it("does not treat another DOMException as an abort", async () => {
    const fetch = vi.fn(async () => {
      throw new DOMException("nope", "NetworkError");
    });
    await expect(createClient({ fetch }).meta()).rejects.toMatchObject({ code: "unreachable" });
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
