/**
 * The client against a fake `fetch`: each call's method, path and body, and
 * the one error shape every failure becomes.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError, createClient } from "../src/api/client";
import { makeMeta, makePerson } from "./helpers";

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

describe("createClient", () => {
  it("sends each call to its route with its method and body", async () => {
    const fetch = vi.fn().mockImplementation(async (_url: string, init: RequestInit) =>
      init.method === "DELETE" || String(_url).endsWith("/password") || String(_url).endsWith("/unlock")
        ? json(204, undefined)
        : json(200, makePerson()),
    );
    const client = createClient({ fetch, baseUrl: "https://dir.example/" });
    await client.meta();
    await client.people();
    await client.person("a b");
    await client.add({ uid: "zoe" });
    await client.change("zoe", { mail: "z@x" });
    await client.remove("zoe");
    await client.setPassword("zoe", "long-password");
    await client.unlock("zoe");
    await client.groups();
    await client.importFile("people.csv", "uid\nzoe\n");
    await client.sync();
    const calls = fetch.mock.calls.map(([url, init]) => [url, init.method, init.body]);
    expect(calls).toEqual([
      ["https://dir.example/directory/v1/meta", "GET", undefined],
      ["https://dir.example/directory/v1/people", "GET", undefined],
      ["https://dir.example/directory/v1/people/a%20b", "GET", undefined],
      ["https://dir.example/directory/v1/people", "POST", JSON.stringify({ uid: "zoe" })],
      ["https://dir.example/directory/v1/people/zoe", "PATCH", JSON.stringify({ mail: "z@x" })],
      ["https://dir.example/directory/v1/people/zoe", "DELETE", undefined],
      ["https://dir.example/directory/v1/people/zoe/password", "POST", JSON.stringify({ password: "long-password" })],
      ["https://dir.example/directory/v1/people/zoe/unlock", "POST", undefined],
      ["https://dir.example/directory/v1/groups", "GET", undefined],
      ["https://dir.example/directory/v1/import", "POST", JSON.stringify({ filename: "people.csv", content: "uid\nzoe\n" })],
      ["https://dir.example/directory/v1/sync", "POST", undefined],
    ]);
    expect(fetch.mock.calls[3]![1].headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
    expect(fetch.mock.calls[0]![1]).toMatchObject({ credentials: "same-origin", headers: { Accept: "application/json" } });
  });

  it("returns what the service answered", async () => {
    const fetch = vi.fn().mockResolvedValue(json(200, makeMeta()));
    await expect(createClient({ fetch }).meta()).resolves.toEqual(makeMeta());
  });

  it("turns the error envelope, a bare status and an unreachable service into ApiErrors", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(409, { error: { code: "cannot_remove_yourself", message: "ask another administrator" } }))
      .mockResolvedValueOnce(new Response("<html>", { status: 502 }))
      .mockRejectedValueOnce(new TypeError("offline"));
    const client = createClient({ fetch });
    await expect(client.remove("admin")).rejects.toMatchObject({ status: 409, code: "cannot_remove_yourself" });
    await expect(client.people()).rejects.toMatchObject({ status: 502, code: "error", message: "HTTP 502" });
    const unreachable = client.meta();
    await expect(unreachable).rejects.toBeInstanceOf(ApiError);
    await expect(unreachable).rejects.toMatchObject({ status: 0, code: "unreachable" });
  });

  it("raises the unauthorized event on a 401, so the sign-in gate asks again", async () => {
    const heard = vi.fn();
    globalThis.addEventListener("nl2sql:unauthorized", heard);
    const fetch = vi.fn().mockResolvedValue(json(401, { error: { code: "expired", message: "the session has expired" } }));
    await expect(createClient({ fetch }).meta()).rejects.toMatchObject({ status: 401, code: "expired" });
    globalThis.removeEventListener("nl2sql:unauthorized", heard);
    expect(heard).toHaveBeenCalledOnce();
  });

  it("uses the global fetch by default", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, makeMeta()));
    await createClient().meta();
    expect(spy).toHaveBeenCalledWith("/directory/v1/meta", expect.anything());
    spy.mockRestore();
  });
});
