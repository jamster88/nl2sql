/**
 * The history kept in this browser: newest first, one entry per query and
 * mode, capped -- and never the reason the page fails to open.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  HISTORY_KEY,
  HISTORY_LIMIT,
  loadHistory,
  remember,
  saveHistory,
  type HistoryEntry,
} from "../src/api/history";

function entry(sql: string, overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return { sql, mode: "run", at: "2026-09-28T10:00:00.000Z", accepted: true, ...overrides };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("remember", () => {
  it("puts the newest first", () => {
    expect(remember([entry("a")], entry("b")).map((e) => e.sql)).toEqual(["b", "a"]);
  });

  it("keeps one entry per query and mode", () => {
    const list = remember([entry("a"), entry("b"), entry("a", { mode: "plan" })], entry("a"));
    expect(list.map((e) => `${e.mode}:${e.sql}`)).toEqual(["run:a", "run:b", "plan:a"]);
  });

  it("forgets the oldest past the limit", () => {
    let list: HistoryEntry[] = [];
    for (let index = 0; index < HISTORY_LIMIT + 5; index += 1) list = remember(list, entry(`q${index}`));
    expect(list).toHaveLength(HISTORY_LIMIT);
    expect(list[0]!.sql).toBe(`q${HISTORY_LIMIT + 4}`);
  });
});

describe("storage", () => {
  it("round-trips through localStorage", () => {
    saveHistory([entry("a"), entry("b", { accepted: false, mode: "analyze" })]);
    expect(loadHistory()).toEqual([entry("a"), entry("b", { accepted: false, mode: "analyze" })]);
  });

  it("starts empty", () => {
    expect(loadHistory()).toEqual([]);
  });

  it("drops anything that is not an entry", () => {
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([entry("ok"), null, "text", { sql: "no mode" }, entry("bad mode", { mode: "drop" as never }), entry("x", { at: 1 as never }), entry("y", { accepted: "yes" as never })]),
    );
    expect(loadHistory().map((e) => e.sql)).toEqual(["ok"]);
  });

  it("reads a stored value that is not a list as empty", () => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify({ sql: "x" }));
    expect(loadHistory()).toEqual([]);
  });

  it("reads corrupt JSON as empty", () => {
    localStorage.setItem(HISTORY_KEY, "{not json");
    expect(loadHistory()).toEqual([]);
  });

  it("caps what it reads, whatever was stored", () => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(Array.from({ length: 40 }, (_, i) => entry(`q${i}`))));
    expect(loadHistory()).toHaveLength(HISTORY_LIMIT);
  });

  it("works with no storage at all", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(loadHistory()).toEqual([]);
    expect(() => saveHistory([entry("a")])).not.toThrow();
  });

  it("works when the browser refuses storage outright", () => {
    vi.stubGlobal("localStorage", undefined);
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    });
    expect(loadHistory()).toEqual([]);
    expect(() => saveHistory([entry("a")])).not.toThrow();
  });

  it("keeps working when storage is full", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(() => saveHistory([entry("a")])).not.toThrow();
  });
});
