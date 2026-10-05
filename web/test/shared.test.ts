import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/errors";
import { PEERS, sharedPackage } from "../src/config";
import { counted, plainText } from "../src/text";
import { devProxies } from "../src/vite";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");

describe("ApiError", () => {
  it("carries the code a component branches on, the status, and any detail", () => {
    const error = new ApiError(409, "already_reviewed", "someone got there first", { by: "rita" });
    expect(error).toBeInstanceOf(Error);
    expect([error.name, error.status, error.code, error.message]).toEqual([
      "ApiError",
      409,
      "already_reviewed",
      "someone got there first",
    ]);
    expect(error.detail).toEqual({ by: "rita" });
    expect(new ApiError(400, "invalid_request", "no").detail).toBeUndefined();
  });

  it("is worth retrying only when the service, or the network, was not there", () => {
    expect(new ApiError(503, "unavailable", "").retryable).toBe(true);
    expect(new ApiError(0, "unreachable", "").retryable).toBe(true);
    expect(new ApiError(401, "sign_in_required", "").retryable).toBe(false);
  });
});

describe("plainText", () => {
  it("undoes exactly the three entities the pipeline writes, ampersand last", () => {
    expect(plainText("Thrift &amp; Table &lt;b&gt;")).toBe("Thrift & Table <b>");
    // Escaped first, so unescaped last: `&amp;lt;` was a literal `&lt;`.
    expect(plainText("&amp;lt;")).toBe("&lt;");
    expect(plainText("&quot;&#39;")).toBe("&quot;&#39;");
  });
});

describe("counted", () => {
  it("agrees the noun with the count, and writes thousands as a person would", () => {
    expect(counted(1, "pair")).toBe("1 pair");
    expect(counted(0, "pair")).toBe("0 pairs");
    expect(counted(1204, "correction")).toBe("1,204 corrections");
    expect(counted(2, "query", "queries")).toBe("2 queries");
  });
});

describe("devProxies", () => {
  function headers(proxy: ReturnType<typeof devProxies>[string]): Record<string, string> {
    const set: Record<string, string> = {};
    proxy.configure?.({
      on: (_event, listener) => listener({ setHeader: (name, value) => void (set[name] = value) }),
    });
    return set;
  }

  it("sends each of the service's paths to it, and sign-in to the auth service", () => {
    const proxies = devProxies({ paths: ["/v1", "/readyz"], target: "https://localhost:8443", auth: "https://localhost:8446", secure: false });
    expect(Object.keys(proxies)).toEqual(["/v1", "/readyz", "/auth"]);
    expect(proxies["/v1"]).toBe(proxies["/readyz"]);
    expect(proxies["/v1"]).toMatchObject({ target: "https://localhost:8443", changeOrigin: true, secure: false });
    expect(proxies["/auth"]).toEqual({ target: "https://localhost:8446", changeOrigin: true, secure: false });
    expect(headers(proxies["/v1"]!)).toEqual({});
  });

  it("adds the token as the bearer, and holds nothing back from a stream", () => {
    const proxies = devProxies({ paths: ["/v1"], target: "https://api", secure: true, token: "t", stream: true });
    expect(proxies["/auth"]).toBeUndefined();
    expect(proxies["/v1"]).toMatchObject({ ws: false, timeout: 0, proxyTimeout: 0, secure: true });
    expect(headers(proxies["/v1"]!)).toEqual({ Authorization: "Bearer t", "Accept-Encoding": "identity" });
  });
});

describe("sharedPackage", () => {
  it("resolves the package here, its peers from the page, and finds its tests and sources", () => {
    const page = resolve(WEB, "..", "review", "gui");
    const shared = sharedPackage(pathToFileURL(resolve(page, "vitest.config.ts")).href);
    expect(shared.resolve.alias["@nl2sql/web"]).toBe(resolve(WEB, "src"));
    expect(shared.resolve.dedupe).toEqual(PEERS);
    expect(shared.resolve.dedupe).toContain("react");
    expect(shared.server.fs.allow).toEqual([page, WEB]);
    expect(shared.tests).toEqual(["../../web/test/**/*.test.{ts,tsx}"]);
    expect(shared.sources).toEqual([`${WEB}/src/**/*.{ts,tsx}`]);
  });

});
